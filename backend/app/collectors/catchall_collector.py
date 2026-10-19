"""Catch-all senders (Phase C.2b): one keyed Ariel search per tick, folded to buckets.

A run collects every settled bucket since its watermark with one keyed search over
the catch-all device types (`logsourceid, logsourceidentifier, starttime / bucket`),
splitting it in halves of whole buckets while it is truncated. At one bucket a
truncated result keeps its exact top and is a LOWER_BOUND.

Guarantees:
  * Read-only: an aggregate search over ``events``. Raw sender text lives only in
    the pure fold; what is stored is the bounded top list and the admitted senders.
  * No overlapping runs for one instance: the advisory lock is keyed on
    (instance, collector); a second worker skips.
  * A bucket that was not measured (FAILED, REJECTED, out of budget) is never
    written, so its absence is never a zero. The watermark holds at the last
    contiguous measured bucket, and a later run retries from there.
  * A catch-all source with no rows in a COMPLETE bucket gets a measured zero; in
    a LOWER_BOUND bucket it gets nothing, because its rows may be in the tail.
  * Admission bounds what a hostname can create (`app.catchall.fold`). A refused
    sender is counted in its bucket and never stored by name.
  * Bootstrap without a flood: until a run has measured every bucket it searched,
    every admitted sender is REVIEWED. Afterwards a new one is NEW, and raises a LOW
    inventory alert only when `catchall_new_sender_alerts_enabled` is on.
  * A constant number of statements per run, whatever the number of senders:
    bulk loads, chunked bulk upserts, one retire and one events_24h update.
  * Idempotent: re-collecting a bucket replaces its row; a known sender is
    touched, never duplicated, and raises no second alert.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fingerprint import catchall_sender_fingerprint
from app.alerts.service import AlertInput, AlertService
from app.catchall.fold import CatchallBucketFact, SenderLedger, fold_source_bucket
from app.catchall.keyed_search import CatchallBucketOutcome, run_catchall_search
from app.core.config import Settings, get_settings
from app.ips.keyed_search import IpsSearchBudget
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.enums import (
    FIRST_RUN_LOOKBACK_BUCKETS,
    CatchallMeasurement,
    CatchallReviewStatus,
    IpsCellState,
    SearchFamily,
    Severity,
)
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderCapability, QRadarProvider
from app.providers.dto import CatchallSenderRow
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService
from app.waf.admission import AdmissionPolicy

logger = logging.getLogger("app.collectors.catchall")

COLLECTOR_NAME = "catchall_senders"
#: Set in the watermark metadata by the first run that measured every bucket it searched.
BOOTSTRAPPED_KEY = "bootstrapped"
#: Rows per bulk statement. Bounds the bind parameters (asyncpg allows 32767).
_BUCKET_CHUNK = 1000
_SENDER_CHUNK = 2000


@dataclass
class CatchallCollectionReport:
    instance_id: uuid.UUID
    buckets_attempted: int = 0
    buckets_complete: int = 0
    buckets_lower_bound: int = 0
    #: FAILED or REJECTED: searched, no measurement.
    buckets_failed: int = 0
    #: The tick's search budget ran out before the bucket was searched.
    buckets_not_collected: int = 0
    #: Ariel searches sent, failed ones included.
    searches_sent: int = 0
    #: Times a search was divided in halves because it was truncated.
    splits: int = 0
    bucket_rows_written: int = 0
    senders_admitted: int = 0
    #: Senders created this run (`NEW`, or `REVIEWED` while bootstrapping).
    senders_created: int = 0
    senders_retired: int = 0
    alerts_raised: int = 0
    #: Rows of a log source this instance's inventory does not know (not stored).
    unmapped_rows: int = 0
    bootstrap: bool = False
    skipped_locked: bool = False
    unsupported: bool = False
    partial_failure: bool = False
    error: str | None = None
    duration_ms: int = 0
    watermark_at: datetime | None = None


@dataclass
class _SenderTouch:
    """One tracked sender seen in this run, to upsert once."""

    first_seen_at: datetime
    last_seen_at: datetime


@dataclass
class _RunState:
    ledgers: dict[uuid.UUID, SenderLedger] = field(default_factory=dict)
    touches: dict[tuple[uuid.UUID, str], _SenderTouch] = field(default_factory=dict)
    buckets: list[dict] = field(default_factory=list)


def floor_to_bucket(moment: datetime, bucket_seconds: int) -> datetime:
    epoch = int(moment.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch - epoch % bucket_seconds, tz=UTC)


class CatchallCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def collect(self, instance: QRadarInstance) -> CatchallCollectionReport:
        started = self._clock()
        report = CatchallCollectionReport(instance_id=instance.id)
        settings = self.settings

        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            # Nothing was attempted, so nothing is recorded.
            report.unsupported = True
            report.error = "provider cannot run Ariel searches"
            return report

        async with CollectorAdvisoryLock(
            self.session, settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            bucket_seconds = settings.collection_interval_seconds
            watermark = await self._get_or_create_watermark(instance)
            report.bootstrap = not (watermark.collection_metadata or {}).get(BOOTSTRAPPED_KEY)
            cadence = await SearchCadenceService(self.session, settings, self._clock).get(
                SearchFamily.CATCHALL_SENDERS
            )
            cadence_buckets = max(1, cadence.cadence_seconds // bucket_seconds)
            starts = self._buckets_to_collect(
                watermark,
                floor_to_bucket(
                    started - timedelta(seconds=settings.search_cadence_settle_seconds),
                    bucket_seconds,
                ),
                bucket_seconds,
                first_run_buckets=FIRST_RUN_LOOKBACK_BUCKETS,
                max_buckets=cadence_buckets + settings.collection_max_backfill_intervals,
            )

            report.senders_retired = await self._retire_stale(instance.id, started)
            if starts:
                await self._collect_window(
                    instance, watermark, starts, bucket_seconds, started, report
                )
            watermark.last_run_at = started
            if watermark.watermark_at is not None:
                end_of_last = watermark.watermark_at + timedelta(seconds=bucket_seconds)
                watermark.lag_seconds = max(0, int((started - end_of_last).total_seconds()))
            measured = report.buckets_complete + report.buckets_lower_bound
            watermark.collection_metadata = {
                "bucket_seconds": bucket_seconds,
                "device_type_ids": sorted(set(settings.catchall_type_ids)),
                # Bootstrap ends with the first run that measured everything it searched.
                BOOTSTRAPPED_KEY: not report.bootstrap
                or (report.buckets_attempted > 0 and report.buckets_attempted == measured),
            }
            report.watermark_at = watermark.watermark_at
            await self.session.flush()

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "catch-all collection complete",
            extra={
                "instance_id": str(instance.id),
                "buckets_attempted": report.buckets_attempted,
                "buckets_complete": report.buckets_complete,
                "buckets_lower_bound": report.buckets_lower_bound,
                "buckets_failed": report.buckets_failed,
                "buckets_not_collected": report.buckets_not_collected,
                "senders_admitted": report.senders_admitted,
                "duration_ms": report.duration_ms,
            },
        )
        return report

    # ------------------------------------------------------------------ window
    def _buckets_to_collect(
        self,
        watermark: CollectionWatermark,
        boundary: datetime,
        bucket_seconds: int,
        *,
        first_run_buckets: int,
        max_buckets: int,
    ) -> list[datetime]:
        """Settled bucket starts after the watermark, oldest first, at most `max_buckets`."""
        step = timedelta(seconds=bucket_seconds)
        if watermark.watermark_at is None:
            # First run: a fixed look-back (one hour at five-minute buckets), whatever the cadence.
            first = boundary - step * first_run_buckets
            limit = first_run_buckets
        else:
            last = watermark.watermark_at
            first = (last if last.tzinfo else last.replace(tzinfo=UTC)) + step
            limit = max_buckets
        out: list[datetime] = []
        cursor = first
        while cursor + step <= boundary and len(out) < limit:
            out.append(cursor)
            cursor += step
        return out

    async def _collect_window(
        self,
        instance: QRadarInstance,
        watermark: CollectionWatermark,
        starts: list[datetime],
        bucket_seconds: int,
        now: datetime,
        report: CatchallCollectionReport,
    ) -> None:
        settings = self.settings
        sources = await self._catchall_sources(instance.id)
        by_qradar = {source.qradar_id: source for source in sources}
        state = _RunState()
        known = await self._load_senders(instance.id, state)

        budget = IpsSearchBudget(settings.search_cadence_max_searches_per_tick)
        outcomes = await run_catchall_search(
            self.provider,
            settings.catchall_type_ids,
            (starts[0], starts[-1] + timedelta(seconds=bucket_seconds)),
            bucket_seconds,
            max_rows=settings.catchall_max_rows_per_search,
            budget=budget,
        )
        report.searches_sent = budget.used
        report.splits = (
            len(
                {
                    (o.provenance.search_window_start, o.provenance.search_window_end)
                    for o in outcomes
                    if o.provenance is not None and o.provenance.split_depth > 0
                }
            )
            // 2
        )

        policy = AdmissionPolicy(
            max_new_per_bucket=settings.catchall_max_new_senders_per_bucket,
            max_active=settings.catchall_max_active_senders_per_source,
            min_requests=settings.catchall_min_admission_events,
        )
        for outcome in outcomes:
            report.buckets_attempted += 1
            if outcome.state is IpsCellState.COMPLETE:
                report.buckets_complete += 1
            elif outcome.state is IpsCellState.LOWER_BOUND:
                report.buckets_lower_bound += 1
            elif outcome.state is IpsCellState.NOT_COLLECTED:
                report.buckets_not_collected += 1
            else:
                report.buckets_failed += 1
            if not outcome.measured:
                continue
            self._fold_bucket(outcome, by_qradar, state, policy, bucket_seconds, now, report)

        created = await self._write(instance, state, known, bucket_seconds, now, report)
        if settings.catchall_new_sender_alerts_enabled and created:
            report.alerts_raised = await self._raise_alerts(instance, sources, created, now)
        await self._refresh_events_24h(instance.id, now)
        self._advance_watermark(watermark, outcomes, bucket_seconds, report)

    # -------------------------------------------------------------------- fold
    def _fold_bucket(
        self,
        outcome: CatchallBucketOutcome,
        by_qradar: dict[int, LogSource],
        state: _RunState,
        policy: AdmissionPolicy,
        bucket_seconds: int,
        now: datetime,
        report: CatchallCollectionReport,
    ) -> None:
        by_source: dict[uuid.UUID, list[CatchallSenderRow]] = {}
        for row in outcome.rows:
            source = by_qradar.get(row.qradar_log_source_id)
            if source is None:
                report.unmapped_rows += 1
                continue
            by_source.setdefault(source.id, []).append(row)
        measurement = (
            CatchallMeasurement.LOWER_BOUND
            if outcome.state is IpsCellState.LOWER_BOUND
            else CatchallMeasurement.EXACT
        )
        for source in sorted(by_qradar.values(), key=lambda s: s.qradar_id):
            rows = by_source.get(source.id, [])
            if not rows and measurement is CatchallMeasurement.LOWER_BOUND:
                # Its rows may be in the missing tail: unknown, not zero.
                continue
            ledger = state.ledgers.setdefault(source.id, SenderLedger())
            fact = fold_source_bucket(
                rows,
                ledger,
                measurement=measurement,
                policy=policy,
                top_n=self.settings.catchall_top_senders,
            )
            report.senders_admitted += fact.admitted
            state.buckets.append(
                self._bucket_values(source.id, outcome, fact, bucket_seconds, now)
            )
            for identifier in fact.tracked_events:
                touch = state.touches.get((source.id, identifier))
                if touch is None:
                    state.touches[(source.id, identifier)] = _SenderTouch(
                        outcome.bucket_start, outcome.bucket_start
                    )
                else:
                    touch.last_seen_at = max(touch.last_seen_at, outcome.bucket_start)
                    touch.first_seen_at = min(touch.first_seen_at, outcome.bucket_start)

    @staticmethod
    def _bucket_values(
        source_id: uuid.UUID,
        outcome: CatchallBucketOutcome,
        fact: CatchallBucketFact,
        bucket_seconds: int,
        now: datetime,
    ) -> dict:
        assert outcome.provenance is not None
        return {
            "catchall_log_source_id": source_id,
            "bucket_start": outcome.bucket_start,
            "bucket_seconds": bucket_seconds,
            "events": fact.events,
            "records": fact.records,
            "distinct_senders": fact.distinct_senders,
            "distinct_senders_lower_bound": fact.distinct_senders_lower_bound,
            "measurement": fact.measurement.value,
            "top_senders": [
                {"identifier": identifier, "events": events}
                for identifier, events in fact.top_senders
            ],
            "admitted": fact.admitted,
            "below_admission_threshold": fact.below_admission_threshold,
            "rejected_over_quota": fact.rejected_over_quota,
            "untracked_senders": fact.untracked_senders,
            "untracked_events": fact.untracked_events,
            "query_provenance": outcome.provenance.as_json(),
            "collected_at": now,
        }

    # ------------------------------------------------------------------ writes
    async def _write(
        self,
        instance: QRadarInstance,
        state: _RunState,
        known: dict[tuple[uuid.UUID, str], bool],
        bucket_seconds: int,
        now: datetime,
        report: CatchallCollectionReport,
    ) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        """Bulk-upsert buckets, then senders. Returns the senders created NEW this run."""
        for offset in range(0, len(state.buckets), _BUCKET_CHUNK):
            chunk = state.buckets[offset : offset + _BUCKET_CHUNK]
            stmt = pg_insert(CatchallBucket).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["catchall_log_source_id", "bucket_start"],
                set_={
                    column: stmt.excluded[column]
                    for column in chunk[0]
                    if column not in ("catchall_log_source_id", "bucket_start")
                },
            )
            await self.session.execute(stmt)
        report.bucket_rows_written = len(state.buckets)

        bootstrap = report.bootstrap
        status = CatchallReviewStatus.REVIEWED if bootstrap else CatchallReviewStatus.NEW
        rows = [
            {
                "id": uuid.uuid4(),
                "instance_id": instance.id,
                "catchall_log_source_id": source_id,
                "identifier": identifier,
                "first_seen_at": touch.first_seen_at,
                "last_seen_at": touch.last_seen_at,
                "active": True,
                "retired_at": None,
                "review_status": status.value,
                "reviewed_by": "bootstrap" if bootstrap else None,
                "reviewed_at": now if bootstrap else None,
            }
            for (source_id, identifier), touch in sorted(state.touches.items())
        ]
        created: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        for offset in range(0, len(rows), _SENDER_CHUNK):
            chunk = rows[offset : offset + _SENDER_CHUNK]
            insert = pg_insert(CatchallSender).values(chunk)
            upsert = insert.on_conflict_do_update(
                constraint="uq_catchall_sender_key",
                set_={
                    "first_seen_at": text(
                        "LEAST(catchall_sender.first_seen_at, excluded.first_seen_at)"
                    ),
                    "last_seen_at": text(
                        "GREATEST(catchall_sender.last_seen_at, excluded.last_seen_at)"
                    ),
                    "active": True,
                    "retired_at": None,
                    "updated_at": now,
                },
            )
            returning: Any = upsert.returning(
                CatchallSender.id,
                CatchallSender.catchall_log_source_id,
                CatchallSender.identifier,
                CatchallSender.review_status,
                literal_column("(xmax = 0)").label("inserted"),
            )
            for sender_id, source_id, identifier, review_status, inserted in (
                await self.session.execute(returning)
            ).all():
                if inserted:
                    report.senders_created += 1
                    if review_status == CatchallReviewStatus.NEW.value:
                        created.append((sender_id, source_id, identifier))
        return created

    async def _raise_alerts(
        self,
        instance: QRadarInstance,
        sources: list[LogSource],
        created: list[tuple[uuid.UUID, uuid.UUID, str]],
        now: datetime,
    ) -> int:
        """One LOW inventory alert per new sender, in one `apply_bulk` (D-9: off by default)."""
        by_id = {source.id: source for source in sources}
        inputs = [
            AlertInput(
                fingerprint=catchall_sender_fingerprint(instance.id, source_id, identifier),
                title=f"New sender on {by_id[source_id].name}: {identifier}",
                severity=Severity.LOW,
                source_type="catchall_sender",
                source_id=sender_id,
                description=(
                    "A device that is not a configured log source is sending to a "
                    "catch-all log source and requires SOC review."
                ),
                evidence={
                    "identifier": identifier,
                    "catchall_log_source": by_id[source_id].name,
                    "qradar_log_source_id": by_id[source_id].qradar_id,
                },
                context={
                    "identifier": identifier,
                    "catchall_log_source": by_id[source_id].name,
                    "owner": by_id[source_id].owner,
                    "instance": instance.name,
                },
            )
            for sender_id, source_id, identifier in sorted(created, key=lambda c: (c[1], c[2]))
        ]
        await AlertService(self.session, clock=lambda: now).apply_bulk(inputs, [])
        return len(inputs)

    async def _retire_stale(self, instance_id: uuid.UUID, now: datetime) -> int:
        """Retire senders not seen for `catchall_sender_active_days`, in one statement."""
        cutoff = now - timedelta(days=self.settings.catchall_sender_active_days)
        result = await self.session.execute(
            update(CatchallSender)
            .where(
                CatchallSender.instance_id == instance_id,
                CatchallSender.active.is_(True),
                CatchallSender.last_seen_at < cutoff,
            )
            .values(active=False, retired_at=now, updated_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def _refresh_events_24h(self, instance_id: uuid.UUID, now: datetime) -> None:
        """Recompute `events_24h` for the instance's senders from the stored top lists.

        One statement. A sender no stored top list named in the window gets NULL
        (unknown), never zero.
        """
        await self.session.execute(
            text(
                "WITH agg AS MATERIALIZED ("
                " SELECT b.catchall_log_source_id AS src, e.value ->> 'identifier' AS ident,"
                "  SUM((e.value ->> 'events')::bigint) AS events"
                " FROM catchall_bucket b"
                " JOIN log_source ls ON ls.id = b.catchall_log_source_id"
                " CROSS JOIN LATERAL jsonb_array_elements(b.top_senders) AS e(value)"
                " WHERE ls.instance_id = :instance_id"
                "  AND b.bucket_start >= :since AND b.bucket_start < :until"
                " GROUP BY 1, 2) "
                "UPDATE catchall_sender s SET events_24h = ("
                " SELECT agg.events FROM agg"
                " WHERE agg.src = s.catchall_log_source_id AND agg.ident = s.identifier)"
                " WHERE s.instance_id = :instance_id"
            ).bindparams(
                instance_id=instance_id, since=now - timedelta(hours=24), until=now
            )
        )

    def _advance_watermark(
        self,
        watermark: CollectionWatermark,
        outcomes: list[CatchallBucketOutcome],
        bucket_seconds: int,
        report: CatchallCollectionReport,
    ) -> None:
        """Hold the watermark at the last contiguous measured bucket."""
        advanced = 0
        for outcome in outcomes:
            if not outcome.measured:
                break
            watermark.watermark_at = outcome.bucket_start
            advanced += 1
        watermark.intervals_collected += advanced
        unmeasured = [o for o in outcomes if not o.measured]
        if unmeasured:
            report.partial_failure = True
            states = sorted({o.state.value for o in unmeasured})
            report.error = f"{len(unmeasured)} bucket(s) not measured: {', '.join(states)}"
            watermark.consecutive_failures += 1
            watermark.last_error = report.error
        else:
            watermark.consecutive_failures = 0
            watermark.last_error = None

    # ------------------------------------------------------------------- loads
    async def _catchall_sources(self, instance_id: uuid.UUID) -> list[LogSource]:
        """The inventory's catch-all sources: the only ones a bucket row is written for."""
        rows = await self.session.scalars(
            select(LogSource).where(
                LogSource.instance_id == instance_id,
                LogSource.type_id.in_(sorted(set(self.settings.catchall_type_ids))),
            )
        )
        return list(rows.all())

    async def _load_senders(
        self, instance_id: uuid.UUID, state: _RunState
    ) -> dict[tuple[uuid.UUID, str], bool]:
        """Every known sender of the instance: seeds the ledgers with the active ones."""
        known: dict[tuple[uuid.UUID, str], bool] = {}
        rows = await self.session.execute(
            select(
                CatchallSender.catchall_log_source_id,
                CatchallSender.identifier,
                CatchallSender.active,
            ).where(CatchallSender.instance_id == instance_id)
        )
        for source_id, identifier, active in rows.tuples().all():
            known[(source_id, identifier)] = active
            if active:
                state.ledgers.setdefault(source_id, SenderLedger()).active.add(identifier)
        return known

    async def _get_or_create_watermark(self, instance: QRadarInstance) -> CollectionWatermark:
        watermark = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if watermark is None:
            watermark = CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME)
            self.session.add(watermark)
            await self.session.flush()
        return watermark
