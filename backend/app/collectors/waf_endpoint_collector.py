"""WAF endpoint observation collection.

Mirrors MetricCollector's guarantees -- advisory lock, watermark, bounded
backfill, idempotent writes, explicit completeness -- but deliberately does NOT
copy its zero-fill strategy.

`MetricCollector._zero_filled_rows` writes a zero row for every monitored log
source absent from the Ariel result. That works because log sources are few,
operator-owned and enumerable before the query runs. WAF endpoints are none of
those things: they are created by application deployments, discovered from
traffic, and can be minted by anyone who can send a request.

So Phase B tracks only endpoints admitted through the bounded, deterministic
rule in `app.waf.admission`, retires an endpoint that has not been seen for
`waf_endpoint_active_days`, and -- since Phase B.1.5 -- writes **no zero rows at
all**. A tracked endpoint absent from an exact bucket served zero requests; the
bucket's `WafSourceBucket` record and the endpoint's tracking window are what
make that absence a measurement (app/waf/observations.py). Rows are written only
for endpoints the grouped result mentioned, and re-collecting a bucket replaces
its rows rather than merging into them.

**Completeness is per dimension, not per bucket.** When the grouped query hits
its cap, or too little of the source's traffic carries a parseable endpoint,
every endpoint count in the bucket becomes a *lower bound* (recorded as
`request_count_measurement = LOWER_BOUND`, completeness PARTIAL): enough to prove
a spike, never enough to call an endpoint normal. The source's total volume,
from its own ungrouped aggregate, stays exact either way, and the bucket is
still collected, recorded and evaluated -- a flood of raw paths cannot blind the
source. See docs/PHASE-B-WAF-BEHAVIOR.md section 29.

**Ariel budget.** Each bucket's searches run under one global WAF Ariel permit
(`app.services.waf_ariel_budget`). A bucket that cannot get one is not
collected: the loop stops, the watermark stays on the last bucket actually
written, and the next run retries. Nothing is written for it -- in particular,
no zero.

**Breakdowns (Phase B.2).** A profile's `BreakdownPlan` may add up to three more
bounded searches per bucket, under the same permit: requests by endpoint and
action, by endpoint and response code, and by endpoint and source IP. Each has
its own group cap and its own completeness (`WafSourceBucket.*_measurement`),
because a whole volume search says nothing about whether the action search was
whole. A breakdown search that fails is recorded FAILED and its values stay
NULL; it does not fail the bucket, so an unreliable optional field cannot stop
request-rate detection. A tracked endpoint that a breakdown mentions but the
volume search did not still gets a row, so its breakdown counts are kept and a
tracked endpoint absent from an exact breakdown reads as zero of that breakdown.

Property names come from the source's profile (`WafFieldMapping`). No property
name is a default here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import in_maintenance
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.models.enums import (
    BucketCompleteness,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import WafBreakdownBatch, WafObservationBatch
from app.services.locks import CollectorAdvisoryLock
from app.services.waf_ariel_budget import WafArielBudget
from app.waf.admission import AdmissionPolicy, decide_admission
from app.waf.breakdown import (
    ActionCounts,
    ClientCounts,
    EndpointKey,
    ResponseCounts,
    fold_actions,
    fold_response_codes,
    fold_source_ips,
)
from app.waf.normalize import (
    ENDPOINT_NORMALIZER_VERSION,
    normalize_endpoint,
    normalize_method,
    normalize_virtual_host,
)
from app.waf.profile import BreakdownPlan, WafFieldMapping
from app.waf.semantics import breakdown_semantics, request_count_semantics

COLLECTOR_NAME = "waf_endpoint_metric"


def watermark_collector(log_source: LogSource) -> str:
    """Watermark and advisory-lock key for one WAF log source.

    Per source, not per instance. Each source is collected in its own call, so
    an instance-wide watermark advanced by the first source leaves no intervals
    for the second: it would be skipped every cycle, silently. The QRadar id is
    unique within the instance the watermark row is already scoped to.
    """
    return f"{COLLECTOR_NAME}:{log_source.qradar_id}"


@dataclass
class WafCollectionReport:
    instance_id: uuid.UUID
    intervals_collected: int = 0
    endpoints_seen: int = 0
    endpoints_created: int = 0
    endpoints_reactivated: int = 0
    rows_written: int = 0
    zero_rows_written: int = 0
    retired: int = 0
    #: Groups the provider returned that carried no usable endpoint or method.
    #: Counted, never silently dropped: it is the numerator of the coverage
    #: question an operator asks when a source looks quieter than it is.
    unusable_groups: int = 0
    untracked_endpoints: int = 0
    untracked_requests: int = 0
    #: Ariel searches the provider reported issuing during this run.
    ariel_searches: int = 0
    #: Wall time of the whole collection, including permit waits.
    duration_ms: int = 0
    #: Collection stopped because no global WAF Ariel permit freed up in time.
    #: Every bucket before that point was written; none after it was touched.
    capacity_unavailable: bool = False
    permit_wait_ms: int = 0
    skipped_locked: bool = False
    skipped_reason: str | None = None
    watermark_at: datetime | None = None
    lag_seconds: int | None = None
    partial_buckets: list[str] = field(default_factory=list)
    lower_bound_buckets: list[str] = field(default_factory=list)
    admission_truncated_buckets: list[str] = field(default_factory=list)
    maintenance_buckets: list[str] = field(default_factory=list)
    #: Breakdown -> buckets in which its search was truncated or failed.
    breakdown_lower_bound_buckets: dict[str, list[str]] = field(default_factory=dict)
    breakdown_failed_buckets: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class _BreakdownResult:
    """One breakdown's outcome for one bucket."""

    status: WafTelemetryStatus
    batch: WafBreakdownBatch | None = None
    error_class: str | None = None


@dataclass
class _Folded:
    """Per-endpoint counts of each breakdown that returned a result."""

    actions: dict[EndpointKey, ActionCounts] | None = None
    responses: dict[EndpointKey, ResponseCounts] | None = None
    clients: dict[EndpointKey, ClientCounts] | None = None

    def keys(self) -> set[EndpointKey]:
        out: set[EndpointKey] = set()
        for counts in (self.actions, self.responses, self.clients):
            out.update(counts or ())
        return out


@dataclass
class _Fetched:
    batch: WafObservationBatch
    breakdowns: dict[WafBreakdown, _BreakdownResult]
    duration_ms: int
    collected_at: datetime


@dataclass
class _Observation:
    """One normalized endpoint's volume within a bucket."""

    virtual_host: str | None
    endpoint: str
    method: str
    request_count: int
    distinct_clients: int | None
    record_count: int

    @property
    def key(self) -> tuple[str | None, str, str]:
        return (self.virtual_host, self.endpoint, self.method)


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
        mapping: WafFieldMapping,
        profile_version: int,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        ariel_budget: WafArielBudget | None = None,
        breakdowns: BreakdownPlan | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.mapping = mapping
        self.profile_version = profile_version
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        #: None: no global budget (direct use outside the scheduled pipeline).
        self.ariel_budget = ariel_budget
        #: None: no breakdowns, exactly the B.1.5 two-search collection.
        self.breakdowns = breakdowns

    @property
    def _collection_source(self) -> str:
        name = type(self.provider).__name__.lower()
        return "mock" if "mock" in name else "ariel_aggregate"

    @property
    def metric_semantics(self) -> str:
        return request_count_semantics(
            aggregation=self.provider.event_count_aggregation,
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            profile_version=self.profile_version,
        )

    def breakdown_semantics(self, breakdown: WafBreakdown) -> str:
        return breakdown_semantics(
            breakdown.value,
            aggregation=self.provider.event_count_aggregation,
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            profile_version=self.profile_version,
        )

    def planned_breakdowns(self) -> frozenset[WafBreakdown]:
        return self.breakdowns.collect if self.breakdowns is not None else frozenset()

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
        run_started = time.monotonic()

        collector = watermark_collector(log_source)
        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, collector
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            watermark = await self._get_or_create_watermark(instance, collector)
            intervals = self._intervals_to_collect(
                watermark, current_boundary, interval
            )

            last_done = watermark.watermark_at
            collected = 0
            for start in intervals:
                end = start + timedelta(seconds=interval)
                fetched = await self._fetch_under_permit(log_source, start, end, report)
                if fetched is None:
                    # No permit: stop here. Nothing is written for this bucket
                    # or any later one, and the watermark stays on the last
                    # bucket that was, so the next run resumes exactly here.
                    report.capacity_unavailable = True
                    break
                await self._persist_interval(
                    log_source, start, end, fetched, watermark_at=last_done, report=report,
                )
                # Only advances past an interval that completed. A failure
                # raises out of this loop and leaves the watermark behind, so
                # the next run retries the gap rather than skipping it.
                last_done = start
                collected += 1

            watermark.watermark_at = last_done
            watermark.last_run_at = now
            watermark.intervals_collected += collected
            if collected or not intervals:
                # A run that collected nothing because capacity was exhausted
                # has proven nothing about the source; it neither clears nor
                # adds to its failure record.
                watermark.consecutive_failures = 0
                watermark.last_error = None
            if last_done is not None:
                watermark.lag_seconds = int(
                    (now - (last_done + timedelta(seconds=interval))).total_seconds()
                )
            report.intervals_collected = collected
            report.watermark_at = watermark.watermark_at
            report.lag_seconds = watermark.lag_seconds
            report.duration_ms = int((time.monotonic() - run_started) * 1000)
            metadata = {
                **(watermark.collection_metadata or {}),
                "profile_version": self.profile_version,
                "metric_semantics": self.metric_semantics,
                "last_run_ariel_searches": report.ariel_searches,
                "last_run_duration_ms": report.duration_ms,
                "last_run_intervals": collected,
                "last_run_intervals_due": len(intervals),
                "last_run_permit_wait_ms": report.permit_wait_ms,
            }
            if report.capacity_unavailable:
                metadata["last_capacity_skip_at"] = now.isoformat()
            if collected or not intervals:
                metadata["last_success_at"] = now.isoformat()
            watermark.collection_metadata = metadata

            report.retired = await self._retire_stale_endpoints(log_source, now, last_done)
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

    async def _fetch_under_permit(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        report: WafCollectionReport,
    ) -> _Fetched | None:
        """One bucket's Ariel searches under a global permit, or None without one."""
        if self.ariel_budget is None:
            return await self._fetch(log_source, start, end, report)
        async with self.ariel_budget.permit() as permit:
            if permit is None:
                return None
            report.permit_wait_ms += int(permit.waited_seconds * 1000)
            return await self._fetch(log_source, start, end, report)

    async def _fetch(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        report: WafCollectionReport,
    ) -> _Fetched:
        started = self._clock()
        batch = await self.provider.get_waf_observations(
            qradar_log_source_id=log_source.qradar_id,
            window_start=start,
            window_end=end,
            url_property=self.mapping.url,
            method_property=self.mapping.method,
            virtual_host_property=self.mapping.virtual_host,
            max_groups=self.settings.waf_max_groups_per_bucket,
        )
        report.ariel_searches += batch.ariel_searches
        breakdowns: dict[WafBreakdown, _BreakdownResult] = {}
        for breakdown in sorted(self.planned_breakdowns()):
            breakdowns[breakdown] = await self._fetch_breakdown(
                log_source, start, end, breakdown, report
            )
        collected_at = self._clock()
        return _Fetched(
            batch=batch,
            breakdowns=breakdowns,
            duration_ms=int((collected_at - started).total_seconds() * 1000),
            collected_at=collected_at,
        )

    async def _fetch_breakdown(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        breakdown: WafBreakdown,
        report: WafCollectionReport,
    ) -> _BreakdownResult:
        prop = {
            WafBreakdown.ACTION: self.mapping.action,
            WafBreakdown.RESPONSE_CODE: self.mapping.response_code,
            WafBreakdown.SOURCE_IP: None,
        }[breakdown]
        try:
            batch = await self.provider.get_waf_breakdown(
                qradar_log_source_id=log_source.qradar_id,
                window_start=start,
                window_end=end,
                url_property=self.mapping.url,
                method_property=self.mapping.method,
                virtual_host_property=self.mapping.virtual_host,
                breakdown=breakdown.value,
                breakdown_property=prop,
                max_groups=self.settings.waf_max_breakdown_groups_per_bucket,
            )
        except (ProviderError, ValueError) as exc:
            # Recorded, not raised: the volume searches succeeded and must still
            # be judged. The class name only -- a message can echo query text.
            report.breakdown_failed_buckets.setdefault(breakdown.value, []).append(
                start.isoformat()
            )
            return _BreakdownResult(WafTelemetryStatus.FAILED, error_class=type(exc).__name__)
        report.ariel_searches += batch.ariel_searches
        if batch.truncated:
            report.breakdown_lower_bound_buckets.setdefault(breakdown.value, []).append(
                start.isoformat()
            )
            return _BreakdownResult(WafTelemetryStatus.LOWER_BOUND, batch)
        return _BreakdownResult(WafTelemetryStatus.EXACT, batch)

    async def _persist_interval(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        fetched: _Fetched,
        *,
        watermark_at: datetime | None,
        report: WafCollectionReport,
    ) -> None:
        batch = fetched.batch
        duration_ms, collected_at = fetched.duration_ms, fetched.collected_at
        observations, unusable = fold_groups(batch)
        folded = self._fold_breakdowns(fetched.breakdowns)
        breakdown_keys = folded.keys()
        report.unusable_groups += unusable
        report.endpoints_seen += len(observations)

        parsed_total = sum(o.request_count for o in observations)
        # Coverage is measured, not assumed. When the source produced nothing at
        # all the ratio is 1.0: we observed the whole window and it was empty,
        # which is a complete measurement of zero, not a coverage failure. When
        # grouping was truncated, `parsed_total` covers only the returned groups,
        # so the ratio is a lower bound on the true coverage.
        coverage = (
            parsed_total / batch.total_events if batch.total_events > 0 else 1.0
        )
        coverage = min(1.0, max(0.0, coverage))

        under_covered = coverage < self.settings.waf_min_endpoint_coverage
        # Either condition leaves events of this window unattributed, so every
        # endpoint count below can only understate the truth. That makes them
        # lower bounds -- not unknowns: they can still prove an increase.
        # Admission truncation is deliberately NOT one of them: refusing to
        # create new entities leaves every tracked endpoint's count whole.
        measurement = (
            WafMeasurement.LOWER_BOUND
            if (batch.truncated or under_covered)
            else WafMeasurement.EXACT
        )
        completeness = (
            BucketCompleteness.COMPLETE
            if measurement is WafMeasurement.EXACT
            else BucketCompleteness.PARTIAL
        )
        if completeness is BucketCompleteness.PARTIAL:
            report.partial_buckets.append(start.isoformat())
            report.lower_bound_buckets.append(start.isoformat())

        maintenance = in_maintenance(
            start,
            maintenance_mode=log_source.maintenance_mode,
            maintenance_until=log_source.maintenance_until,
        )
        if maintenance:
            report.maintenance_buckets.append(start.isoformat())
        semantics = self.metric_semantics
        bucket_seconds = int((end - start).total_seconds())

        # --- admission ------------------------------------------------------
        existing = await self._existing_endpoints(
            log_source, {o.endpoint for o in observations} | {k[1] for k in breakdown_keys}
        )
        tracked_obs = [o for o in observations if o.key in existing and existing[o.key].is_active]
        unseen = [o for o in observations if o.key not in existing or not existing[o.key].is_active]
        active_count = await self._active_endpoint_count(log_source)
        decision = decide_admission(
            unseen,
            active_count=active_count,
            policy=AdmissionPolicy(
                max_new_per_bucket=self.settings.waf_max_new_endpoints_per_bucket,
                max_active=self.settings.waf_max_active_endpoints_per_source,
                min_requests=self.settings.waf_min_admission_requests,
            ),
        )
        report.untracked_endpoints += len(decision.untracked)
        report.untracked_requests += decision.untracked_requests
        if decision.truncated:
            report.admission_truncated_buckets.append(start.isoformat())

        tracked: list[tuple[_Observation, WafEndpoint]] = []
        for obs in tracked_obs:
            endpoint = existing[obs.key]
            if start > endpoint.last_seen_at:
                endpoint.last_seen_at = start
            tracked.append((obs, endpoint))
        # A tracked endpoint a breakdown mentioned but the volume search did not
        # (it can happen when only the volume search was truncated) gets a row,
        # so its breakdown counts are kept. Its visible volume is zero.
        observed_keys = {o.key for o in observations}
        for key in sorted(breakdown_keys - observed_keys, key=lambda k: (k[0] or "", k[1], k[2])):
            endpoint_or_none = existing.get(key)
            if endpoint_or_none is None or not endpoint_or_none.is_active:
                continue
            tracked.append((
                _Observation(
                    virtual_host=key[0], endpoint=key[1], method=key[2],
                    request_count=0, distinct_clients=None, record_count=0,
                ),
                endpoint_or_none,
            ))
        created: list[WafEndpoint] = []
        for obs in decision.admitted:
            endpoint_or_none = existing.get(obs.key)
            if endpoint_or_none is not None:
                # A retired endpoint requested again re-enters through admission
                # like any untracked endpoint. Its first_seen_at is deliberately
                # NOT reset: novelty distinguishes a first sighting from a return.
                # Its tracking period IS reset: rows from before retirement lost
                # the zeros that belonged with them, so they no longer train, and
                # its baseline is rebuilt first.
                endpoint_or_none.is_active = True
                endpoint_or_none.retired_at = None
                endpoint_or_none.tracked_since = start
                endpoint_or_none.tracked_until = None
                endpoint_or_none.baseline_rebuilt_at = None
                if start > endpoint_or_none.last_seen_at:
                    endpoint_or_none.last_seen_at = start
                report.endpoints_reactivated += 1
                tracked.append((obs, endpoint_or_none))
                continue
            endpoint = WafEndpoint(
                id=uuid.uuid4(),
                instance_id=log_source.instance_id,
                log_source_id=log_source.id,
                virtual_host=obs.virtual_host,
                normalized_endpoint=obs.endpoint,
                http_method=obs.method,
                normalizer_version=ENDPOINT_NORMALIZER_VERSION,
                first_seen_at=start,
                last_seen_at=start,
                tracked_since=start,
            )
            created.append(endpoint)
            tracked.append((obs, endpoint))
        if created:
            self.session.add_all(created)
            report.endpoints_created += len(created)
        # One flush for every entity insert and last-seen update in the bucket.
        await self.session.flush()

        # --- rows -----------------------------------------------------------
        # Re-collecting a bucket replaces its rows. Merging would leave a row
        # for an endpoint the fresh result no longer mentions -- a stale count
        # that the fresh bucket record would then vouch for.
        await self.session.execute(
            delete(WafEndpointMetric)
            .where(
                WafEndpointMetric.bucket_start == start,
                WafEndpointMetric.waf_endpoint_id.in_(
                    select(WafEndpoint.id).where(WafEndpoint.log_source_id == log_source.id)
                ),
            )
            .execution_options(synchronize_session=False)
        )
        rows: list[dict] = [
            {
                "waf_endpoint_id": endpoint.id,
                "bucket_start": start,
                "bucket_seconds": bucket_seconds,
                "request_count": obs.request_count,
                "distinct_client_count": obs.distinct_clients,
                # NULL, not 0: no verified WAF DSM exposes blocked or error counts,
                # and 0 would assert a measurement we did not make.
                "blocked_count": None,
                "error_count": None,
                "endpoint_coverage_ratio": coverage,
                "completeness": completeness,
                "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
                # Provenance, collection time, watermark and the metric-semantics
                # identity are the same for every row of the bucket and live once
                # on its WafSourceBucket record.
                "query_provenance": {},
                **self._breakdown_values(obs.key, folded),
            }
            for obs, endpoint in tracked
        ]
        if rows:
            stmt = pg_insert(WafEndpointMetric)
            stmt = stmt.on_conflict_do_update(
                index_elements=["waf_endpoint_id", "bucket_start"],
                set_={
                    c: stmt.excluded[c]
                    for c in rows[0]
                    if c not in ("waf_endpoint_id", "bucket_start")
                },
            )
            await self.session.execute(stmt, rows)
            report.rows_written += len(rows)

        bucket_row = {
            "log_source_id": log_source.id,
            "bucket_start": start,
            "bucket_seconds": bucket_seconds,
            "profile_version": self.profile_version,
            "metric_semantics": semantics,
            "completeness": completeness,
            "request_count_measurement": measurement,
            "sparse_endpoint_rows": True,
            "endpoint_coverage_ratio": coverage,
            "total_events": batch.total_events,
            "parsed_events": parsed_total,
            "group_count": len(batch.groups),
            "groups_truncated": batch.truncated,
            "unusable_groups": unusable,
            "observed_endpoints": len(observations),
            "tracked_endpoints_observed": len(tracked_obs),
            "admitted_endpoints": len(decision.admitted),
            "below_admission_threshold": len(decision.below_threshold),
            "rejected_over_quota": len(decision.over_quota),
            "untracked_endpoints": len(decision.untracked),
            "untracked_requests": decision.untracked_requests,
            "admission_truncated": decision.truncated,
            "zero_filled_endpoints": 0,
            "in_maintenance": maintenance,
            **self._breakdown_bucket_facts(fetched.breakdowns),
            "collection_source": self._collection_source,
            "query_provenance": {
                "collector": COLLECTOR_NAME,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "provider": type(self.provider).__name__,
                "request_count_aggregation": self.provider.event_count_aggregation,
                "admission_quota": decision.quota,
                "ariel_searches": batch.ariel_searches,
                "aql": batch.query,
                # The query behind `total_events`, from which coverage -- and so
                # the measurement semantics of every count in this bucket -- is
                # derived.
                "coverage_aql": batch.total_query,
                "breakdowns": {
                    b.value: {
                        "status": r.status.value,
                        "aql": r.batch.query if r.batch is not None else None,
                        "groups": len(r.batch.groups) if r.batch is not None else None,
                        "max_groups": self.settings.waf_max_breakdown_groups_per_bucket,
                        "error_class": r.error_class,
                        # Source-IP requests whose value is not an address: not
                        # attributed to any client, so absent from client_events.
                        **(
                            {"unattributed_requests": sum(
                                c.unattributed for c in folded.clients.values()
                            )}
                            if b is WafBreakdown.SOURCE_IP and folded.clients is not None
                            else {}
                        ),
                    }
                    for b, r in sorted(fetched.breakdowns.items())
                },
            },
            "collection_duration_ms": duration_ms,
            "collected_at": collected_at,
            "watermark_at": watermark_at,
        }
        stmt_bucket = pg_insert(WafSourceBucket).values([bucket_row])
        stmt_bucket = stmt_bucket.on_conflict_do_update(
            index_elements=["log_source_id", "bucket_start"],
            set_={
                c: stmt_bucket.excluded[c]
                for c in bucket_row
                if c not in ("log_source_id", "bucket_start")
            },
        )
        await self.session.execute(stmt_bucket)

    def _fold_breakdowns(self, results: dict[WafBreakdown, _BreakdownResult]) -> _Folded:
        folded = _Folded()
        for breakdown, result in results.items():
            if result.batch is None:
                continue
            if breakdown is WafBreakdown.ACTION:
                folded.actions, _ = fold_actions(result.batch, self.mapping.action_outcome)
            elif breakdown is WafBreakdown.RESPONSE_CODE:
                folded.responses, _ = fold_response_codes(result.batch)
            else:
                folded.clients, _ = fold_source_ips(result.batch)
        return folded

    def _breakdown_values(self, key: EndpointKey, folded: _Folded) -> dict[str, object]:
        """One row's breakdown columns. NULL for a breakdown not measured.

        For a measured breakdown an endpoint it did not mention gets zeros: exact
        zeros when the search was whole, visible zeros when it was truncated --
        the bucket's measurement says which.
        """
        out: dict[str, object] = dict.fromkeys(
            (
                "action_events", "blocked_count", "unmapped_action_count",
                "response_code_events", "client_error_count", "server_error_count",
                "unclassified_response_count", "client_events", "top1_client_requests",
                "top5_client_requests", "client_sources", "top_clients",
            )
        )
        if folded.actions is not None:
            a = folded.actions.get(key) or ActionCounts()
            out.update(action_events=a.events, blocked_count=a.blocked,
                       unmapped_action_count=a.unmapped)
        if folded.responses is not None:
            r = folded.responses.get(key) or ResponseCounts()
            out.update(response_code_events=r.events, client_error_count=r.client_errors,
                       server_error_count=r.server_errors,
                       unclassified_response_count=r.unclassified)
        if folded.clients is not None:
            c = folded.clients.get(key) or ClientCounts()
            out.update(
                client_events=c.events, top1_client_requests=c.top1,
                top5_client_requests=c.top5, client_sources=c.sources,
                top_clients=c.contributors(
                    min_top_requests=self.settings.waf_concentration_min_client_requests
                ),
            )
        return out

    def _breakdown_bucket_facts(
        self, results: dict[WafBreakdown, _BreakdownResult]
    ) -> dict[str, object]:
        out: dict[str, object] = {}
        for breakdown, column in (
            (WafBreakdown.ACTION, "action"),
            (WafBreakdown.RESPONSE_CODE, "response_code"),
            (WafBreakdown.SOURCE_IP, "client"),
        ):
            result = results.get(breakdown)
            batch = result.batch if result is not None else None
            out[f"{column}_measurement"] = (
                result.status if result is not None else WafTelemetryStatus.NOT_COLLECTED
            )
            out[f"{column}_semantics"] = (
                self.breakdown_semantics(breakdown) if batch is not None else None
            )
            out[f"{column}_events"] = (
                sum(g.request_count for g in batch.groups) if batch is not None else None
            )
        return out

    async def _existing_endpoints(
        self, log_source: LogSource, names: set[str]
    ) -> dict[tuple[str | None, str, str], WafEndpoint]:
        """Current-version entities for the observed endpoints, active or not.

        One query bounded by the observed endpoint names, matched on the full
        key in memory. Matching in memory also sidesteps the `= NULL` trap: a
        NULL virtual host compares equal to None here, exactly as the
        NULLS NOT DISTINCT natural key treats it.
        """
        if not names:
            return {}
        rows = await self.session.scalars(
            select(WafEndpoint).where(
                WafEndpoint.log_source_id == log_source.id,
                WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
                WafEndpoint.normalized_endpoint.in_(names),
            )
        )
        return {(e.virtual_host, e.normalized_endpoint, e.http_method): e for e in rows}

    async def _active_endpoint_count(self, log_source: LogSource) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(WafEndpoint)
            .where(
                WafEndpoint.log_source_id == log_source.id,
                WafEndpoint.is_active.is_(True),
                WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
        )
        return int(count or 0)

    async def _retire_stale_endpoints(
        self, log_source: LogSource, now: datetime, last_collected: datetime | None
    ) -> int:
        """Stop tracking endpoints nobody has requested in a long time.

        Retirement is what bounds the tracked set. Rows and baselines are
        retained until retention expires. `tracked_until` is the last bucket this
        run collected while the endpoint was still tracked; a bucket collected
        after it -- even one earlier in time, by a lagging backfill -- no longer
        reads the endpoint's absence as zero. One statement, whatever the count.
        """
        cutoff = now - timedelta(days=self.settings.waf_endpoint_active_days)
        result = await self.session.execute(
            update(WafEndpoint)
            .where(
                WafEndpoint.log_source_id == log_source.id,
                WafEndpoint.is_active.is_(True),
                WafEndpoint.last_seen_at < cutoff,
            )
            .values(
                is_active=False,
                retired_at=now,
                tracked_until=(
                    last_collected if last_collected is not None else WafEndpoint.last_seen_at
                ),
            )
            .returning(WafEndpoint.id)
            .execution_options(synchronize_session="fetch")
        )
        return len(result.all())

    async def _get_or_create_watermark(
        self, instance: QRadarInstance, collector: str
    ) -> CollectionWatermark:
        wm = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == collector,
            )
        )
        if wm is None:
            wm = CollectionWatermark(instance_id=instance.id, collector=collector)
            self.session.add(wm)
            await self.session.flush()
        return wm
