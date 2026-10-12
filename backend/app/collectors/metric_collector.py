"""Log-source metric collection.

Guarantees:
  * Bounded batches — sources processed in configurable chunks.
  * No overlapping runs for the same instance: a PostgreSQL advisory lock keyed
    on (instance, collector) is held for the duration; a second worker skips.
  * Idempotent — metrics are upserted on (log_source_id, bucket_start), so
    re-collecting an interval overwrites rather than duplicates.
  * Explicit UTC interval boundaries — intervals are floored to the configured
    interval width in UTC.
  * Watermark + lag persisted per (instance, collector).
  * Bounded backfill — at most one cadence plus
    `collection_max_backfill_intervals` is filled per tick.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import BucketCompleteness, SearchFamily
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import LogSourceMetricSample
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService

COLLECTOR_NAME = "log_source_metric"


@dataclass
class CollectionReport:
    instance_id: uuid.UUID
    intervals_collected: int
    samples_written: int
    skipped_locked: bool = False
    watermark_at: datetime | None = None
    lag_seconds: int | None = None
    searches_sent: int = 0
    splits: int = 0
    partial: bool = False

    @property
    def buckets_collected(self) -> int:
        return self.intervals_collected


@dataclass(frozen=True)
class _CompletedBucket:
    samples: list[LogSourceMetricSample]
    search_window_start: datetime
    search_window_end: datetime
    search_bucket_count: int
    split_depth: int
    duration_ms: int
    collected_at: datetime


def floor_to_interval(dt: datetime, interval_seconds: int) -> datetime:
    """Floor a UTC datetime to the interval grid. Boundaries are explicit and
    deterministic so every worker agrees on interval identity."""
    dt = dt.astimezone(UTC)
    epoch = int(dt.timestamp())
    floored = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(floored, tz=UTC)


class MetricCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock=None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def _collection_source(self) -> str:
        """Which subsystem produced a bucket. QRadar exposes no per-log-source
        metric endpoint, so live volume is always a bounded Ariel aggregate."""
        name = type(self.provider).__name__.lower()
        return "mock" if "mock" in name else "ariel_aggregate"

    async def collect(self, instance: QRadarInstance) -> CollectionReport:
        interval = self.settings.collection_interval_seconds
        now = self._clock()
        # The most recent *completed* interval is the one before the current.
        current_boundary = floor_to_interval(now, interval)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                return CollectionReport(instance.id, 0, 0, skipped_locked=True)

            watermark = await self._get_or_create_watermark(instance)
            cadence = await SearchCadenceService(self.session, self.settings).get(
                SearchFamily.PHASE_A_VOLUME
            )
            cadence_buckets = max(1, cadence.cadence_seconds // interval)
            max_intervals = cadence_buckets + self.settings.collection_max_backfill_intervals
            intervals = self._intervals_to_collect(
                watermark, current_boundary, interval, max_intervals=max_intervals
            )

            completed, searches_sent, splits, search_partial = (
                await self._collect_bucketed_window(intervals, interval)
            )

            total_samples = 0
            buckets_collected = 0
            last_done: datetime | None = watermark.watermark_at
            for start in intervals:
                bucket = completed.get(start)
                if bucket is None:
                    break
                end = start + timedelta(seconds=interval)
                written = await self._write_bucket(
                    instance,
                    start,
                    end,
                    bucket.samples,
                    watermark_at=last_done,
                    duration_ms=bucket.duration_ms,
                    collected_at=bucket.collected_at,
                    search_window_start=bucket.search_window_start,
                    search_window_end=bucket.search_window_end,
                    search_bucket_count=bucket.search_bucket_count,
                    split_depth=bucket.split_depth,
                )
                total_samples += written
                buckets_collected += 1
                # The watermark only advances past an interval that completed.
                # A partial failure raises out of this loop, leaving the
                # watermark on the last whole interval so the next run retries
                # rather than skipping the gap.
                last_done = start

            watermark.watermark_at = last_done
            watermark.last_run_at = now
            watermark.intervals_collected += buckets_collected
            if last_done is not None:
                watermark.lag_seconds = int((now - (last_done + timedelta(seconds=interval)))
                                            .total_seconds())
            watermark.consecutive_failures = 0
            watermark.last_error = None
            await self.session.flush()

            return CollectionReport(
                instance_id=instance.id,
                intervals_collected=buckets_collected,
                samples_written=total_samples,
                watermark_at=watermark.watermark_at,
                lag_seconds=watermark.lag_seconds,
                searches_sent=searches_sent,
                splits=splits,
                partial=search_partial or buckets_collected < len(intervals),
            )

    def _intervals_to_collect(
        self,
        watermark: CollectionWatermark,
        current_boundary: datetime,
        interval: int,
        *,
        max_intervals: int,
    ) -> list[datetime]:
        """Intervals before the boundary, resuming at a bounded cadence window."""
        if watermark.watermark_at is None:
            # First run: just the most recent completed interval.
            start = current_boundary - timedelta(seconds=interval)
            return [start] if start < current_boundary else []

        first = watermark.watermark_at + timedelta(seconds=interval)
        out: list[datetime] = []
        cursor = first
        while cursor < current_boundary and len(out) < max_intervals:
            out.append(cursor)
            cursor += timedelta(seconds=interval)
        return out

    async def _collect_bucketed_window(
        self, intervals: list[datetime], interval: int
    ) -> tuple[dict[datetime, _CompletedBucket], int, int, bool]:
        """Search, recursively split truncation, and retain complete buckets."""
        completed: dict[datetime, _CompletedBucket] = {}
        searches_sent = 0
        splits = 0
        capped = False
        failure: ProviderError | None = None

        async def search(start: datetime, bucket_count: int, split_depth: int) -> None:
            nonlocal searches_sent, splits, capped, failure
            if failure is not None:
                return
            if searches_sent >= self.settings.search_cadence_max_searches_per_tick:
                capped = True
                return
            end = start + timedelta(seconds=interval * bucket_count)
            searches_sent += 1
            started = self._clock()
            if bucket_count == 1:
                try:
                    samples = await self.provider.get_log_source_metrics(start, end)
                except ProviderError as exc:
                    # A lone bucket that fails (or is still truncated) writes
                    # nothing; the watermark stops before it.
                    failure = exc
                    return
                collected_at = self._clock()
                completed[start] = _CompletedBucket(
                    samples=samples,
                    search_window_start=start,
                    search_window_end=end,
                    search_bucket_count=1,
                    split_depth=split_depth,
                    duration_ms=int((collected_at - started).total_seconds() * 1000),
                    collected_at=collected_at,
                )
                return
            try:
                batch = await self.provider.get_log_source_metrics_bucketed(
                    start,
                    end,
                    bucket_seconds=interval,
                    max_rows=self.settings.ariel_multi_bucket_max_rows,
                )
            except ProviderError as exc:
                failure = exc
                return
            collected_at = self._clock()
            duration_ms = int((collected_at - started).total_seconds() * 1000)
            if batch.truncated:
                splits += 1
                left_count = bucket_count // 2
                await search(start, left_count, split_depth + 1)
                if not capped and failure is None:
                    await search(
                        start + timedelta(seconds=interval * left_count),
                        bucket_count - left_count,
                        split_depth + 1,
                    )
                return

            for index in range(bucket_count):
                bucket_start = start + timedelta(seconds=interval * index)
                completed[bucket_start] = _CompletedBucket(
                    samples=batch.buckets.get(bucket_start, []),
                    search_window_start=start,
                    search_window_end=end,
                    search_bucket_count=bucket_count,
                    split_depth=split_depth,
                    duration_ms=duration_ms,
                    collected_at=collected_at,
                )

        if intervals:
            await search(intervals[0], len(intervals), 0)
        # Complete buckets before the failure are kept; with none, nothing
        # advanced and the error surfaces so the run is recorded as failed.
        if failure is not None and intervals[0] not in completed:
            raise failure
        return completed, searches_sent, splits, capped or failure is not None

    async def _collect_interval(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        *,
        watermark_at: datetime | None = None,
    ) -> int:
        started = self._clock()
        samples = await self.provider.get_log_source_metrics(start, end)
        collected_at = self._clock()
        duration_ms = int((collected_at - started).total_seconds() * 1000)
        return await self._write_bucket(
            instance,
            start,
            end,
            samples,
            watermark_at=watermark_at,
            duration_ms=duration_ms,
            collected_at=collected_at,
        )

    async def _write_bucket(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        samples: list[LogSourceMetricSample],
        *,
        watermark_at: datetime | None,
        duration_ms: int,
        collected_at: datetime,
        search_window_start: datetime | None = None,
        search_window_end: datetime | None = None,
        search_bucket_count: int | None = None,
        split_depth: int | None = None,
    ) -> int:
        """Persist one complete bucket through the existing upsert and zero-fill."""

        # Map qradar_id -> internal LogSource id for this instance.
        id_map = await self._log_source_id_map(instance.id)

        # Non-secret evidence of how these rows were produced. Never the token,
        # never response headers — only the shape of the request.
        provenance = {
            "collector": COLLECTOR_NAME,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "provider": type(self.provider).__name__,
            "sample_count": len(samples),
            # What `event_count` counts, and which columns were really measured.
            # Without these a reader cannot tell events (SUM(eventcount)) from
            # Ariel records (COUNT(*)), or a measured zero from a DTO default.
            # Rows written before these keys existed carry neither.
            "event_count_aggregation": self.provider.event_count_aggregation,
            "telemetry": sorted(t.value for t in self.provider.log_source_telemetry),
        }
        if search_window_start is not None and search_window_end is not None:
            provenance.update(
                {
                    "search_window_start": search_window_start.isoformat(),
                    "search_window_end": search_window_end.isoformat(),
                    "search_bucket_count": search_bucket_count,
                    "bucket_key": f"starttime / {int((end - start).total_seconds()) * 1000}",
                    "split_depth": split_depth,
                }
            )

        rows = []
        for s in samples:
            ls_id = id_map.get(s.qradar_id)
            if ls_id is None:
                continue  # source not yet in inventory; a sync will add it
            rows.append(
                {
                    "log_source_id": ls_id,
                    "bucket_start": start,
                    "completeness": BucketCompleteness.COMPLETE,
                    "collection_source": self._collection_source,
                    "query_provenance": provenance,
                    "collection_duration_ms": duration_ms,
                    "collected_at": collected_at,
                    "watermark_at": watermark_at,
                    "bucket_seconds": s.bucket_seconds,
                    "event_count": s.event_count,
                    "average_eps": s.average_eps,
                    "peak_eps": s.peak_eps,
                    "last_event_at": s.last_event_at,
                    "event_delay_seconds": s.event_delay_seconds,
                    "unknown_event_count": s.unknown_event_count,
                    "stored_event_count": s.stored_event_count,
                    "parsed_username_ratio": s.parsed_username_ratio,
                    "parsed_source_ip_ratio": s.parsed_source_ip_ratio,
                    "distinct_qid_count": s.distinct_qid_count,
                    "distinct_username_count": s.distinct_username_count,
                    "distinct_source_ip_count": s.distinct_source_ip_count,
                    "collection_error_count": s.collection_error_count,
                    "payload_signature": s.payload_signature,
                }
            )

        rows.extend(
            await self._zero_filled_rows(
                instance,
                start,
                end,
                reported={s.qradar_id for s in samples},
                id_map=id_map,
                provenance=provenance,
                duration_ms=duration_ms,
                collected_at=collected_at,
                watermark_at=watermark_at,
            )
        )
        if not rows:
            return 0

        # Idempotent upsert on the composite PK. Re-collecting overwrites.
        stmt = pg_insert(LogSourceMetric).values(rows)
        update_cols = {c: stmt.excluded[c] for c in rows[0] if c not in
                       ("log_source_id", "bucket_start")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["log_source_id", "bucket_start"], set_=update_cols
        )
        await self.session.execute(stmt)
        return len(rows)

    async def _zero_filled_rows(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        *,
        reported: set[int],
        id_map: dict[int, uuid.UUID],
        provenance: dict,
        duration_ms: int,
        collected_at: datetime,
        watermark_at: datetime | None,
    ) -> list[dict]:
        """Explicit zero buckets for monitored sources QRadar did not report.

        A source that emits nothing is simply absent from the Ariel
        `GROUP BY logsourceid` result, so without this the interval leaves no
        row at all. Silence would then be indistinguishable from a collection
        gap: the anomaly engine evaluates the newest row it can find, so it
        would re-judge the last busy bucket forever and never observe the
        silence. `detect_no_events` requires a COMPLETE bucket with a zero
        count, and `_preceding_empty_buckets` counts consecutive complete zero
        buckets — both were written expecting these rows to exist.

        Only monitored sources are filled. An unmonitored source is never
        evaluated, so a row for it would be storage without a reader.

        The zero is an observation, not an assumption: the query for this
        window succeeded and returned no events for this source. That is why
        the row is COMPLETE. A failed or partial collection raises instead of
        reaching this point, so no zero row is ever written for a window we did
        not actually observe.
        """
        missing = [
            (qradar_id, ls_id)
            for qradar_id, ls_id in id_map.items()
            if qradar_id not in reported
        ]
        if not missing:
            return []

        monitored = set(
            (
                await self.session.scalars(
                    select(LogSource.id).where(
                        LogSource.instance_id == instance.id,
                        LogSource.monitoring_enabled.is_(True),
                    )
                )
            ).all()
        )

        bucket_seconds = int((end - start).total_seconds())
        # Marked so a zero that came from an empty result is never mistaken for
        # a zero that a provider actively reported.
        zero_provenance = {**provenance, "zero_filled": True}

        return [
            {
                "log_source_id": ls_id,
                "bucket_start": start,
                "completeness": BucketCompleteness.COMPLETE,
                "collection_source": self._collection_source,
                "query_provenance": zero_provenance,
                "collection_duration_ms": duration_ms,
                "collected_at": collected_at,
                "watermark_at": watermark_at,
                "bucket_seconds": bucket_seconds,
                "event_count": 0,
                "average_eps": 0.0,
                "peak_eps": 0.0,
                "last_event_at": None,
                "event_delay_seconds": None,
                "unknown_event_count": 0,
                "stored_event_count": 0,
                "parsed_username_ratio": None,
                "parsed_source_ip_ratio": None,
                "distinct_qid_count": 0,
                "distinct_username_count": 0,
                "distinct_source_ip_count": 0,
                "collection_error_count": 0,
                "payload_signature": None,
            }
            for _, ls_id in missing
            if ls_id in monitored
        ]

    async def _log_source_id_map(self, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
        rows = await self.session.execute(
            select(LogSource.qradar_id, LogSource.id).where(
                LogSource.instance_id == instance_id
            )
        )
        return dict(rows.all())

    async def _get_or_create_watermark(self, instance: QRadarInstance) -> CollectionWatermark:
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
