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

**One keyed search per kind per tick** (docs/SEARCH-CADENCE.md section SC.5). A
tick covers every complete bucket since the watermark -- at most one cadence plus
the backfill bound -- with one keyed volume search and one keyed search per
planned breakdown (`get_waf_observations_bucketed`, `get_waf_breakdown_bucketed`).
The per-bucket top-N cut happens in the fold (`app.waf.bucketed`). A kind whose
keyed search was truncated keeps nothing for that window: the window is split in
halves of whole buckets and that kind is searched again for each half, down to a
single bucket, where the search is exactly the per-bucket one with its own
truncation semantics. The other kinds keep what they already got. Each bucket is
then persisted by `_persist_interval`, unchanged.

**Ariel budget.** The whole tick's searches run under one global WAF Ariel permit
(`app.services.waf_ariel_budget`). A tick that cannot get one collects nothing:
the watermark stays where it was and the next run retries. Nothing is written
for it -- in particular, no zero.

**Caps and failures.** A tick sends at most `search_cadence_max_searches_per_tick`
searches (a keyed volume search is one search; it issues two Ariel statements).
On reaching the cap, or on a provider failure of a volume search, only the
contiguous run of buckets for which *every* kind completed is written and the
watermark stops before the first bucket that did not. A failure is not split
around: whether a wide search fails because it is wide is a question for live
validation (docs/SEARCH-CADENCE.md section SC.6). A failed keyed breakdown is
recorded FAILED for every bucket it covered, exactly as the per-bucket search
records it, and does not stop the tick.

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

import dataclasses
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Table, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import in_maintenance
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.history import (
    HistoryFamily,
    HistoryOrigin,
    is_history_clause,
    marker_values,
)
from app.history.planner import (
    ChunkResult,
    ScanOutcome,
    ScanSpanResult,
    ScopeProgress,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    interpret_scan,
    plan_step,
    settle_step,
)
from app.history.state import HistoryTrigger

# The WAF adapter is imported from its submodule: ``app.history`` must not re-export it
# (``app.models.history_backfill`` imports ``app.history`` while ``app.models`` loads).
from app.history.waf import (
    HistoryEndpoint,
    HistoryVolumeBucket,
    WafHistoryFold,
    fold_volume_history,
    waf_planner_settings,
)
from app.models.enums import (
    BucketCompleteness,
    SearchFamily,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import (
    WafBreakdownBatch,
    WafObservationBatch,
    WafObservationBucketBatch,
)
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService
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
    #: Provider search calls made this run: one keyed volume search counts once
    #: although it issues two Ariel statements. This is what the per-tick cap
    #: `search_cadence_max_searches_per_tick` counts.
    searches_sent: int = 0
    #: Keyed searches that were truncated and therefore split.
    splits: int = 0
    #: A volume search failed (not truncated) and ended the tick; the class name.
    provider_failure_class: str | None = None
    #: The per-tick search cap stopped the tick before every kind completed.
    search_cap_reached: bool = False
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


@dataclass(frozen=True)
class WafHistoryStep:
    progress: ScopeProgress
    reason: str
    trigger: HistoryTrigger | None = None
    rows_written: int = 0
    searches: int = 0
    permit_unavailable: bool = False


@dataclass(frozen=True)
class WafBreakdownHistoryStep:
    progress: ScopeProgress
    reason: str
    trigger: HistoryTrigger | None = None
    searches: int = 0
    buckets_updated: int = 0
    metrics_updated: int = 0
    permit_unavailable: bool = False


@dataclass(frozen=True)
class _SearchMeta:
    """Which search produced a bucket's result, for its provenance."""

    window_start: datetime
    window_end: datetime
    bucket_count: int
    split_depth: int
    duration_ms: int
    collected_at: datetime

    def provenance(self) -> dict[str, object]:
        return {
            "search_window_start": self.window_start.isoformat(),
            "search_window_end": self.window_end.isoformat(),
            "search_bucket_count": self.bucket_count,
            "split_depth": self.split_depth,
        }


@dataclass
class _BreakdownResult:
    """One breakdown's outcome for one bucket."""

    status: WafTelemetryStatus
    batch: WafBreakdownBatch | None = None
    error_class: str | None = None
    search: _SearchMeta | None = None


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
    #: The keyed volume search behind this bucket; None for a bucket fetched by
    #: the control path.
    search: _SearchMeta | None = None


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
            cadence = await SearchCadenceService(self.session, self.settings).get(
                SearchFamily.WAF_COLLECTION
            )
            cadence_buckets = max(1, cadence.cadence_seconds // interval)
            intervals = self._intervals_to_collect(
                watermark,
                current_boundary,
                interval,
                max_intervals=cadence_buckets + self.settings.collection_max_backfill_intervals,
            )

            fetched_by_start = await self._fetch_window_under_permit(
                log_source, intervals, interval, report
            )

            last_done = watermark.watermark_at
            collected = 0
            for start in intervals:
                end = start + timedelta(seconds=interval)
                fetched = fetched_by_start.get(start)
                if fetched is None:
                    # This bucket, or a search kind behind it, did not complete:
                    # nothing is written for it or any later bucket, and the
                    # watermark stays on the last bucket that was, so the next
                    # run resumes exactly here.
                    break
                await self._persist_interval(
                    log_source, start, end, fetched, watermark_at=last_done, report=report,
                )
                # Only advances past an interval that completed.
                last_done = start
                collected += 1

            watermark.watermark_at = last_done
            watermark.last_run_at = now
            watermark.intervals_collected += collected
            failed = report.provider_failure_class is not None
            if (collected and not failed) or not intervals:
                # A run that collected nothing because capacity was exhausted
                # has proven nothing about the source; it neither clears nor
                # adds to its failure record. Neither does one that a provider
                # failure ended early.
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
                "last_run_searches_sent": report.searches_sent,
                "last_run_splits": report.splits,
                "last_run_duration_ms": report.duration_ms,
                "last_run_intervals": collected,
                "last_run_intervals_due": len(intervals),
                "last_run_permit_wait_ms": report.permit_wait_ms,
            }
            if report.capacity_unavailable:
                metadata["last_capacity_skip_at"] = now.isoformat()
            if report.provider_failure_class is not None:
                metadata["last_provider_failure_class"] = report.provider_failure_class
            if (collected and not failed) or not intervals:
                metadata["last_success_at"] = now.isoformat()
            watermark.collection_metadata = metadata

            report.retired = await self._retire_stale_endpoints(log_source, now, last_done)
            await self.session.flush()
            return report

    def _intervals_to_collect(
        self,
        watermark: CollectionWatermark,
        current_boundary: datetime,
        interval: int,
        *,
        max_intervals: int,
    ) -> list[datetime]:
        """Complete buckets after the watermark, at most one cadence plus backfill."""
        if watermark.watermark_at is None:
            start = current_boundary - timedelta(seconds=interval)
            return [start] if start < current_boundary else []
        cursor = watermark.watermark_at + timedelta(seconds=interval)
        out: list[datetime] = []
        while cursor < current_boundary and len(out) < max_intervals:
            out.append(cursor)
            cursor += timedelta(seconds=interval)
        return out

    async def _fetch_window_under_permit(
        self,
        log_source: LogSource,
        intervals: list[datetime],
        interval: int,
        report: WafCollectionReport,
    ) -> dict[datetime, _Fetched]:
        """The tick's searches under one global permit; nothing without one."""
        if not intervals:
            return {}
        if self.ariel_budget is None:
            return await self._fetch_window(log_source, intervals, interval, report)
        async with self.ariel_budget.permit() as permit:
            if permit is None:
                report.capacity_unavailable = True
                return {}
            report.permit_wait_ms += int(permit.waited_seconds * 1000)
            return await self._fetch_window(log_source, intervals, interval, report)

    async def _fetch_window(
        self,
        log_source: LogSource,
        intervals: list[datetime],
        interval: int,
        report: WafCollectionReport,
    ) -> dict[datetime, _Fetched]:
        """Search the window once per kind, splitting a kind that was truncated.

        Returns the buckets for which every kind completed. The caller writes
        only the contiguous run of them from the front of the window.
        """
        observations: dict[datetime, tuple[WafObservationBatch, _SearchMeta]] = {}
        breakdowns: dict[WafBreakdown, dict[datetime, _BreakdownResult]] = {
            b: {} for b in sorted(self.planned_breakdowns())
        }
        kinds: list[WafBreakdown | None] = [None, *breakdowns]
        failure: ProviderError | None = None
        capped = False

        async def search(
            pending: list[WafBreakdown | None], start: datetime, count: int, depth: int
        ) -> None:
            nonlocal failure, capped
            if failure is not None:
                return
            truncated: list[WafBreakdown | None] = []
            end = start + timedelta(seconds=interval * count)
            for kind in pending:
                if report.searches_sent >= self.settings.search_cadence_max_searches_per_tick:
                    capped = True
                    report.search_cap_reached = True
                    return
                report.searches_sent += 1
                began = self._clock()
                if kind is None:
                    try:
                        if count == 1:
                            batch = await self._observations_one(log_source, start, end)
                            report.ariel_searches += batch.ariel_searches
                            per_bucket = {start: batch}
                            was_truncated = False
                        else:
                            keyed = await self._observations_keyed(
                                log_source, start, end, interval
                            )
                            report.ariel_searches += keyed.ariel_searches
                            per_bucket, was_truncated = keyed.buckets, keyed.truncated
                    except ProviderError as exc:
                        # A lone bucket that fails writes nothing; the buckets
                        # complete before it are still kept.
                        failure = exc
                        report.provider_failure_class = type(exc).__name__
                        return
                    finished = self._clock()
                    if was_truncated:
                        truncated.append(kind)
                        report.splits += 1
                        continue
                    meta = _SearchMeta(
                        start, end, count, depth,
                        int((finished - began).total_seconds() * 1000), finished,
                    )
                    for bucket_start, batch in per_bucket.items():
                        observations[bucket_start] = (batch, meta)
                    continue

                results, was_truncated, searches = await self._breakdown_search(
                    log_source, start, end, interval, count, kind
                )
                report.ariel_searches += searches
                finished = self._clock()
                if was_truncated:
                    truncated.append(kind)
                    report.splits += 1
                    continue
                meta = _SearchMeta(
                    start, end, count, depth,
                    int((finished - began).total_seconds() * 1000), finished,
                )
                for bucket_start, result in results.items():
                    result.search = meta
                    breakdowns[kind][bucket_start] = result
            if truncated:
                left = count // 2
                await search(truncated, start, left, depth + 1)
                if not capped and failure is None:
                    await search(
                        truncated,
                        start + timedelta(seconds=interval * left),
                        count - left,
                        depth + 1,
                    )

        await search(kinds, intervals[0], len(intervals), 0)
        if failure is not None and intervals[0] not in observations:
            # Nothing advanced, so the error surfaces and the run is recorded
            # as failed.
            raise failure

        out: dict[datetime, _Fetched] = {}
        for start in intervals:
            found = observations.get(start)
            results_here = {b: per.get(start) for b, per in breakdowns.items()}
            if found is None or any(r is None for r in results_here.values()):
                continue
            batch, meta = found
            metas = [meta, *(r.search for r in results_here.values() if r and r.search)]
            out[start] = _Fetched(
                batch=batch,
                breakdowns={b: r for b, r in results_here.items() if r is not None},
                duration_ms=sum(m.duration_ms for m in metas),
                collected_at=max(m.collected_at for m in metas),
                search=meta,
            )
        return out

    async def _observations_one(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> WafObservationBatch:
        """The per-bucket volume search: the base case of a split window."""
        return await self.provider.get_waf_observations(
            qradar_log_source_id=log_source.qradar_id,
            window_start=start,
            window_end=end,
            url_property=self.mapping.url,
            method_property=self.mapping.method,
            virtual_host_property=self.mapping.virtual_host,
            max_groups=self.settings.waf_max_groups_per_bucket,
        )

    async def _observations_keyed(
        self, log_source: LogSource, start: datetime, end: datetime, interval: int
    ) -> WafObservationBucketBatch:
        return await self.provider.get_waf_observations_bucketed(
            qradar_log_source_id=log_source.qradar_id,
            window_start=start,
            window_end=end,
            bucket_seconds=interval,
            url_property=self.mapping.url,
            method_property=self.mapping.method,
            virtual_host_property=self.mapping.virtual_host,
            max_groups=self.settings.waf_max_groups_per_bucket,
            max_rows=self.settings.ariel_multi_bucket_max_rows,
        )

    def _breakdown_property(self, breakdown: WafBreakdown) -> str | None:
        return {
            WafBreakdown.ACTION: self.mapping.action,
            WafBreakdown.RESPONSE_CODE: self.mapping.response_code,
            WafBreakdown.SOURCE_IP: None,
        }[breakdown]

    async def _breakdown_search(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        interval: int,
        count: int,
        breakdown: WafBreakdown,
    ) -> tuple[dict[datetime, _BreakdownResult], bool, int]:
        """One breakdown search over `count` buckets: results, truncated?, searches.

        A single bucket is the per-bucket search. A failure is recorded as FAILED
        for every bucket the search covered; the class name only, as a message
        can echo query text. A truncated keyed search returns no results.
        """
        starts = [start + timedelta(seconds=interval * i) for i in range(count)]
        common: dict[str, Any] = {
            "qradar_log_source_id": log_source.qradar_id,
            "url_property": self.mapping.url,
            "method_property": self.mapping.method,
            "virtual_host_property": self.mapping.virtual_host,
            "breakdown": breakdown.value,
            "breakdown_property": self._breakdown_property(breakdown),
            "max_groups": self.settings.waf_max_breakdown_groups_per_bucket,
        }
        try:
            if count == 1:
                single = await self.provider.get_waf_breakdown(
                    window_start=start, window_end=end, **common
                )
                batches, whole_truncated, searches = {start: single}, False, single.ariel_searches
            else:
                keyed = await self.provider.get_waf_breakdown_bucketed(
                    window_start=start,
                    window_end=end,
                    bucket_seconds=interval,
                    max_rows=self.settings.ariel_multi_bucket_max_rows,
                    **common,
                )
                batches, whole_truncated, searches = (
                    keyed.buckets, keyed.truncated, keyed.ariel_searches
                )
        except (ProviderError, ValueError) as exc:
            failed = _BreakdownResult(WafTelemetryStatus.FAILED, error_class=type(exc).__name__)
            return {s: dataclasses.replace(failed) for s in starts}, False, 0
        if whole_truncated:
            return {}, True, searches
        return (
            {
                bucket_start: _BreakdownResult(
                    WafTelemetryStatus.LOWER_BOUND if batch.truncated else WafTelemetryStatus.EXACT,
                    batch,
                )
                for bucket_start, batch in batches.items()
            },
            False,
            searches,
        )

    def _account_breakdowns(
        self, start: datetime, fetched: _Fetched, report: WafCollectionReport
    ) -> None:
        """Record, for a bucket about to be written, which breakdowns were not whole."""
        for breakdown, result in sorted(fetched.breakdowns.items()):
            if result.status is WafTelemetryStatus.FAILED:
                report.breakdown_failed_buckets.setdefault(breakdown.value, []).append(
                    start.isoformat()
                )
            elif result.status is WafTelemetryStatus.LOWER_BOUND:
                report.breakdown_lower_bound_buckets.setdefault(breakdown.value, []).append(
                    start.isoformat()
                )

    async def _fetch_per_bucket_control(
        self,
        log_source: LogSource,
        start: datetime,
        end: datetime,
        report: WafCollectionReport,
    ) -> _Fetched:
        """One bucket by the per-bucket searches, as collection did before T-036.

        Production never calls this. It is the control the equivalence tests
        compare the keyed path against: every persisted column must match.
        """
        started = self._clock()
        batch = await self._observations_one(log_source, start, end)
        report.ariel_searches += batch.ariel_searches
        report.searches_sent += 1
        breakdowns: dict[WafBreakdown, _BreakdownResult] = {}
        for breakdown in sorted(self.planned_breakdowns()):
            results, _, searches = await self._breakdown_search(
                log_source, start, end, 0, 1, breakdown
            )
            report.ariel_searches += searches
            report.searches_sent += 1
            breakdowns[breakdown] = results[start]
        collected_at = self._clock()
        return _Fetched(
            batch=batch,
            breakdowns=breakdowns,
            duration_ms=int((collected_at - started).total_seconds() * 1000),
            collected_at=collected_at,
        )

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
        self._account_breakdowns(start, fetched, report)
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
            **marker_values(HistoryFamily.WAF, HistoryOrigin.LIVE),
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
                # The searches behind this bucket (SC.5.5): the volume search at
                # the top level, each breakdown's own beneath. Absent from a
                # bucket the control path fetched.
                **(
                    {
                        **fetched.search.provenance(),
                        "bucket_key": f"starttime / {bucket_seconds * 1000}",
                        "breakdown_searches": {
                            b.value: r.search.provenance()
                            for b, r in sorted(fetched.breakdowns.items())
                            if r.search is not None
                        },
                    }
                    if fetched.search is not None
                    else {}
                ),
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
                # A live write never changes the marker of an existing row.
                if c not in ("log_source_id", "bucket_start", "origin")
            },
        )
        await self.session.execute(stmt_bucket)

    async def persist_history_volume(
        self,
        log_source: LogSource,
        buckets: list[HistoryVolumeBucket],
        *,
        floor: datetime,
    ) -> WafHistoryFold:
        """Fold and persist one history step with one statement per WAF table.

        The endpoint ledger is loaded once for the source, including retired endpoints,
        then ``fold_volume_history`` replays admission and per-bucket retirement in
        memory.  Bucket and metric conflicts do nothing: history can never replace a
        live row.  The surrounding pipeline transaction writes scope progress beside
        these rows, so a worker failure commits neither.
        """
        stored = (
            await self.session.scalars(
                select(WafEndpoint).where(
                    WafEndpoint.log_source_id == log_source.id,
                    WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
                )
            )
        ).all()
        existing = [
            HistoryEndpoint(
                id=row.id,
                instance_id=row.instance_id,
                log_source_id=row.log_source_id,
                virtual_host=row.virtual_host,
                endpoint=row.normalized_endpoint,
                method=row.http_method,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                active=row.is_active,
                retired_at=row.retired_at,
                tracked_since=row.tracked_since,
                tracked_until=row.tracked_until,
                baseline_rebuilt_at=row.baseline_rebuilt_at,
            )
            for row in stored
        ]
        folded = fold_volume_history(
            buckets,
            existing,
            instance_id=log_source.instance_id,
            log_source_id=log_source.id,
            profile_version=self.profile_version,
            metric_semantics=self.metric_semantics,
            collection_source=(
                "mock_history" if "mock" in type(self.provider).__name__.lower()
                else "ariel_history"
            ),
            floor=floor,
            policy=AdmissionPolicy(
                max_new_per_bucket=self.settings.waf_max_new_endpoints_per_bucket,
                max_active=self.settings.waf_max_active_endpoints_per_source,
                min_requests=self.settings.waf_min_admission_requests,
            ),
            min_coverage=self.settings.waf_min_endpoint_coverage,
            active_days=self.settings.waf_endpoint_active_days,
            maintenance=lambda at: in_maintenance(
                at,
                maintenance_mode=log_source.maintenance_mode,
                maintenance_until=log_source.maintenance_until,
            ),
            id_factory=uuid.uuid4,
        )

        if folded.endpoints:
            endpoint_insert = pg_insert(cast(Table, WafEndpoint.__table__))
            await self.session.execute(
                endpoint_insert.on_conflict_do_update(
                    constraint="uq_waf_endpoint_natural_key",
                    set_={
                        "first_seen_at": func.least(
                            WafEndpoint.first_seen_at, endpoint_insert.excluded.first_seen_at
                        ),
                        "last_seen_at": func.greatest(
                            WafEndpoint.last_seen_at, endpoint_insert.excluded.last_seen_at
                        ),
                        "is_active": endpoint_insert.excluded.is_active,
                        "retired_at": endpoint_insert.excluded.retired_at,
                        "tracked_since": endpoint_insert.excluded.tracked_since,
                        "tracked_until": endpoint_insert.excluded.tracked_until,
                        "baseline_rebuilt_at": endpoint_insert.excluded.baseline_rebuilt_at,
                    },
                ),
                list(folded.endpoints),
            )
        if folded.buckets:
            await self._history_insert_jsonb(
                "waf_source_bucket",
                list(folded.buckets),
                conflict="ON CONFLICT (log_source_id, bucket_start) DO NOTHING",
            )
        if folded.metrics:
            await self._history_insert_jsonb(
                "waf_endpoint_metric",
                list(folded.metrics),
                conflict="ON CONFLICT (waf_endpoint_id, bucket_start) DO NOTHING",
            )
        return folded

    async def _history_insert_jsonb(
        self,
        table: str,
        rows: list[dict[str, object]],
        *,
        conflict: str,
    ) -> None:
        """Insert an arbitrary-size aggregate payload as one PostgreSQL statement."""
        if not rows:
            return
        allowed = {
            (
                "waf_source_bucket",
                "ON CONFLICT (log_source_id, bucket_start) DO NOTHING",
            ),
            (
                "waf_endpoint_metric",
                "ON CONFLICT (waf_endpoint_id, bucket_start) DO NOTHING",
            ),
        }
        if (table, conflict) not in allowed:
            raise ValueError("unsupported WAF history bulk target")
        tables = {
            "waf_source_bucket": WafSourceBucket.__table__,
            "waf_endpoint_metric": WafEndpointMetric.__table__,
        }
        names = tuple(rows[0])
        if any(tuple(row) != names for row in rows):
            raise ValueError("history bulk rows must have identical columns")
        # The column names become SQL identifiers. Accept only names the target model
        # declares, so a fold that grows a key fails here instead of at the server.
        known_columns = set(tables[table].columns.keys())
        for name in names:
            if name not in known_columns:
                raise ValueError(
                    f"history bulk column {name!r} is not a column of {table}"
                )
        columns = ", ".join(f'"{name}"' for name in names)
        selected = ", ".join(f'history_rows."{name}"' for name in names)
        # The table and the conflict clause come from the closed set above, and every
        # column name from the model; only the JSON document is a bound value.
        statement = text(
            f'INSERT INTO "{table}" ({columns}) '  # noqa: S608
            f"SELECT {selected} "
            f"FROM jsonb_populate_recordset(NULL::{table}, CAST(:history_rows AS jsonb)) "
            f"AS history_rows {conflict}"
        )
        await self.session.execute(
            statement,
            {"history_rows": json.dumps(rows, default=self._history_json_default)},
        )

    @staticmethod
    def _history_json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        raise TypeError(f"unsupported history payload value {type(value).__name__}")

    async def history_volume_step(
        self,
        log_source: LogSource,
        progress: ScopeProgress,
        *,
        boundary: datetime,
        search_budget: int,
        seconds_left: float,
        cadence_seconds: int,
    ) -> WafHistoryStep:
        """Run one core-planned volume step under the global Ariel permit.

        The floor scan is the ordinary keyed volume search: its totals are the second
        statement of that search, and when it finds the floor its grouped buckets are
        reused immediately.  No separate floor query is issued.
        """
        planner = waf_planner_settings(self.settings, cadence_seconds=cadence_seconds)
        plan = plan_step(
            progress,
            now=self._clock(),
            end=boundary,
            budget=StepBudget(search_budget, seconds_left),
            settings=planner,
        )
        if plan.action is StepAction.FINISH:
            return WafHistoryStep(progress, plan.reason, plan.trigger)
        if plan.action is StepAction.WAIT:
            return WafHistoryStep(progress, plan.reason)

        async def run() -> WafHistoryStep:
            if plan.action is StepAction.SCAN:
                assert plan.scan_span is not None
                status, keyed, _ = await self._history_volume_search(
                    log_source, plan.scan_span, planner.bucket_seconds
                )
                totals: tuple[tuple[datetime, int], ...] = ()
                if keyed is not None:
                    totals = tuple(
                        (start, batch.total_events)
                        for start, batch in sorted(keyed.buckets.items())
                    )
                decision = interpret_scan(
                    progress,
                    [ScanSpanResult(plan.scan_span, status, totals)],
                    boundary,
                    planner,
                )
                updated = decision.progress
                if (
                    decision.outcome is not ScanOutcome.FLOOR_FOUND
                    or keyed is None
                    or updated.floor is None
                    or updated.floor >= plan.scan_span.end
                ):
                    return WafHistoryStep(
                        updated,
                        decision.reason,
                        decision.trigger,
                        searches=1 if status is not SearchStatus.NOT_RUN else 0,
                    )
                reuse_span = Span(updated.floor, plan.scan_span.end)
                result = self._history_chunk_result(
                    reuse_span, keyed, SearchStatus.COMPLETE, searches=0
                )
                settled = settle_step(updated, [result], end=boundary, settings=planner)
                scan_values = self._history_volume_buckets(keyed, (reuse_span,))
                folded = await self.persist_history_volume(
                    log_source, scan_values, floor=updated.floor
                )
                return WafHistoryStep(
                    settled.progress,
                    f"{decision.reason}; {settled.reason}",
                    settled.trigger,
                    rows_written=len(folded.metrics),
                    searches=1,
                )

            results: list[ChunkResult] = []
            keyed_by_span: dict[Span, WafObservationBucketBatch] = {}
            began = time.monotonic()
            for chunk in plan.chunks:
                if time.monotonic() - began >= seconds_left:
                    break
                status, keyed, searches = await self._history_volume_search(
                    log_source, chunk, planner.bucket_seconds
                )
                results.append(self._history_chunk_result(chunk, keyed, status, searches))
                if keyed is not None and status is SearchStatus.COMPLETE:
                    keyed_by_span[chunk] = keyed
                if status in (SearchStatus.FAILED, SearchStatus.TRUNCATED):
                    break
            if not results:
                return WafHistoryStep(progress, "waiting for the history time budget")
            settled = settle_step(progress, results, end=boundary, settings=planner)
            step_values: list[HistoryVolumeBucket] = []
            for span in settled.write_spans:
                keyed = keyed_by_span.get(span)
                if keyed is not None:
                    step_values.extend(self._history_volume_buckets(keyed, (span,)))
            rows = 0
            if step_values:
                assert settled.progress.floor is not None
                folded = await self.persist_history_volume(
                    log_source, step_values, floor=settled.progress.floor
                )
                rows = len(folded.metrics)
            return WafHistoryStep(
                settled.progress,
                settled.reason,
                settled.trigger,
                rows_written=rows,
                searches=sum(result.searches for result in results),
            )

        if self.ariel_budget is None:
            return await run()
        async with self.ariel_budget.permit() as permit:
            if permit is None:
                return WafHistoryStep(
                    progress,
                    "waiting for a global WAF Ariel permit",
                    permit_unavailable=True,
                )
            return await run()

    async def history_breakdown_step(
        self,
        log_source: LogSource,
        progress: ScopeProgress,
        *,
        boundary: datetime,
        search_budget: int,
        seconds_left: float,
        cadence_seconds: int,
    ) -> WafBreakdownHistoryStep:
        """Enrich finished HISTORY buckets, oldest first, with the core planner.

        §B.7.3 originally says newest first, but HB-6 deliberately uses the shared
        planner's oldest-first order.  Width adaptation, retries, gaps, the density gate,
        and time limits then stay common to every family.  No value depends on order; if
        time cuts the pass short, its older days are enriched rather than its newest.
        """
        kinds = tuple(sorted(self.planned_breakdowns()))
        if not kinds:
            return WafBreakdownHistoryStep(
                progress,
                "the profile plans no WAF breakdown history",
                HistoryTrigger.FINISHED_COMPLETE,
            )
        planner = waf_planner_settings(self.settings, cadence_seconds=cadence_seconds)
        # Each chunk costs one search per planned breakdown.  Let the core plan only
        # the number of chunks that fits the real search sub-budget.
        chunk_budget = search_budget // len(kinds)
        plan = plan_step(
            progress,
            now=self._clock(),
            end=boundary,
            budget=StepBudget(chunk_budget, seconds_left),
            settings=planner,
        )
        if plan.action is StepAction.FINISH:
            return WafBreakdownHistoryStep(progress, plan.reason, plan.trigger)
        if plan.action is StepAction.WAIT:
            return WafBreakdownHistoryStep(progress, plan.reason)
        if plan.action is StepAction.SCAN:
            raise ValueError("breakdown progress must start with its floor already known")

        async def run() -> WafBreakdownHistoryStep:
            began = time.monotonic()
            chunk_results: list[ChunkResult] = []
            completed: dict[Span, dict[datetime, dict[WafBreakdown, _BreakdownResult]]] = {}
            for chunk in plan.chunks:
                if time.monotonic() - began >= seconds_left:
                    break
                by_bucket: dict[datetime, dict[WafBreakdown, _BreakdownResult]] = {}
                status = SearchStatus.COMPLETE
                spent = 0
                rows = 0
                lower_bound_starts: set[datetime] = set()
                count = chunk.buckets(planner)
                for kind in kinds:
                    results, truncated, _ = await self._breakdown_search(
                        log_source,
                        chunk.start,
                        chunk.end,
                        planner.bucket_seconds,
                        count,
                        kind,
                    )
                    spent += 1
                    if truncated:
                        status = SearchStatus.TRUNCATED
                        break
                    if any(
                        result.status is WafTelemetryStatus.FAILED
                        for result in results.values()
                    ):
                        status = SearchStatus.FAILED
                        break
                    for start, result in results.items():
                        by_bucket.setdefault(start, {})[kind] = result
                        if result.batch is not None:
                            rows += len(result.batch.groups)
                            if result.status is WafTelemetryStatus.LOWER_BOUND:
                                lower_bound_starts.add(start)
                chunk_results.append(
                    ChunkResult(
                        chunk,
                        status,
                        rows=rows,
                        non_silent_buckets=sum(bool(items) for items in by_bucket.values()),
                        lower_bound_buckets=len(lower_bound_starts),
                        searches=spent,
                    )
                )
                if status is SearchStatus.COMPLETE:
                    completed[chunk] = by_bucket
                else:
                    break
            if not chunk_results:
                return WafBreakdownHistoryStep(
                    progress, "waiting for the history time budget"
                )
            settled = settle_step(progress, chunk_results, end=boundary, settings=planner)
            ready: dict[datetime, dict[WafBreakdown, _BreakdownResult]] = {}
            for span in settled.write_spans:
                ready.update(completed.get(span, {}))
            bucket_count = metric_count = 0
            if ready:
                bucket_count, metric_count = await self.persist_history_breakdowns(
                    log_source, ready
                )
            return WafBreakdownHistoryStep(
                settled.progress,
                settled.reason,
                settled.trigger,
                searches=sum(item.searches for item in chunk_results),
                buckets_updated=bucket_count,
                metrics_updated=metric_count,
            )

        if self.ariel_budget is None:
            return await run()
        async with self.ariel_budget.permit() as permit:
            if permit is None:
                return WafBreakdownHistoryStep(
                    progress,
                    "waiting for a global WAF Ariel permit",
                    permit_unavailable=True,
                )
            return await run()

    async def persist_history_breakdowns(
        self,
        log_source: LogSource,
        results: dict[datetime, dict[WafBreakdown, _BreakdownResult]],
    ) -> tuple[int, int]:
        """Bulk-update only breakdown columns of HISTORY rows.

        Endpoints are loaded once for the whole step.  A breakdown value is written only
        to an existing metric whose endpoint was tracked at that bucket.  Both UPDATEs
        join back to a HISTORY source bucket, so neither a conflicting live bucket nor a
        live metric at the same time can be changed.
        """
        if not results:
            return 0, 0
        starts = sorted(results)
        stored = (
            await self.session.execute(
                select(WafEndpoint, WafEndpointMetric)
                .join(
                    WafEndpointMetric,
                    WafEndpointMetric.waf_endpoint_id == WafEndpoint.id,
                )
                .join(
                    WafSourceBucket,
                    (WafSourceBucket.log_source_id == WafEndpoint.log_source_id)
                    & (WafSourceBucket.bucket_start == WafEndpointMetric.bucket_start),
                )
                .where(
                    WafEndpoint.log_source_id == log_source.id,
                    WafEndpointMetric.bucket_start.in_(starts),
                    is_history_clause(WafSourceBucket),
                )
            )
        ).all()
        by_bucket: dict[datetime, list[tuple[WafEndpoint, WafEndpointMetric]]] = {}
        for endpoint, metric in stored:
            if endpoint.tracked_at(metric.bucket_start):
                by_bucket.setdefault(metric.bucket_start, []).append((endpoint, metric))

        bucket_rows: list[dict[str, object]] = []
        metric_rows: list[dict[str, object]] = []
        for start in starts:
            per_kind = results[start]
            folded = self._fold_breakdowns(per_kind)
            bucket_rows.append(
                {
                    "log_source_id": log_source.id,
                    "bucket_start": start,
                    **self._breakdown_bucket_facts(per_kind),
                }
            )
            for endpoint, metric in by_bucket.get(start, []):
                key = (
                    endpoint.virtual_host,
                    endpoint.normalized_endpoint,
                    endpoint.http_method,
                )
                metric_rows.append(
                    {
                        "waf_endpoint_id": metric.waf_endpoint_id,
                        "bucket_start": start,
                        **self._breakdown_values(key, folded),
                    }
                )

        if bucket_rows:
            await self.session.execute(
                text(
                    "UPDATE waf_source_bucket AS target SET "
                    "action_measurement = data.action_measurement, "
                    "action_semantics = data.action_semantics, "
                    "action_events = data.action_events, "
                    "response_code_measurement = data.response_code_measurement, "
                    "response_code_semantics = data.response_code_semantics, "
                    "response_code_events = data.response_code_events, "
                    "client_measurement = data.client_measurement, "
                    "client_semantics = data.client_semantics, "
                    "client_events = data.client_events "
                    "FROM jsonb_populate_recordset(NULL::waf_source_bucket, "
                    "CAST(:history_rows AS jsonb)) AS data "
                    "WHERE target.log_source_id = data.log_source_id "
                    "AND target.bucket_start = data.bucket_start "
                    "AND target.origin = 'HISTORY'"
                ),
                {
                    "history_rows": json.dumps(
                        bucket_rows, default=self._history_json_default
                    )
                },
            )
        if metric_rows:
            await self.session.execute(
                text(
                    "UPDATE waf_endpoint_metric AS target SET "
                    "action_events = data.action_events, "
                    "blocked_count = data.blocked_count, "
                    "unmapped_action_count = data.unmapped_action_count, "
                    "response_code_events = data.response_code_events, "
                    "client_error_count = data.client_error_count, "
                    "server_error_count = data.server_error_count, "
                    "unclassified_response_count = data.unclassified_response_count, "
                    "client_events = data.client_events, "
                    "top1_client_requests = data.top1_client_requests, "
                    "top5_client_requests = data.top5_client_requests, "
                    "client_sources = data.client_sources, "
                    "top_clients = data.top_clients "
                    "FROM jsonb_populate_recordset(NULL::waf_endpoint_metric, "
                    "CAST(:history_rows AS jsonb)) AS data "
                    "WHERE target.waf_endpoint_id = data.waf_endpoint_id "
                    "AND target.bucket_start = data.bucket_start "
                    "AND EXISTS (SELECT 1 FROM waf_endpoint AS endpoint "
                    "JOIN waf_source_bucket AS bucket "
                    "ON bucket.log_source_id = endpoint.log_source_id "
                    "AND bucket.bucket_start = target.bucket_start "
                    "WHERE endpoint.id = target.waf_endpoint_id "
                    "AND bucket.origin = 'HISTORY')"
                ),
                {
                    "history_rows": json.dumps(
                        metric_rows, default=self._history_json_default
                    )
                },
            )
        return len(bucket_rows), len(metric_rows)

    async def _history_volume_search(
        self, log_source: LogSource, span: Span, bucket_seconds: int
    ) -> tuple[SearchStatus, WafObservationBucketBatch | None, int]:
        try:
            if span.end - span.start == timedelta(seconds=bucket_seconds):
                batch = await self._observations_one(log_source, span.start, span.end)
                return (
                    SearchStatus.COMPLETE,
                    WafObservationBucketBatch(
                        buckets={span.start: batch},
                        rows_returned=len(batch.groups) + int(batch.total_events > 0),
                        query=batch.query,
                        total_query=batch.total_query,
                        ariel_searches=batch.ariel_searches,
                    ),
                    1,
                )
            keyed = await self._observations_keyed(
                log_source, span.start, span.end, bucket_seconds
            )
        except ProviderError:
            return SearchStatus.FAILED, None, 1
        if keyed.truncated:
            return SearchStatus.TRUNCATED, None, 1
        return SearchStatus.COMPLETE, keyed, 1

    def _history_chunk_result(
        self,
        span: Span,
        keyed: WafObservationBucketBatch | None,
        status: SearchStatus,
        searches: int,
    ) -> ChunkResult:
        if keyed is None:
            return ChunkResult(span, status, searches=searches)
        buckets = {
            start: batch
            for start, batch in keyed.buckets.items()
            if span.start <= start < span.end
        }
        rows = sum(len(batch.groups) for batch in buckets.values())
        non_silent = sum(
            bool(batch.groups or batch.total_events) for batch in buckets.values()
        )
        lower_bound = 0
        for batch in buckets.values():
            parsed = sum(group.request_count for group in batch.groups)
            coverage = parsed / batch.total_events if batch.total_events > 0 else 1.0
            if batch.truncated or coverage < self.settings.waf_min_endpoint_coverage:
                lower_bound += 1
        return ChunkResult(
            span,
            status,
            rows=rows,
            non_silent_buckets=non_silent,
            lower_bound_buckets=lower_bound,
            searches=searches,
        )

    def _history_volume_buckets(
        self, keyed: WafObservationBucketBatch, spans: tuple[Span, ...]
    ) -> list[HistoryVolumeBucket]:
        collected_at = self._clock()
        out: list[HistoryVolumeBucket] = []
        for start, batch in sorted(keyed.buckets.items()):
            if not any(span.start <= start < span.end for span in spans):
                continue
            out.append(
                HistoryVolumeBucket(
                    batch=batch,
                    collected_at=collected_at,
                    search_provenance={
                        "search_window_start": min(span.start for span in spans).isoformat(),
                        "search_window_end": max(span.end for span in spans).isoformat(),
                        "search_bucket_count": sum(
                            int((span.end - span.start).total_seconds())
                            // self.settings.collection_interval_seconds
                            for span in spans
                        ),
                        "bucket_key": (
                            f"starttime / {self.settings.collection_interval_seconds * 1000}"
                        ),
                    },
                )
            )
        return out

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
        for breakdown, prefix in (
            (WafBreakdown.ACTION, "action"),
            (WafBreakdown.RESPONSE_CODE, "response_code"),
            (WafBreakdown.SOURCE_IP, "client"),
        ):
            result = results.get(breakdown)
            batch = result.batch if result is not None else None
            out[f"{prefix}_measurement"] = (
                result.status if result is not None else WafTelemetryStatus.NOT_COLLECTED
            )
            out[f"{prefix}_semantics"] = (
                self.breakdown_semantics(breakdown) if batch is not None else None
            )
            out[f"{prefix}_events"] = (
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
