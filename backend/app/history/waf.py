"""Pure WAF history folding and planner settings (HISTORY-BACKFILL §B.4.2, §B.8.4).

The live WAF writer performs admission and retirement one bucket at a time.  Repeating
that database path for a long history span would turn a 2,016-bucket step into thousands
of statements.  This module instead carries the bounded endpoint ledger in memory,
applies the same admission rule and per-bucket retirement oldest first, and returns one
payload per table.  Persistence is owned by ``WafEndpointCollector``.

No database, provider, settings import, or clock.  IDs and maintenance decisions are
inputs.  Raw WAF values exist only while batches are normalized and folded; only the
bounded endpoint rows and aggregate bucket/metric payloads leave the function.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from app.history.origin import HistoryOrigin
from app.history.planner import PlannerSettings
from app.models.enums import BucketCompleteness, WafMeasurement, WafTelemetryStatus
from app.providers.dto import WafObservationBatch
from app.waf.admission import AdmissionPolicy, decide_admission
from app.waf.normalize import (
    ENDPOINT_NORMALIZER_VERSION,
    normalize_endpoint,
    normalize_method,
    normalize_virtual_host,
)

INITIAL_CHUNK_BUCKETS = 288
MIN_CHUNK_BUCKETS = 1
MAX_GAPS = 20
ROW_TARGET_SHARE = 0.8


def waf_planner_settings(settings: Any, *, cadence_seconds: int) -> PlannerSettings:
    """The shared planner's WAF numbers; attributes are read from the caller's settings."""
    return PlannerSettings(
        bucket_seconds=int(settings.collection_interval_seconds),
        floor_margin_hours=int(settings.history_backfill_floor_margin_hours),
        row_cap=int(settings.ariel_multi_bucket_max_rows),
        row_target_share=ROW_TARGET_SHARE,
        initial_chunk_buckets=INITIAL_CHUNK_BUCKETS,
        min_chunk_buckets=MIN_CHUNK_BUCKETS,
        max_failures=int(settings.history_backfill_max_failures),
        max_lower_bound_share=float(settings.history_backfill_max_lower_bound_share),
        max_hours=int(settings.history_backfill_max_hours),
        max_gaps=MAX_GAPS,
        searches_per_tick=int(settings.history_backfill_searches_per_tick),
        cadence_seconds=cadence_seconds,
    )


@dataclass(frozen=True)
class HistoryEndpoint:
    id: uuid.UUID
    instance_id: uuid.UUID
    log_source_id: uuid.UUID
    virtual_host: str | None
    endpoint: str
    method: str
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
    retired_at: datetime | None
    tracked_since: datetime
    tracked_until: datetime | None
    baseline_rebuilt_at: datetime | None

    @property
    def key(self) -> tuple[str | None, str, str]:
        return self.virtual_host, self.endpoint, self.method


@dataclass(frozen=True)
class HistoryObservation:
    virtual_host: str | None
    endpoint: str
    method: str
    request_count: int
    distinct_clients: int | None
    record_count: int

    @property
    def key(self) -> tuple[str | None, str, str]:
        return self.virtual_host, self.endpoint, self.method


@dataclass(frozen=True)
class HistoryVolumeBucket:
    batch: WafObservationBatch
    collected_at: datetime
    duration_ms: int = 0
    search_provenance: Mapping[str, object] | None = None


@dataclass(frozen=True)
class WafHistoryFold:
    endpoints: tuple[dict[str, object], ...]
    buckets: tuple[dict[str, object], ...]
    metrics: tuple[dict[str, object], ...]
    endpoints_created: int
    endpoints_reactivated: int
    endpoints_retired: int
    untracked_endpoints: int
    untracked_requests: int


def _fold_groups(batch: WafObservationBatch) -> tuple[list[HistoryObservation], int]:
    merged: dict[tuple[str | None, str, str], HistoryObservation] = {}
    unusable = 0
    for group in batch.groups:
        endpoint = normalize_endpoint(group.raw_url)
        method = normalize_method(group.raw_method)
        if not endpoint.is_usable or endpoint.value is None or method is None:
            unusable += 1
            continue
        host = normalize_virtual_host(group.raw_virtual_host)
        key = host, endpoint.value, method
        current = merged.get(key)
        if current is None:
            merged[key] = HistoryObservation(
                host,
                endpoint.value,
                method,
                group.request_count,
                group.distinct_client_count,
                group.record_count,
            )
            continue
        clients = current.distinct_clients
        if group.distinct_client_count is not None:
            clients = max(clients or 0, group.distinct_client_count)
        merged[key] = replace(
            current,
            request_count=current.request_count + group.request_count,
            distinct_clients=clients,
            record_count=current.record_count + group.record_count,
        )
    return list(merged.values()), unusable


def _endpoint_values(endpoint: HistoryEndpoint) -> dict[str, object]:
    return {
        "id": endpoint.id,
        "instance_id": endpoint.instance_id,
        "log_source_id": endpoint.log_source_id,
        "virtual_host": endpoint.virtual_host,
        "normalized_endpoint": endpoint.endpoint,
        "http_method": endpoint.method,
        "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
        "first_seen_at": endpoint.first_seen_at,
        "last_seen_at": endpoint.last_seen_at,
        "is_active": endpoint.active,
        "retired_at": endpoint.retired_at,
        "tracked_since": endpoint.tracked_since,
        "tracked_until": endpoint.tracked_until,
        "baseline_rebuilt_at": endpoint.baseline_rebuilt_at,
    }


def fold_volume_history(
    buckets: Sequence[HistoryVolumeBucket],
    existing: Sequence[HistoryEndpoint],
    *,
    instance_id: uuid.UUID,
    log_source_id: uuid.UUID,
    profile_version: int,
    metric_semantics: str,
    collection_source: str,
    floor: datetime,
    policy: AdmissionPolicy,
    min_coverage: float,
    active_days: int,
    maintenance: Callable[[datetime], bool],
    id_factory: Callable[[], uuid.UUID],
) -> WafHistoryFold:
    """Replay a history step and return bulk table payloads.

    Input buckets must be strictly oldest first.  Retirement is applied after each
    bucket with that bucket as the reference; an endpoint retired there frees a ceiling
    slot for the next bucket, matching bucket-by-bucket collection without wall-clock
    retirement of the whole 28-day span.
    """
    ordered = sorted(buckets, key=lambda item: item.batch.window_start)
    if list(buckets) != ordered:
        raise ValueError("WAF history buckets must be oldest first")
    ledger = {endpoint.key: endpoint for endpoint in existing}
    metric_rows: list[dict[str, object]] = []
    bucket_rows: list[dict[str, object]] = []
    created = reactivated = retired = untracked_count = untracked_requests = 0
    previous: datetime | None = None

    for item in ordered:
        batch = item.batch
        start, end = batch.window_start, batch.window_end
        if previous is not None and start <= previous:
            raise ValueError("WAF history bucket starts must increase")
        previous = start
        observations, unusable = _fold_groups(batch)
        parsed_total = sum(observation.request_count for observation in observations)
        coverage = parsed_total / batch.total_events if batch.total_events > 0 else 1.0
        coverage = min(1.0, max(0.0, coverage))
        measurement = (
            WafMeasurement.LOWER_BOUND
            if batch.truncated or coverage < min_coverage
            else WafMeasurement.EXACT
        )
        completeness = (
            BucketCompleteness.COMPLETE
            if measurement is WafMeasurement.EXACT
            else BucketCompleteness.PARTIAL
        )

        active_count = sum(endpoint.active for endpoint in ledger.values())
        tracked = [
            observation
            for observation in observations
            if (current := ledger.get(observation.key)) is not None and current.active
        ]
        unseen = [
            observation
            for observation in observations
            if (current := ledger.get(observation.key)) is None or not current.active
        ]
        decision = decide_admission(unseen, active_count=active_count, policy=policy)
        untracked_count += len(decision.untracked)
        untracked_requests += decision.untracked_requests

        for observation in tracked:
            current = ledger[observation.key]
            if start > current.last_seen_at:
                ledger[observation.key] = replace(current, last_seen_at=start)
        for observation in decision.admitted:
            current = ledger.get(observation.key)
            if current is None:
                ledger[observation.key] = HistoryEndpoint(
                    id=id_factory(),
                    instance_id=instance_id,
                    log_source_id=log_source_id,
                    virtual_host=observation.virtual_host,
                    endpoint=observation.endpoint,
                    method=observation.method,
                    first_seen_at=start,
                    last_seen_at=start,
                    active=True,
                    retired_at=None,
                    tracked_since=start,
                    tracked_until=None,
                    baseline_rebuilt_at=None,
                )
                created += 1
            else:
                ledger[observation.key] = replace(
                    current,
                    last_seen_at=max(current.last_seen_at, start),
                    active=True,
                    retired_at=None,
                    tracked_since=start,
                    tracked_until=None,
                    baseline_rebuilt_at=None,
                )
                reactivated += 1

        stored = [*tracked, *decision.admitted]
        for observation in stored:
            endpoint = ledger[observation.key]
            metric_rows.append(
                {
                    "waf_endpoint_id": endpoint.id,
                    "bucket_start": start,
                    "bucket_seconds": int((end - start).total_seconds()),
                    "request_count": observation.request_count,
                    "distinct_client_count": observation.distinct_clients,
                    "blocked_count": None,
                    "error_count": None,
                    "action_events": None,
                    "unmapped_action_count": None,
                    "response_code_events": None,
                    "client_error_count": None,
                    "server_error_count": None,
                    "unclassified_response_count": None,
                    "client_events": None,
                    "top1_client_requests": None,
                    "top5_client_requests": None,
                    "client_sources": None,
                    "top_clients": None,
                    "endpoint_coverage_ratio": coverage,
                    "completeness": completeness.value,
                    "collection_source": None,
                    "query_provenance": {},
                    "collection_duration_ms": None,
                    "collected_at": None,
                    "watermark_at": None,
                    "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
                    "metric_semantics": None,
                }
            )

        provenance: dict[str, object] = {
            "collector": "waf_endpoint_metric",
            "history": True,
            "floor": floor.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "admission_quota": decision.quota,
            "aql": batch.query,
            "coverage_aql": batch.total_query,
        }
        provenance.update(item.search_provenance or {})
        bucket_rows.append(
            {
                "log_source_id": log_source_id,
                "bucket_start": start,
                "bucket_seconds": int((end - start).total_seconds()),
                "origin": HistoryOrigin.HISTORY.value,
                "profile_version": profile_version,
                "metric_semantics": metric_semantics,
                "completeness": completeness.value,
                "endpoint_coverage_ratio": coverage,
                "total_events": batch.total_events,
                "parsed_events": parsed_total,
                "group_count": len(batch.groups),
                "groups_truncated": batch.truncated,
                "unusable_groups": unusable,
                "observed_endpoints": len(observations),
                "tracked_endpoints_observed": len(tracked),
                "admitted_endpoints": len(decision.admitted),
                "below_admission_threshold": len(decision.below_threshold),
                "rejected_over_quota": len(decision.over_quota),
                "untracked_endpoints": len(decision.untracked),
                "untracked_requests": decision.untracked_requests,
                "admission_truncated": decision.truncated,
                "zero_filled_endpoints": 0,
                "request_count_measurement": measurement.value,
                "sparse_endpoint_rows": True,
                "in_maintenance": maintenance(start),
                "action_measurement": WafTelemetryStatus.NOT_COLLECTED.value,
                "action_semantics": None,
                "action_events": None,
                "response_code_measurement": WafTelemetryStatus.NOT_COLLECTED.value,
                "response_code_semantics": None,
                "response_code_events": None,
                "client_measurement": WafTelemetryStatus.NOT_COLLECTED.value,
                "client_semantics": None,
                "client_events": None,
                "collection_source": collection_source,
                "query_provenance": provenance,
                "collection_duration_ms": item.duration_ms,
                "collected_at": item.collected_at,
                "watermark_at": None,
            }
        )

        cutoff = start - timedelta(days=active_days)
        for key, endpoint in tuple(ledger.items()):
            if endpoint.active and endpoint.last_seen_at < cutoff:
                ledger[key] = replace(
                    endpoint,
                    active=False,
                    retired_at=start,
                    tracked_until=start,
                )
                retired += 1

    return WafHistoryFold(
        endpoints=tuple(_endpoint_values(endpoint) for endpoint in ledger.values()),
        buckets=tuple(bucket_rows),
        metrics=tuple(metric_rows),
        endpoints_created=created,
        endpoints_reactivated=reactivated,
        endpoints_retired=retired,
        untracked_endpoints=untracked_count,
        untracked_requests=untracked_requests,
    )
