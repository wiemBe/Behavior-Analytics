"""Offense collection.

Guarantees, and why each one is needed:

  * **Incremental** — collection resumes from a per-instance watermark keyed on
    QRadar's own `last_persisted_time`, so a run costs a page or two rather than
    a full offense crawl.
  * **Bounded** — the first run and any gap-fill reach back at most
    `offense_max_backfill_hours`, and at most `offense_max_pages` pages are
    walked per run. A fresh install must never try to ingest years of history.
  * **A cut-off read does not skip** — when the provider stops at the page
    ceiling, every offense read is stored, and the watermark moves only to the
    newest `last_persisted_time` strictly below the last row. Rows on that
    boundary are read again next run (an unchanged snapshot is already
    idempotent). The run is partial. It is not a provider failure, so
    `consecutive_failures` is left as it is. If every row shares that one
    timestamp, the watermark does not move. That stall is recorded on the
    watermark row's `collection_metadata` (`watermark_stalled`), which is the
    field a collector already uses for evidence about the row. A clean run,
    and a truncated run that can still move, clear it. A provider failure
    clears it too: the current outcome is the failure, not a confirmed stall.
    The watermark is never moved onto the boundary.
  * **No overlapping runs** — a PostgreSQL advisory lock keyed on (instance,
    collector) is held for the duration; a second worker skips rather than
    queues. Two collectors writing the same offense concurrently would race on
    the snapshot key and double-count the watermark.
  * **Idempotent** — snapshots are upserted on their natural key, so replaying an
    interval overwrites rather than duplicates.
  * **Change-driven** — a snapshot row is written only when the meaningful fields
    change, detected via a content hash. Polling a stable offense every five
    minutes would otherwise write 288 identical rows a day and make the history
    view useless.
  * **History preserving** — a changed offense appends a new snapshot; it never
    overwrites the previous one. Offense aging and assignment latency are
    questions about history.
  * **UTC everywhere** — QRadar epoch milliseconds are converted at the provider
    boundary and never re-interpreted here.

Explicitly NOT done: no offense is closed, assigned or otherwise mutated, and
raw contributing events are never fetched. Both are out of scope for Phase 3 and
the provider interface has no method for either.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import String, any_, bindparam, delete, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.entity import EntityMention
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import OffenseDTO
from app.security.sanitizer import sanitize_text
from app.services.entity_mentions import (
    EntityMentionRow,
    EntityRole,
    classify_entity,
    normalize_entity,
    record_mentions,
    user_match_forms,
)
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.collectors.offense")

COLLECTOR_NAME = "offense_snapshot"

#: QRadar offense types whose `offense_source` is a destination address (1 destination IP,
#: 11 destination IPv6). Every other type names an IP as a source address.
_DESTINATION_OFFENSE_TYPES = (1, 11)

#: Fields whose change makes an offense meaningfully different. Deliberately
#: excludes capture time and any counter QRadar bumps continuously without an
#: analyst-visible state change.
_HASHED_FIELDS = (
    "description",
    "status",
    "magnitude",
    "severity",
    "credibility",
    "relevance",
    "assigned_to",
    "offense_type",
    "offense_source",
    "source_network",
    "event_count",
    "flow_count",
    "device_count",
    "source_count",
    "destination_count",
    "close_time",
    "closing_reason_id",
    "categories",
    "source_addresses",
    "local_destination_addresses",
    "usernames",
    "log_source_ids",
    "rule_ids",
)


@dataclass
class OffenseCollectionReport:
    instance_id: uuid.UUID
    offenses_seen: int = 0
    snapshots_written: int = 0
    unchanged: int = 0
    skipped_locked: bool = False
    partial_failure: bool = False
    #: The provider stopped at the page ceiling. Rows past it were not read.
    truncated: bool = False
    #: A truncated page had no timestamp strictly below its last row, so the
    #: watermark was held. The next run reads the same rows again.
    watermark_stalled: bool = False
    error: str | None = None
    watermark_at: datetime | None = None
    lag_seconds: int | None = None
    duration_ms: int = 0
    dropped: list[int] = field(default_factory=list)


def offense_content_hash(dto: OffenseDTO) -> str:
    """Stable hash over the meaningful fields of an offense.

    JSON with sorted keys so the digest does not depend on field ordering, and
    datetimes as epoch seconds so a re-parse cannot perturb it.
    """
    payload: dict[str, object] = {}
    for name in _HASHED_FIELDS:
        value = getattr(dto, name, None)
        if isinstance(value, datetime):
            value = int(value.timestamp())
        payload[name] = value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def boundary_watermark(offenses: Sequence[OffenseDTO]) -> datetime | None:
    """Newest filter timestamp strictly below the last row, or None to hold.

    `list_offenses` returns `last_persisted_time` ascending, and that value is
    what `OffenseDTO.last_updated_time` carries (QRadar's last-event time is a
    different field and is not the filter). The last row is therefore the
    newest persisted time on the page, and also the boundary: another offense
    with that same timestamp may be sitting on the next page, so the watermark
    stops short of it and those rows are read again. When every returned row
    shares the boundary timestamp there is no safe place to move.
    """
    if not offenses:
        return None
    boundary = offenses[-1].last_updated_time
    if boundary is None:
        return None
    below = [
        dto.last_updated_time
        for dto in offenses
        if dto.last_updated_time is not None and dto.last_updated_time < boundary
    ]
    if not below:
        return None
    return max(below)


#: `collection_metadata` key. Doctor reads it. Not a column: the row already
#: carries collector-specific evidence here, and a stall is not `last_error`.
WATERMARK_STALLED_KEY = "watermark_stalled"


def _record_watermark_stall(watermark: CollectionWatermark, *, stalled: bool) -> None:
    """Record or clear a page-ceiling stall. No change when the row already matches.

    `last_error` and `consecutive_failures` are not touched. A stall is not a
    provider failure, and clearing those here would hide one. The flag is the
    signal doctor warns on.
    """
    metadata = dict(watermark.collection_metadata or {})
    if stalled:
        if metadata.get(WATERMARK_STALLED_KEY) is True:
            return
        metadata[WATERMARK_STALLED_KEY] = True
    elif WATERMARK_STALLED_KEY not in metadata:
        return
    else:
        del metadata[WATERMARK_STALLED_KEY]
    watermark.collection_metadata = metadata


def _offense_mentions(
    instance: QRadarInstance, dto: OffenseDTO, *, captured_at: datetime
) -> list[EntityMentionRow]:
    """The mentions of one stored offense snapshot, from the values as stored.

    Usernames are USER, once each, under the bare account. `offense_source` is
    recorded only when the shared helper classifies it (an IP address); other text is not
    guessed into HOST or USER. The address-id lists (`source_addresses`,
    `local_destination_addresses`) are QRadar ids, never addresses.
    """
    occurred_at = dto.start_time or dto.last_updated_time or captured_at
    occurred_end = dto.close_time or dto.last_updated_time
    if occurred_end is not None and occurred_end <= occurred_at:
        occurred_end = None
    ref_id = str(dto.qradar_id)

    def row(kind: Literal["IP", "USER"], value: str, role: EntityRole) -> EntityMentionRow | None:
        value_norm = normalize_entity(kind, value)
        if value_norm is None:
            return None
        return EntityMentionRow(
            instance_id=instance.id,
            kind=kind,
            value_norm=value_norm,
            role=role,
            source="OFFENSE",
            ref_table="offense_snapshot",
            ref_id=ref_id,
            occurred_at=occurred_at,
            occurred_end=occurred_end,
        )

    # One mention per username, under its bare account (the form every query spelling
    # binds); the normalised value itself when no bare form can be derived.
    rows = [
        row("USER", form, "USER")
        for name in dto.usernames
        for form in user_match_forms(sanitize_text(name) or "")[-1:]
    ]
    source = sanitize_text(dto.offense_source)
    if source and classify_entity(source) == "IP":
        destination = dto.offense_type in _DESTINATION_OFFENSE_TYPES
        rows.append(row("IP", source, "DESTINATION_IP" if destination else "SOURCE_IP"))
    return [item for item in rows if item is not None]


class OffenseCollector:
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

    async def collect(self, instance: QRadarInstance) -> OffenseCollectionReport:
        started = self._clock()
        report = OffenseCollectionReport(instance_id=instance.id)
        # Set on the path that stored rows. Early returns leave it false and
        # never reach the completion log.
        dropped = False

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                # Another worker owns this instance. Skipping is correct: the
                # next tick will pick up whatever this run would have.
                report.skipped_locked = True
                logger.info(
                    "offense collection skipped; another worker holds the lock",
                    extra={"instance_id": str(instance.id)},
                )
                return report

            watermark = await self._get_or_create_watermark(instance)
            since = self._resume_point(watermark, started)

            try:
                listed = await self.provider.list_offenses(
                    open_only=False,
                    updated_since=since,
                    max_pages=self.settings.offense_max_pages,
                )
            except ProviderError as exc:
                # Record the failure against the watermark and leave it
                # unadvanced, so the next run retries the same span rather than
                # skipping over offenses we never saw.
                watermark.consecutive_failures += 1
                watermark.last_run_at = started
                watermark.last_error = str(exc)[:512]
                # This run did not confirm a stall. Leaving the flag set would
                # make doctor warn about the page ceiling while the failure
                # is the current outcome.
                _record_watermark_stall(watermark, stalled=False)
                await self.session.flush()
                report.partial_failure = True
                report.error = str(exc)[:512]
                logger.warning(
                    "offense collection failed",
                    extra={"instance_id": str(instance.id), "error_class": type(exc).__name__},
                )
                return report

            offenses = listed.rows
            truncated = listed.truncated
            report.offenses_seen = len(offenses)
            report.truncated = truncated
            # A complete empty result proves that collection reached this run's
            # instant. Keeping the backfill floor would make an empty console
            # look permanently stale. A partial or truncated run does not use
            # this value.
            high_water = started if not offenses else since
            #: offense id -> the mentions of the snapshot this run stored for it.
            stored_mentions: dict[int, list[EntityMentionRow]] = {}

            for dto in offenses:
                try:
                    written = await self._store(instance, dto, captured_at=started)
                except (ValueError, TypeError):
                    # One malformed offense must not abort the batch; record it
                    # and keep going, but mark the run partial so the operator
                    # knows this cycle was incomplete.
                    report.partial_failure = True
                    report.dropped.append(dto.qradar_id)
                    logger.warning(
                        "dropping offense that could not be stored",
                        extra={"qradar_offense_id": dto.qradar_id},
                    )
                    continue

                if written:
                    report.snapshots_written += 1
                    stored_mentions[dto.qradar_id] = _offense_mentions(
                        instance, dto, captured_at=started
                    )
                else:
                    report.unchanged += 1

                if dto.last_updated_time is not None and (
                    high_water is None or dto.last_updated_time > high_water
                ):
                    high_water = dto.last_updated_time

            await self._record_mentions(instance, stored_mentions)

            # A row that could not be stored holds the watermark outright:
            # moving it would skip that offense. A truncated read is not that
            # case. It stores every row it has, then stops strictly before the
            # last row's timestamp so the boundary is read again. Neither one
            # is a provider failure, so consecutive_failures is left alone —
            # not incremented, and not cleared the way a clean run clears it.
            dropped = report.partial_failure
            watermark.last_run_at = started
            watermark.intervals_collected += 1
            if not dropped and truncated:
                report.partial_failure = True
                advanced = boundary_watermark(offenses)
                if advanced is None:
                    report.watermark_stalled = True
                    _record_watermark_stall(watermark, stalled=True)
                else:
                    watermark.watermark_at = advanced
                    watermark.lag_seconds = max(
                        0, int((started - advanced).total_seconds())
                    )
                    # The boundary moved, so this read is not stalled. The
                    # flag is cleared here; a later clean run is not required.
                    _record_watermark_stall(watermark, stalled=False)
            elif not dropped:
                if high_water is not None:
                    watermark.watermark_at = high_water
                    watermark.lag_seconds = max(
                        0, int((started - high_water).total_seconds())
                    )
                watermark.consecutive_failures = 0
                watermark.last_error = None
                _record_watermark_stall(watermark, stalled=False)
            await self.session.flush()

            report.watermark_at = watermark.watermark_at
            report.lag_seconds = watermark.lag_seconds

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        extra = {
            "instance_id": str(instance.id),
            "offenses_seen": report.offenses_seen,
            "snapshots_written": report.snapshots_written,
            "unchanged": report.unchanged,
            "duration_ms": report.duration_ms,
            "lag_seconds": report.lag_seconds,
            "partial_failure": report.partial_failure,
            "truncated": report.truncated,
            "watermark_stalled": report.watermark_stalled,
        }
        # The text is ours. A truncated read has no provider error to copy,
        # and copying one would put upstream prose into the log.
        if report.watermark_stalled and report.offenses_seen == 0:
            logger.warning(
                "offense collection partial; a truncated read returned no offense "
                "to advance from, so the watermark did not move",
                extra=extra,
            )
        elif report.watermark_stalled:
            logger.warning(
                "offense collection partial; every returned offense shares one "
                "timestamp, so the watermark did not move",
                extra=extra,
            )
        elif report.truncated and not dropped:
            logger.warning(
                "offense collection partial; watermark stopped before the last "
                "offense's timestamp",
                extra=extra,
            )
        elif report.partial_failure:
            logger.warning("offense collection partial", extra=extra)
        else:
            logger.info("offense collection complete", extra=extra)
        return report

    async def _record_mentions(
        self,
        instance: QRadarInstance,
        stored: dict[int, list[EntityMentionRow]],
    ) -> None:
        """Index the usernames and the `offense_source` this run stored as entity mentions.

        The parent key is the offense: `ref_table='offense_snapshot'`,
        `ref_id=str(qradar_offense_id)` (the read side hydrates the newest snapshot of that
        offense), `occurred_at` the offense's `start_time` (its last update, then the capture
        instant, when QRadar sent none). A changed offense replaces its earlier mentions, so a
        removed username does not linger. Statements per run: one DELETE for every offense the
        run stored, one INSERT (none when nothing qualifies) plus the savepoint pair; nothing
        when the run stored no snapshot. A failure rolls back to the savepoint and is logged by
        class name: mentions are a derived index and never cost the snapshots.
        """
        if not stored:
            return
        try:
            async with self.session.begin_nested():
                await self.session.execute(
                    delete(EntityMention)
                    .where(
                        EntityMention.instance_id == instance.id,
                        EntityMention.source == "OFFENSE",
                        EntityMention.ref_table == "offense_snapshot",
                        EntityMention.ref_id
                        == any_(
                            bindparam(
                                "offense_ids",
                                [str(offense_id) for offense_id in stored],
                                type_=ARRAY(String),
                            )
                        ),
                    )
                    .execution_options(synchronize_session=False)
                )
                await record_mentions(
                    self.session, [row for rows in stored.values() for row in rows]
                )
        except Exception as exc:
            logger.warning(
                "offense entity mentions were not recorded",
                extra={
                    "instance_id": str(instance.id),
                    "offenses": len(stored),
                    "error_class": type(exc).__name__,
                },
            )

    def _resume_point(
        self, watermark: CollectionWatermark, now: datetime
    ) -> datetime | None:
        """Where to resume, clamped by the maximum backfill.

        A watermark older than the backfill limit is treated as the limit: a
        collector that has been down for a month must not attempt a month-long
        catch-up in one run.
        """
        floor = now - timedelta(hours=self.settings.offense_max_backfill_hours)
        if watermark.watermark_at is None:
            return floor
        wm = watermark.watermark_at
        if wm.tzinfo is None:
            wm = wm.replace(tzinfo=UTC)
        return max(wm, floor)

    async def _store(
        self, instance: QRadarInstance, dto: OffenseDTO, *, captured_at: datetime
    ) -> bool:
        """Append a snapshot if the offense changed. Returns True if written."""
        content_hash = offense_content_hash(dto)
        latest = await self.session.scalar(
            select(OffenseSnapshot.content_hash)
            .where(
                OffenseSnapshot.instance_id == instance.id,
                OffenseSnapshot.qradar_offense_id == dto.qradar_id,
            )
            .order_by(OffenseSnapshot.captured_at.desc())
            .limit(1)
        )
        if latest == content_hash:
            return False

        age_seconds: int | None = None
        if dto.start_time is not None:
            end = dto.close_time or captured_at
            age_seconds = max(0, int((end - dto.start_time).total_seconds()))

        values = {
            "instance_id": instance.id,
            "qradar_offense_id": dto.qradar_id,
            "captured_at": captured_at,
            # QRadar text is attacker-influenced; sanitize on the way in so no
            # consumer can forget to do it on the way out.
            "description": sanitize_text(dto.description),
            "status": dto.status,
            "magnitude": dto.magnitude,
            "severity": dto.severity,
            "credibility": dto.credibility,
            "relevance": dto.relevance,
            "assigned_to": sanitize_text(dto.assigned_to),
            "is_assigned": bool(dto.assigned_to),
            "offense_type": dto.offense_type,
            "offense_type_name": sanitize_text(dto.offense_type_name),
            "offense_source": sanitize_text(dto.offense_source),
            "source_network": sanitize_text(dto.source_network),
            "event_count": dto.event_count,
            "flow_count": dto.flow_count,
            "device_count": dto.device_count,
            "source_count": dto.source_count,
            "destination_count": dto.destination_count,
            "start_time": dto.start_time,
            "last_updated_time": dto.last_updated_time,
            "close_time": dto.close_time,
            "age_seconds": age_seconds,
            "closing_reason_id": dto.closing_reason_id,
            "closing_reason": sanitize_text(dto.closing_reason),
            "categories": [sanitize_text(c) for c in dto.categories],
            "source_addresses": dto.source_addresses,
            "local_destination_addresses": dto.local_destination_addresses,
            "usernames": [sanitize_text(u) for u in dto.usernames],
            "log_source_ids": dto.log_source_ids,
            "rule_ids": dto.rule_ids,
            "content_hash": content_hash,
        }

        # Idempotent on the natural key: re-running the same capture instant
        # overwrites rather than duplicating.
        stmt = pg_insert(OffenseSnapshot).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instance_id", "qradar_offense_id", "captured_at"],
            set_={k: v for k, v in values.items()
                  if k not in ("instance_id", "qradar_offense_id", "captured_at")},
        )
        await self.session.execute(stmt)
        return True

    async def _get_or_create_watermark(
        self, instance: QRadarInstance
    ) -> CollectionWatermark:
        wm = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if wm is None:
            wm = CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME)
            self.session.add(wm)
            await self.session.flush()
        return wm
