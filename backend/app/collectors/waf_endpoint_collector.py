"""WAF endpoint observation collection.

Mirrors MetricCollector's guarantees — advisory lock, watermark, bounded
backfill, idempotent upsert, explicit completeness — but deliberately does NOT
copy its zero-fill strategy.

`MetricCollector._zero_filled_rows` writes a zero row for every monitored log
source absent from the Ariel result. That works because log sources are few,
operator-owned and enumerable before the query runs. WAF endpoints are none of
those things: they are created by application deployments, discovered from
traffic, and can be minted by anyone who can send a request. Copying the pattern
would write a zero row for every endpoint ever seen, in every bucket, forever.

So Phase B zero-fills only endpoints that are **already monitored and still
active**, and retires an endpoint that has not been seen for
`waf_endpoint_active_days`. That is what keeps zero-row growth bounded by an
operator-controlled quantity rather than by traffic history.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.models.enums import BucketCompleteness
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafEndpointMetric
from app.providers.base import QRadarProvider
from app.providers.dto import WafObservationBatch
from app.services.locks import CollectorAdvisoryLock
from app.waf.normalize import (
    ENDPOINT_NORMALIZER_VERSION,
    normalize_endpoint,
    normalize_method,
    normalize_virtual_host,
)

COLLECTOR_NAME = "waf_endpoint_metric"


@dataclass
class WafCollectionReport:
    instance_id: uuid.UUID
    intervals_collected: int = 0
    endpoints_seen: int = 0
    endpoints_created: int = 0
    rows_written: int = 0
    zero_rows_written: int = 0
    retired: int = 0
    #: Groups the provider returned that carried no usable endpoint or method.
    #: Counted, never silently dropped: it is the numerator of the coverage
    #: question an operator asks when a source looks quieter than it is.
    unusable_groups: int = 0
    skipped_locked: bool = False
    skipped_reason: str | None = None
    watermark_at: datetime | None = None
    partial_buckets: list[str] = field(default_factory=list)


@dataclass
class _Observation:
    """One normalized endpoint's volume within a bucket."""

    virtual_host: str | None
    endpoint: str
    method: str
    request_count: int
    distinct_clients: int | None
    record_count: int


def fold_groups(batch: WafObservationBatch) -> tuple[list[_Observation], int]:
    """Normalize and merge provider groups into endpoint observations.

    Folding is required, not incidental: normalization is many-to-one, so
    `/users/1` and `/users/2` arrive as separate groups and must sum into one
    `/users/{id}` observation. Summing rather than picking one is the difference
    between a correct request count and an arbitrary fraction of it.

    Returns the observations and the number of groups that carried no usable
    endpoint or method. A group with an unparseable method is dropped rather
    than defaulted: a bogus method would mint a permanent entity that no real
    traffic will ever match again.
    """
    merged: dict[tuple[str | None, str, str], _Observation] = {}
    unusable = 0
    for group in batch.groups:
        endpoint = normalize_endpoint(group.raw_url)
        method = normalize_method(group.raw_method)
        if not endpoint.is_usable or method is None or endpoint.value is None:
            unusable += 1
            continue
        host = normalize_virtual_host(group.raw_virtual_host)
        key = (host, endpoint.value, method)
        existing = merged.get(key)
        if existing is None:
            merged[key] = _Observation(
                virtual_host=host,
                endpoint=endpoint.value,
                method=method,
                request_count=group.request_count,
                distinct_clients=group.distinct_client_count,
                record_count=group.record_count,
            )
            continue
        existing.request_count += group.request_count
        existing.record_count += group.record_count
        # Distinct client counts cannot be summed across groups without
        # double-counting a client that hit both. The maximum is a defensible
        # lower bound; the sum would be an invention.
        if group.distinct_client_count is not None:
            existing.distinct_clients = max(
                existing.distinct_clients or 0, group.distinct_client_count
            )
    return list(merged.values()), unusable


class WafEndpointCollector:
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

    @property
    def _collection_source(self) -> str:
        name = type(self.provider).__name__.lower()
        return "mock" if "mock" in name else "ariel_aggregate"

    async def collect(
        self, instance: QRadarInstance, *, log_source: LogSource
    ) -> WafCollectionReport:
        """Collect one WAF log source's endpoint observations."""
        report = WafCollectionReport(instance.id)
        if not self.settings.waf_collection_enabled:
            report.skipped_reason = "waf collection disabled"
            return report
        if not log_source.monitoring_enabled:
            report.skipped_reason = "monitoring disabled"
            return report

        interval = self.settings.collection_interval_seconds
        now = self._clock()
        current_boundary = floor_to_interval(now, interval)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            watermark = await self._get_or_create_watermark(instance)
            intervals = self._intervals_to_collect(
                watermark, current_boundary, interval
            )

            last_done = watermark.watermark_at
            for start in intervals:
                end = start + timedelta(seconds=interval)
                await self._collect_interval(
                    log_source, start, end, watermark_at=last_done, report=report
                )
                # Only advances past an interval that completed. A failure
                # raises out of this loop and leaves the watermark behind, so
                # the next run retries the gap rather than skipping it.
                last_done = start

            watermark.watermark_at = last_done
            watermark.last_run_at = now
            watermark.intervals_collected += len(intervals)
            watermark.consecutive_failures = 0
            watermark.last_error = None
            report.intervals_collected = len(intervals)
            report.watermark_at = watermark.watermark_at

            report.retired = await self._retire_stale_endpoints(log_source, now)
            await self.session.flush()
            return report

    def _intervals_to_collect(
        self, watermark: CollectionWatermark, current_boundary: datetime, interval: int
    ) -> list[datetime]:
        max_backfill = self.settings.collection_max_backfill_intervals
        if watermark.watermark_at is None:
            start = current_boundary - timedelta(seconds=interval)
            return [start] if start < current_boundary else []
        cursor = watermark.watermark_at + timedelta(seconds=interval)
        out: list[datetime] = []
        while cursor < current_boundary and len(out) < max_backfill + 1:
            out.append(cursor)
            cursor += timedelta(seconds=interval)
        return out

    async def _collect_interval(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        *,
        watermark_at: datetime | None,
        report: WafCollectionReport,
    ) -> None:
        started = self._clock()
        vhost_property = self.settings.waf_virtual_host_property.strip() or None
        batch = await self.provider.get_waf_observations(
            qradar_log_source_id=log_source.qradar_id,
            window_start=start,
            window_end=end,
            url_property=self.settings.waf_url_property,
            method_property=self.settings.waf_method_property,
            virtual_host_property=vhost_property,
            max_groups=self.settings.waf_max_groups_per_bucket,
        )
        collected_at = self._clock()
        duration_ms = int((collected_at - started).total_seconds() * 1000)

        observations, unusable = fold_groups(batch)
        report.unusable_groups += unusable
        report.endpoints_seen += len(observations)

        parsed_total = sum(o.request_count for o in observations)
        # Coverage is measured, not assumed. When the source produced nothing at
        # all the ratio is 1.0: we observed the whole window and it was empty,
        # which is a complete measurement of zero, not a coverage failure.
        coverage = (
            parsed_total / batch.total_events if batch.total_events > 0 else 1.0
        )
        coverage = min(1.0, max(0.0, coverage))

        under_covered = coverage < self.settings.waf_min_endpoint_coverage
        completeness = (
            BucketCompleteness.PARTIAL
            if (batch.truncated or under_covered)
            else BucketCompleteness.COMPLETE
        )
        if completeness is BucketCompleteness.PARTIAL:
            report.partial_buckets.append(start.isoformat())

        provenance = {
            "collector": COLLECTOR_NAME,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "provider": type(self.provider).__name__,
            "group_count": len(batch.groups),
            "unusable_groups": unusable,
            "truncated": batch.truncated,
            "total_events": batch.total_events,
            "parsed_events": parsed_total,
            "aql": batch.query,
        }

        seen_ids: set[uuid.UUID] = set()
        rows: list[dict] = []
        bucket_seconds = int((end - start).total_seconds())

        for obs in observations:
            endpoint = await self._get_or_create_endpoint(
                log_source, obs, first_seen=start, last_seen=start, report=report
            )
            seen_ids.add(endpoint.id)
            rows.append(
                {
                    "waf_endpoint_id": endpoint.id,
                    "bucket_start": start,
                    "bucket_seconds": bucket_seconds,
                    "request_count": obs.request_count,
                    "distinct_client_count": obs.distinct_clients,
                    # NULL, not 0: no verified WAF DSM exposes blocked or error
                    # counts, and 0 would assert a measurement we did not make.
                    "blocked_count": None,
                    "error_count": None,
                    "endpoint_coverage_ratio": coverage,
                    "completeness": completeness,
                    "collection_source": self._collection_source,
                    "query_provenance": provenance,
                    "collection_duration_ms": duration_ms,
                    "collected_at": collected_at,
                    "watermark_at": watermark_at,
                    "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
                }
            )

        rows.extend(
            await self._zero_rows(
                log_source,
                start,
                bucket_seconds=bucket_seconds,
                seen_ids=seen_ids,
                coverage=coverage,
                completeness=completeness,
                provenance={**provenance, "zero_filled": True},
                duration_ms=duration_ms,
                collected_at=collected_at,
                watermark_at=watermark_at,
                report=report,
            )
        )

        if not rows:
            return

        stmt = pg_insert(WafEndpointMetric).values(rows)
        update_cols = {
            c: stmt.excluded[c]
            for c in rows[0]
            if c not in ("waf_endpoint_id", "bucket_start")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["waf_endpoint_id", "bucket_start"], set_=update_cols
        )
        await self.session.execute(stmt)
        report.rows_written += len(rows)

    async def _get_or_create_endpoint(
        self,
        log_source: LogSource,
        obs: _Observation,
        *,
        first_seen: datetime,
        last_seen: datetime,
        report: WafCollectionReport,
    ) -> WafEndpoint:
        """Find or create the entity, treating a NULL virtual host as a value.

        `is_(None)` rather than `== None` matters here: SQLAlchemy renders the
        former as `IS NULL` and the latter would produce `= NULL`, which is
        never true. Getting that wrong would make every hostless lookup miss,
        every bucket create a fresh entity, and no baseline ever accumulate --
        the same failure the NULLS NOT DISTINCT index guards against from the
        other side.
        """
        host_clause = (
            WafEndpoint.virtual_host.is_(None)
            if obs.virtual_host is None
            else WafEndpoint.virtual_host == obs.virtual_host
        )
        existing = await self.session.scalar(
            select(WafEndpoint).where(
                WafEndpoint.log_source_id == log_source.id,
                host_clause,
                WafEndpoint.normalized_endpoint == obs.endpoint,
                WafEndpoint.http_method == obs.method,
                WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
        )
        if existing is not None:
            if last_seen > existing.last_seen_at:
                existing.last_seen_at = last_seen
            # A previously retired endpoint that is requested again becomes
            # active. Its first_seen_at is deliberately NOT reset: the novelty
            # detectors distinguish a first sighting from a reappearance.
            if not existing.is_active:
                existing.is_active = True
                existing.retired_at = None
            return existing

        created = WafEndpoint(
            instance_id=log_source.instance_id,
            log_source_id=log_source.id,
            virtual_host=obs.virtual_host,
            normalized_endpoint=obs.endpoint,
            http_method=obs.method,
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
        )
        self.session.add(created)
        await self.session.flush()
        report.endpoints_created += 1
        return created

    async def _zero_rows(
        self,
        log_source: LogSource,
        start: datetime,
        *,
        bucket_seconds: int,
        seen_ids: set[uuid.UUID],
        coverage: float,
        completeness: BucketCompleteness,
        provenance: dict,
        duration_ms: int,
        collected_at: datetime,
        watermark_at: datetime | None,
        report: WafCollectionReport,
    ) -> list[dict]:
        """Explicit zeros for active endpoints this bucket did not mention.

        The zero is an observation, not an assumption: the query for this window
        succeeded and returned no requests for this endpoint. That is why the row
        carries the bucket's completeness rather than being forced COMPLETE -- if
        the bucket was truncated or under-covered, a zero from it is exactly as
        partial as everything else in it.

        Only *active* endpoints are filled. That is the whole difference from
        Phase A: without retirement this set grows monotonically with traffic
        history and never shrinks.
        """
        rows = await self.session.scalars(
            select(WafEndpoint).where(
                WafEndpoint.log_source_id == log_source.id,
                WafEndpoint.is_active.is_(True),
                WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
        )
        out: list[dict] = []
        for endpoint in rows:
            if endpoint.id in seen_ids:
                continue
            out.append(
                {
                    "waf_endpoint_id": endpoint.id,
                    "bucket_start": start,
                    "bucket_seconds": bucket_seconds,
                    "request_count": 0,
                    "distinct_client_count": 0,
                    "blocked_count": None,
                    "error_count": None,
                    "endpoint_coverage_ratio": coverage,
                    "completeness": completeness,
                    "collection_source": self._collection_source,
                    "query_provenance": provenance,
                    "collection_duration_ms": duration_ms,
                    "collected_at": collected_at,
                    "watermark_at": watermark_at,
                    "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
                }
            )
        report.zero_rows_written += len(out)
        return out

    async def _retire_stale_endpoints(
        self, log_source: LogSource, now: datetime
    ) -> int:
        """Stop zero-filling endpoints nobody has requested in a long time.

        Retirement is what bounds storage. Rows and baselines are retained until
        retention expires -- this only stops *new* zero rows being written.
        """
        cutoff = now - timedelta(days=self.settings.waf_endpoint_active_days)
        stale = await self.session.scalars(
            select(WafEndpoint).where(
                WafEndpoint.log_source_id == log_source.id,
                WafEndpoint.is_active.is_(True),
                WafEndpoint.last_seen_at < cutoff,
            )
        )
        count = 0
        for endpoint in stale:
            endpoint.is_active = False
            endpoint.retired_at = now
            count += 1
        return count

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
