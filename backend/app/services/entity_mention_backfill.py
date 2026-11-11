"""Bounded, resumable backfill of the entity timeline index (SOC-16f4, D-64).

Mentions are a derived index of stored aggregates (AGENTS.md §2.3, §2.8). The live writers
index a parent only when they store it, so parents stored before the index existed (and
history-origin buckets) have none. This service reads those stored parents and folds them
with the same pure folds the live writers call (`entity_mention_folds`); it needs no Ariel
search and reads no raw event.

State is one `entity_mention_backfill` row per (instance, source). The parents and keys are
the design's §1 table (`docs/tasks/SOC-16f.design.md`):

* EXPLANATION: `anomaly_explanation_contributor` rows of `source_ip`, `destination_ip` and
  `username`, with the package's anomaly window; keyset `id`; instance through
  contributor -> package -> anomaly -> log source.
* WAF_TOP: `waf_endpoint_metric` rows whose stored `top_clients` is a JSON array (SQL NULL or
  JSON null is skipped: fail closed); keyset `(waf_endpoint_id, bucket_start)`.
* IPS_TOP: `ips_source_bucket`; keyset `(log_source_id, bucket_start)`.
* IPS_ENTITY: `ips_entity` (DESTINATION_IP) joined to its `ips_entity_metric` buckets;
  keyset `(ips_entity_id, bucket_start)`, the pairs the live writer produces.
* OFFENSE: the newest `offense_snapshot` per offense; keyset `qradar_offense_id`.

LOG_SOURCE is rewritten on every inventory sync, so its row is created COMPLETE. CATCHALL
and PW_VALUE get no row here (SOC-16f7).

Every source reads parents strictly after its keyset position and before the snapshot bound
`until` stored in the cursor when the source starts: later parents belong to the live writers.
A batch is one parent SELECT, one INSERT (chunked past `MENTION_INSERT_CHUNK_SIZE` rows) and
one UPDATE of the state row, committed together; nothing runs per parent. The backfill only
inserts (`ON CONFLICT DO NOTHING`); it never deletes or rewrites a mention.

The whole run holds one global advisory lock (`CollectorAdvisoryLock` on the nil instance
id and collector name `entity_mention_backfill`).

Races with the live writers (D-66). The backfill never deletes, so it must not read a parent
that a live writer replaces afterwards: the old list would survive. Per source:

* WAF_TOP, IPS_TOP, IPS_ENTITY: no lock and no time horizon. The collectors plan only buckets
  after their watermark (`waf_endpoint_collector.py` `_intervals_to_collect`, from
  `watermark_at + interval`; `ips_collector.py` source planning, `start = watermark_at +
  interval`), write the bucket rows and advance the watermark in one session
  (`waf_endpoint_collector.py:474-482`), and no code moves a watermark back (the history
  hand-off, `waf_pipeline.py` `_seed_history_handoff`, sets it forward to the latest boundary;
  the history writers only add HISTORY rows and replace no live row). A bucket the backfill
  can SELECT is committed, hence at or before its watermark, hence never replaced by a live
  writer (`waf_endpoint_collector.py:972-982`, `:1142+`; the paired DELETE in
  `ips_collector.py` `_record_mentions`). Buckets stored after `until` carry live mentions.
  The integration tests `test_live_*_collector_does_not_replace_a_bucket_at_the_watermark`
  pin this.
* EXPLANATION, OFFENSE: the live writer replaces at any time (`explanation_collector.py`
  `_replace_package`, which deletes the package, its contributors and their mentions;
  `offense_collector.py` `_record_mentions`, which deletes every mention of an offense).
  Each batch of these two sources runs inside the collector's own try-lock
  (`EXPLANATION_COLLECTOR`, offense `COLLECTOR_NAME`) from its SELECT to its commit. If the
  lock is held the source does no batch in this run, stays RUNNING with its cursor, and the
  report marks it `busy` (not FAILED).
* CATCHALL: locked too, with the catch-all collector's lock (`catchall_collector.COLLECTOR_NAME`).
  Unlike WAF/IPS it can replace a bucket that is already committed: it writes every measured
  bucket of its window, but `_advance_watermark` (`catchall_collector.py:783-800`) holds the
  watermark at the first unmeasured bucket, so measured buckets after that gap sit beyond the
  watermark and the next run (`_buckets_to_collect`, `:426-450`) collects them again and
  replaces their mentions (`_record_mentions`, `:659-690`). History writes are insert-only
  (`_write_history_buckets`, `:1239+`).
* PW_VALUE: locked with the property-watch pipeline's lock (`pw_pipeline.COLLECTOR_NAME`).
  Live collection stops at the first incomplete bucket (`pw_collector.py` ~1490-1530), so a
  committed value metric is at or before the watch's watermark. But the live DELETE in
  `_record_mentions` (`pw_collector.py:1748-1790`) removes the mentions of every value of the
  watch at a collected `(watch, bucket_start)`, whatever its `watch_version`, so a watch whose
  version was bumped (history restart, `:770-800`) can have old-version rows replaced; the
  lock removes that race rather than relying on the version argument.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NamedTuple

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.catchall_collector import COLLECTOR_NAME as CATCHALL_COLLECTOR
from app.collectors.explanation_collector import EXPLANATION_COLLECTOR
from app.collectors.offense_collector import COLLECTOR_NAME as OFFENSE_COLLECTOR
from app.core.config import Settings, get_settings
from app.models.catchall import CatchallBucket
from app.models.entity import EntityMention, EntityMentionBackfill
from app.models.explanation import AnomalyExplanation, AnomalyExplanationContributor
from app.models.instance import QRadarInstance
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch, PwValue, PwValueMetric
from app.models.waf import WafEndpoint, WafEndpointMetric
from app.services.entity_mention_folds import (
    catchall_mentions,
    explanation_mentions,
    ips_entity_mentions,
    ips_top_mentions,
    offense_mentions,
    pw_value_mentions,
    waf_top_mentions,
)
from app.services.entity_mentions import MAX_ENTITY_VALUE_LENGTH, EntityMentionRow
from app.services.locks import CollectorAdvisoryLock
from app.services.pw_pipeline import COLLECTOR_NAME as PW_COLLECTOR

logger = logging.getLogger(__name__)

LOCK_NAME = "entity_mention_backfill"
#: One lock for the whole run, so the key does not belong to any console.
LOCK_INSTANCE_ID = uuid.UUID(int=0)
#: Sources this service backfills, in processing order.
BACKFILL_SOURCES = (
    "EXPLANATION",
    "WAF_TOP",
    "IPS_TOP",
    "IPS_ENTITY",
    "OFFENSE",
    "CATCHALL",
    "PW_VALUE",
)
#: Sources that need no backfill: their row is created COMPLETE with no rows written.
NO_BACKFILL_SOURCES = ("LOG_SOURCE",)
#: Sources whose live writer replaces a parent's mentions at any time (delete + insert of a
#: whole package / of every mention of an offense); their batches share that collector's lock.
_COLLECTOR_LOCKS = {
    "EXPLANATION": EXPLANATION_COLLECTOR,
    "OFFENSE": OFFENSE_COLLECTOR,
    "CATCHALL": CATCHALL_COLLECTOR,
    "PW_VALUE": PW_COLLECTOR,
}
_EXPLANATION_DIMENSIONS = ("source_ip", "destination_ip", "username")
#: Fixed text of `last_error`; the class name is the only variable part.
FAILED_TEXT = "batch failed and was rolled back; it is retried on the next run"
_INSERT_CHUNK = 1000


@dataclass
class SourceResult:
    status: str
    batches: int = 0
    rows_written: int = 0
    #: The collector's lock was held, so this source did no batch in this run.
    busy: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "batches": self.batches,
            "rows_written": self.rows_written,
            "busy": self.busy,
        }


@dataclass
class BackfillReport:
    busy: bool = False
    skipped_reason: str | None = None
    batches: int = 0
    #: instance name -> source -> result
    instances: dict[str, dict[str, SourceResult]] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return any(
            result.status == "FAILED"
            for sources in self.instances.values()
            for result in sources.values()
        )

    @property
    def complete(self) -> bool:
        return all(
            result.status == "COMPLETE"
            for sources in self.instances.values()
            for result in sources.values()
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "busy": self.busy,
            "skipped_reason": self.skipped_reason,
            "batches": self.batches,
            "instances": {
                name: {source: result.as_dict() for source, result in sources.items()}
                for name, sources in self.instances.items()
            },
        }


@dataclass(frozen=True)
class _Batch:
    parents: int
    mentions: list[EntityMentionRow]
    #: JSON-safe keyset position of the last parent read; None when no parent was read.
    last_key: list[str] | None


@asynccontextmanager
async def _no_lock() -> AsyncIterator[bool]:
    yield True


class _State(NamedTuple):
    """Plain copy of a state row: ORM rows expire at every commit."""

    source: str
    status: str
    cursor: dict[str, Any] | None
    started_at: datetime | None


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class EntityMentionBackfillService:
    """Backfills `entity_mention` for stored parents, a bounded amount per call."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ run

    async def run(
        self,
        *,
        instance_name: str | None = None,
        max_batches: int | None = None,
        unbounded: bool = False,
    ) -> BackfillReport:
        """Process incomplete sources until done or the batch budget is spent.

        `unbounded` (the operator's manual run) ignores the budget unless `max_batches` is
        given; otherwise the budget is `max_batches` or the configured per-run maximum.
        """
        report = BackfillReport()
        budget: int | None
        if max_batches is not None:
            budget = max_batches
        elif unbounded:
            budget = None
        else:
            budget = self.settings.entity_mention_backfill_max_batches_per_run
        async with CollectorAdvisoryLock(
            self.session, self.settings, LOCK_INSTANCE_ID, LOCK_NAME
        ) as acquired:
            if not acquired:
                report.busy = True
                report.skipped_reason = "another entity-mention backfill run holds the lock"
                return report
            instances = await self._instances(instance_name)
            await self._ensure_rows([instance_id for instance_id, _ in instances])
            for instance_id, name in instances:
                states = await self._states(instance_id)
                report.instances[name] = {}
                for source in (*NO_BACKFILL_SOURCES, *BACKFILL_SOURCES):
                    state = states[source]
                    result = SourceResult(status=state.status)
                    report.instances[name][source] = result
                    if state.status == "COMPLETE" or source in NO_BACKFILL_SOURCES:
                        continue
                    if budget is not None and report.batches >= budget:
                        continue
                    await self._run_source(
                        instance_id, name, state, result, report, budget=budget
                    )
        return report

    async def _instances(self, name: str | None) -> list[tuple[uuid.UUID, str]]:
        query = select(QRadarInstance.id, QRadarInstance.name).where(
            QRadarInstance.enabled.is_(True)
        )
        if name is not None:
            query = query.where(QRadarInstance.name == name)
        rows = await self.session.execute(query.order_by(QRadarInstance.name))
        return [(row[0], row[1]) for row in rows.all()]

    async def _ensure_rows(self, instance_ids: Sequence[uuid.UUID]) -> None:
        """Insert every missing (instance, source) row in one statement."""
        if not instance_ids:
            return
        now = self._clock()
        values: list[dict[str, Any]] = []
        for instance_id in instance_ids:
            for source in NO_BACKFILL_SOURCES:
                values.append(
                    {
                        "instance_id": instance_id,
                        "source": source,
                        "status": "COMPLETE",
                        "rows_written": 0,
                        "started_at": now,
                        "completed_at": now,
                        "updated_at": now,
                    }
                )
            for source in BACKFILL_SOURCES:
                values.append(
                    {
                        "instance_id": instance_id,
                        "source": source,
                        "status": "PENDING",
                        "rows_written": 0,
                        "started_at": None,
                        "completed_at": None,
                        "updated_at": now,
                    }
                )
        await self.session.execute(
            pg_insert(EntityMentionBackfill)
            .values(values)
            .on_conflict_do_nothing(index_elements=["instance_id", "source"])
        )
        await self.session.commit()

    async def _states(self, instance_id: uuid.UUID) -> dict[str, _State]:
        rows = await self.session.execute(
            select(
                EntityMentionBackfill.source,
                EntityMentionBackfill.status,
                EntityMentionBackfill.cursor,
                EntityMentionBackfill.started_at,
            ).where(EntityMentionBackfill.instance_id == instance_id)
        )
        return {row[0]: _State(*row) for row in rows.all()}

    # --------------------------------------------------------------- source

    async def _run_source(
        self,
        instance_id: uuid.UUID,
        name: str,
        state: _State,
        result: SourceResult,
        report: BackfillReport,
        *,
        budget: int | None,
    ) -> None:
        source = state.source
        batch_size = self.settings.entity_mention_backfill_batch_size
        cursor = dict(state.cursor or {})
        now = self._clock()
        if "until" not in cursor:
            cursor["until"] = now.isoformat()
        # PENDING -> RUNNING, and FAILED -> RUNNING with the same cursor (a retry).
        await self.session.execute(
            update(EntityMentionBackfill)
            .where(
                EntityMentionBackfill.instance_id == instance_id,
                EntityMentionBackfill.source == source,
            )
            .values(
                status="RUNNING",
                cursor=cursor,
                started_at=state.started_at or now,
                last_error=None,
                updated_at=now,
            )
        )
        await self.session.commit()
        result.status = "RUNNING"
        until = _ts(cursor["until"])
        after: list[str] | None = cursor.get("after")
        while budget is None or report.batches < budget:
            report.batches += 1
            result.batches += 1
            try:
                async with self._collector_lock(instance_id, source) as acquired:
                    if not acquired:
                        # The collector is writing: no batch now, the source keeps its
                        # cursor and stays RUNNING (not a failure).
                        report.batches -= 1
                        result.batches -= 1
                        result.busy = True
                        return
                    complete, inserted, after = await self._batch(
                        instance_id, source, until, after, batch_size, cursor
                    )
            except Exception as exc:
                await self.session.rollback()
                await self._fail(instance_id, name, source, exc)
                result.status = "FAILED"
                return
            result.rows_written += inserted
            if complete:
                result.status = "COMPLETE"
                return

    def _collector_lock(
        self, instance_id: uuid.UUID, source: str
    ) -> AbstractAsyncContextManager[bool]:
        """The collector's own try-lock for sources whose live writer replaces a parent.

        Held for exactly one batch (SELECT to commit), so the collector waits one batch at
        most and is never blocked across batches. Other sources take no lock (see the module
        docstring for why).
        """
        name = _COLLECTOR_LOCKS.get(source)
        if name is None:
            return _no_lock()
        return CollectorAdvisoryLock(self.session, self.settings, instance_id, name)

    async def _batch(
        self,
        instance_id: uuid.UUID,
        source: str,
        until: datetime,
        after: list[str] | None,
        batch_size: int,
        cursor: dict[str, Any],
    ) -> tuple[bool, int, list[str] | None]:
        """One batch: one parent SELECT, one INSERT, one UPDATE, one commit."""
        batch = await self._fetch(instance_id, source, until, after, batch_size)
        inserted = await _insert_mentions(self.session, batch.mentions)
        last = batch.last_key if batch.last_key is not None else after
        complete = batch.parents < batch_size
        now = self._clock()
        new_cursor: dict[str, Any] = {"until": cursor["until"]}
        if last is not None:
            new_cursor["after"] = last
        values: dict[str, Any] = {
            "cursor": new_cursor,
            "rows_written": EntityMentionBackfill.rows_written + inserted,
            "updated_at": now,
        }
        if complete:
            values.update(status="COMPLETE", completed_at=now)
        await self.session.execute(
            update(EntityMentionBackfill)
            .where(
                EntityMentionBackfill.instance_id == instance_id,
                EntityMentionBackfill.source == source,
            )
            .values(**values)
        )
        await self.session.commit()
        return complete, inserted, last

    async def _fail(
        self, instance_id: uuid.UUID, name: str, source: str, exc: Exception
    ) -> None:
        """Mark the row FAILED with a short reason; never a value, secret or event."""
        logger.warning(
            "entity mention backfill batch failed",
            extra={
                "instance": str(instance_id),
                "source": source,
                "error_class": type(exc).__name__,
            },
        )
        await self.session.execute(
            update(EntityMentionBackfill)
            .where(
                EntityMentionBackfill.instance_id == instance_id,
                EntityMentionBackfill.source == source,
            )
            .values(
                status="FAILED",
                last_error=f"{type(exc).__name__}: {FAILED_TEXT}",
                updated_at=self._clock(),
            )
        )
        await self.session.commit()

    # --------------------------------------------------------------- fetchers

    async def _fetch(
        self,
        instance_id: uuid.UUID,
        source: str,
        until: datetime,
        after: list[str] | None,
        limit: int,
    ) -> _Batch:
        if source == "EXPLANATION":
            return await self._explanation(instance_id, until, after, limit)
        if source == "WAF_TOP":
            return await self._waf_top(instance_id, until, after, limit)
        if source == "IPS_TOP":
            return await self._ips_top(instance_id, until, after, limit)
        if source == "IPS_ENTITY":
            return await self._ips_entity(instance_id, until, after, limit)
        if source == "OFFENSE":
            return await self._offense(instance_id, until, after, limit)
        if source == "CATCHALL":
            return await self._catchall(instance_id, until, after, limit)
        if source == "PW_VALUE":
            return await self._pw_value(instance_id, until, after, limit)
        raise ValueError(f"no backfill for source {source}")

    async def _explanation(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        contributor = AnomalyExplanationContributor
        query = (
            select(
                contributor.id,
                contributor.dimension,
                contributor.value,
                AnomalyExplanation.anomaly_window_start,
                AnomalyExplanation.anomaly_window_end,
            )
            .join(AnomalyExplanation, AnomalyExplanation.id == contributor.explanation_id)
            .join(LogSourceAnomaly, LogSourceAnomaly.id == AnomalyExplanation.anomaly_id)
            .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
            .where(
                LogSource.instance_id == instance_id,
                contributor.dimension.in_(_EXPLANATION_DIMENSIONS),
                contributor.created_at < until,
            )
            .order_by(contributor.id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(contributor.id > uuid.UUID(after[0]))
        rows = (await self.session.execute(query)).all()
        mentions: list[EntityMentionRow] = []
        for cid, dimension, value, start, end in rows:
            mentions.extend(
                explanation_mentions(
                    instance_id,
                    [{"id": cid, "dimension": dimension, "value": value}],
                    anomaly_window_start=start,
                    anomaly_window_end=end,
                )
            )
        return _Batch(len(rows), mentions, [str(rows[-1][0])] if rows else None)

    async def _waf_top(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        metric = WafEndpointMetric
        query = (
            select(metric.waf_endpoint_id, metric.bucket_start, metric.top_clients)
            .join(WafEndpoint, WafEndpoint.id == metric.waf_endpoint_id)
            .where(
                WafEndpoint.instance_id == instance_id,
                metric.bucket_start < until,
                # A list only: SQL NULL and JSON null (volume-only history) are skipped.
                func.jsonb_typeof(metric.top_clients) == "array",
            )
            .order_by(metric.waf_endpoint_id, metric.bucket_start)
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(metric.waf_endpoint_id, metric.bucket_start)
                > tuple_(uuid.UUID(after[0]), _ts(after[1]))
            )
        rows = (await self.session.execute(query)).all()
        mentions: list[EntityMentionRow] = []
        for endpoint_id, bucket_start, top_clients in rows:
            mentions.extend(
                waf_top_mentions(
                    instance_id,
                    waf_endpoint_id=endpoint_id,
                    bucket_start=bucket_start,
                    top_clients=top_clients,
                )
            )
        last = [str(rows[-1][0]), rows[-1][1].isoformat()] if rows else None
        return _Batch(len(rows), mentions, last)

    async def _ips_top(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        bucket = IpsSourceBucket
        query = (
            select(bucket.log_source_id, bucket.bucket_start, bucket.contributors)
            .join(LogSource, LogSource.id == bucket.log_source_id)
            .where(LogSource.instance_id == instance_id, bucket.bucket_start < until)
            .order_by(bucket.log_source_id, bucket.bucket_start)
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(bucket.log_source_id, bucket.bucket_start)
                > tuple_(uuid.UUID(after[0]), _ts(after[1]))
            )
        rows = (await self.session.execute(query)).all()
        mentions: list[EntityMentionRow] = []
        for log_source_id, bucket_start, contributors in rows:
            mentions.extend(
                ips_top_mentions(
                    instance_id,
                    log_source_id=log_source_id,
                    bucket_start=bucket_start,
                    contributors=contributors or {},
                )
            )
        last = [str(rows[-1][0]), rows[-1][1].isoformat()] if rows else None
        return _Batch(len(rows), mentions, last)

    async def _ips_entity(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        metric = IpsEntityMetric
        query = (
            select(metric.ips_entity_id, metric.bucket_start, IpsEntity.value_key)
            .join(IpsEntity, IpsEntity.id == metric.ips_entity_id)
            .where(
                IpsEntity.instance_id == instance_id,
                IpsEntity.dimension == "DESTINATION_IP",
                metric.bucket_start < until,
            )
            .order_by(metric.ips_entity_id, metric.bucket_start)
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(metric.ips_entity_id, metric.bucket_start)
                > tuple_(uuid.UUID(after[0]), _ts(after[1]))
            )
        rows = (await self.session.execute(query)).all()
        mentions: list[EntityMentionRow] = []
        for entity_id, bucket_start, value_key in rows:
            mentions.extend(
                ips_entity_mentions(
                    instance_id,
                    ips_entity_id=entity_id,
                    bucket_start=bucket_start,
                    value_key=value_key,
                )
            )
        last = [str(rows[-1][0]), rows[-1][1].isoformat()] if rows else None
        return _Batch(len(rows), mentions, last)

    async def _offense(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        snap = OffenseSnapshot
        query = (
            select(snap)
            .where(snap.instance_id == instance_id, snap.captured_at < until)
            .ext(distinct_on(snap.qradar_offense_id))
            .order_by(snap.qradar_offense_id, snap.captured_at.desc())
            .limit(limit)
        )
        if after is not None:
            query = query.where(snap.qradar_offense_id > int(after[0]))
        rows = (await self.session.scalars(query)).all()
        mentions: list[EntityMentionRow] = []
        for row in rows:
            mentions.extend(
                offense_mentions(
                    instance_id,
                    qradar_offense_id=row.qradar_offense_id,
                    captured_at=row.captured_at,
                    usernames=list(row.usernames or ()),
                    offense_source=row.offense_source,
                    offense_type=row.offense_type,
                    start_time=row.start_time,
                    last_updated_time=row.last_updated_time,
                    close_time=row.close_time,
                )
            )
        last = [str(rows[-1].qradar_offense_id)] if rows else None
        return _Batch(len(rows), mentions, last)

    async def _catchall(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        bucket = CatchallBucket
        query = (
            select(
                bucket.catchall_log_source_id,
                bucket.bucket_start,
                bucket.bucket_seconds,
                bucket.top_senders,
            )
            .join(LogSource, LogSource.id == bucket.catchall_log_source_id)
            .where(
                LogSource.instance_id == instance_id,
                bucket.bucket_start < until,
                # An empty stored list folds to nothing; do not page through it.
                func.jsonb_array_length(bucket.top_senders) > 0,
            )
            .order_by(bucket.catchall_log_source_id, bucket.bucket_start)
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(bucket.catchall_log_source_id, bucket.bucket_start)
                > tuple_(uuid.UUID(after[0]), _ts(after[1]))
            )
        rows = (await self.session.execute(query)).all()
        mentions = catchall_mentions(
            instance_id,
            [
                {
                    "catchall_log_source_id": source_id,
                    "bucket_start": start,
                    "bucket_seconds": seconds,
                    "top_senders": senders,
                }
                for source_id, start, seconds, senders in rows
            ],
        )
        last = [str(rows[-1][0]), rows[-1][1].isoformat()] if rows else None
        return _Batch(len(rows), mentions, last)

    async def _pw_value(
        self, instance_id: uuid.UUID, until: datetime, after: list[str] | None, limit: int
    ) -> _Batch:
        metric = PwValueMetric
        name = func.lower(func.btrim(PropertyWatch.properties[0]["name"].as_string()))
        query = (
            select(
                metric.pw_value_id,
                metric.bucket_start,
                metric.bucket_seconds,
                PwValue.value_key,
                PwValue.truncated,
                PropertyWatch.properties,
            )
            .join(PwValue, PwValue.id == metric.pw_value_id)
            .join(PropertyWatch, PropertyWatch.id == PwValue.watch_id)
            .where(
                PropertyWatch.instance_id == instance_id,
                # Single-property watches of a classifiable property, untruncated values:
                # the rest folds to nothing, so it is not paged through.
                func.jsonb_array_length(PropertyWatch.properties) == 1,
                name.in_(("username", "sourceip", "destinationip")),
                PwValue.truncated.is_(False),
                metric.bucket_start < until,
            )
            .order_by(metric.pw_value_id, metric.bucket_start)
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(metric.pw_value_id, metric.bucket_start)
                > tuple_(uuid.UUID(after[0]), _ts(after[1]))
            )
        rows = (await self.session.execute(query)).all()
        mentions: list[EntityMentionRow] = []
        for value_id, bucket_start, seconds, value_key, truncated, properties in rows:
            mentions.extend(
                pw_value_mentions(
                    instance_id,
                    properties,
                    pw_value_id=value_id,
                    value_key=value_key,
                    truncated=truncated,
                    bucket_start=bucket_start,
                    bucket_seconds=seconds,
                )
            )
        last = [str(rows[-1][0]), rows[-1][1].isoformat()] if rows else None
        return _Batch(len(rows), mentions, last)


async def _insert_mentions(session: AsyncSession, mentions: Iterable[EntityMentionRow]) -> int:
    """Insert-only (`ON CONFLICT DO NOTHING`); returns the rows actually inserted.

    `record_mentions` returns nothing, and a re-run must count 0 written rows, so the
    backfill reads the INSERT's rowcount itself. Same table, same conflict constraint.
    """
    rows = [
        {
            "instance_id": mention.instance_id,
            "kind": mention.kind,
            "value_norm": mention.value_norm,
            "role": mention.role,
            "source": mention.source,
            "ref_table": mention.ref_table,
            "ref_id": mention.ref_id,
            "occurred_at": mention.occurred_at,
            "occurred_end": mention.occurred_end,
        }
        for mention in mentions
        if mention.value_norm and len(mention.value_norm) <= MAX_ENTITY_VALUE_LENGTH
    ]
    inserted = 0
    for offset in range(0, len(rows), _INSERT_CHUNK):
        result = await session.execute(
            pg_insert(EntityMention)
            .values(rows[offset : offset + _INSERT_CHUNK])
            .on_conflict_do_nothing(constraint="pk_entity_mention")
        )
        inserted += int(getattr(result, "rowcount", 0) or 0)
    return inserted
