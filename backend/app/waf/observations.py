"""Reconstruct what one WAF endpoint was observed to do in one bucket.

Pure: no I/O, no ORM. The engine and the baseline builder both read endpoint
behaviour through this module, so "what a missing row means" is decided in one
place.

Endpoint rows are sparse (docs/PHASE-B-WAF-BEHAVIOR.md section 29.4). A bucket's
`WafSourceBucket` record is what turns the *absence* of a row into a fact:

  * row present, bucket record present -- the row, under the bucket's
    measurement semantics;
  * row present, no bucket record (written before records existed) -- the row,
    exact only if COMPLETE, otherwise of unknown semantics;
  * no row, sparse EXACT bucket, endpoint tracked -- a measured zero;
  * no row, sparse LOWER_BOUND bucket, endpoint tracked -- unknown, lower bound 0;
  * no row, sparse bucket, endpoint not tracked -- not an observation;
  * no row, dense bucket (explicit zeros were written) -- it was not tracked;
  * no row, no bucket record (collection failed or never ran) -- nothing, and
    never a zero.

A zero is inferred only where collection would have written the row had there
been any traffic: a tracked endpoint is always written when the grouped result
mentions it, whatever its volume, so its absence from an exact result is zero.
An untracked endpoint may have been refused admission without a row, so its
absence says nothing.

Breakdown counts (Phase B.2) follow the same rule per breakdown, with that
breakdown's own measurement: a tracked endpoint with no row in a bucket whose
action breakdown was measured had no action-bearing requests (exactly zero when
the breakdown is EXACT, a visible zero when it is a LOWER_BOUND), and when the
breakdown was NOT_COLLECTED or FAILED every count is None -- never zero.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.enums import BucketCompleteness, WafMeasurement, WafTelemetryStatus
from app.waf.measurement import default_measurement

_MEASURED = (WafTelemetryStatus.EXACT, WafTelemetryStatus.LOWER_BOUND)


@dataclass(frozen=True)
class BreakdownStatus:
    """Per-breakdown completeness and identity of one bucket."""

    action: WafTelemetryStatus = WafTelemetryStatus.NOT_COLLECTED
    action_semantics: str | None = None
    response_code: WafTelemetryStatus = WafTelemetryStatus.NOT_COLLECTED
    response_code_semantics: str | None = None
    client: WafTelemetryStatus = WafTelemetryStatus.NOT_COLLECTED
    client_semantics: str | None = None

    @classmethod
    def of(cls, bucket) -> BreakdownStatus:  # type: ignore[no-untyped-def]
        def status(name: str) -> WafTelemetryStatus:
            value = getattr(bucket, f"{name}_measurement", None)
            return WafTelemetryStatus(value) if value is not None else (
                WafTelemetryStatus.NOT_COLLECTED
            )

        return cls(
            action=status("action"),
            action_semantics=getattr(bucket, "action_semantics", None),
            response_code=status("response_code"),
            response_code_semantics=getattr(bucket, "response_code_semantics", None),
            client=status("client"),
            client_semantics=getattr(bucket, "client_semantics", None),
        )


@dataclass(frozen=True)
class BreakdownCounts:
    """One endpoint's breakdown counts in one bucket. None: not measured."""

    action_events: int | None = None
    blocked: int | None = None
    unmapped_actions: int | None = None
    response_code_events: int | None = None
    client_errors: int | None = None
    server_errors: int | None = None
    unclassified_responses: int | None = None
    client_events: int | None = None
    top1_client_requests: int | None = None
    top5_client_requests: int | None = None
    client_sources: int | None = None
    top_clients: tuple[dict, ...] | None = None

    @classmethod
    def of(cls, row) -> BreakdownCounts:  # type: ignore[no-untyped-def]
        top = getattr(row, "top_clients", None)
        return cls(
            action_events=getattr(row, "action_events", None),
            blocked=getattr(row, "blocked_count", None),
            unmapped_actions=getattr(row, "unmapped_action_count", None),
            response_code_events=getattr(row, "response_code_events", None),
            client_errors=getattr(row, "client_error_count", None),
            server_errors=getattr(row, "server_error_count", None),
            unclassified_responses=getattr(row, "unclassified_response_count", None),
            client_events=getattr(row, "client_events", None),
            top1_client_requests=getattr(row, "top1_client_requests", None),
            top5_client_requests=getattr(row, "top5_client_requests", None),
            client_sources=getattr(row, "client_sources", None),
            top_clients=tuple(top) if top is not None else None,
        )

    @classmethod
    def absent(cls, status: BreakdownStatus) -> BreakdownCounts:
        """A tracked endpoint with no row: zero of every measured breakdown."""
        action = 0 if status.action in _MEASURED else None
        code = 0 if status.response_code in _MEASURED else None
        client = 0 if status.client in _MEASURED else None
        return cls(
            action_events=action, blocked=action, unmapped_actions=action,
            response_code_events=code, client_errors=code, server_errors=code,
            unclassified_responses=code,
            client_events=client, top1_client_requests=client, top5_client_requests=client,
            client_sources=client, top_clients=None,
        )

    def masked(self, status: BreakdownStatus) -> BreakdownCounts:
        """Drop counts of a breakdown the bucket does not vouch for."""
        action = status.action in _MEASURED
        code = status.response_code in _MEASURED
        client = status.client in _MEASURED
        return BreakdownCounts(
            action_events=self.action_events if action else None,
            blocked=self.blocked if action else None,
            unmapped_actions=self.unmapped_actions if action else None,
            response_code_events=self.response_code_events if code else None,
            client_errors=self.client_errors if code else None,
            server_errors=self.server_errors if code else None,
            unclassified_responses=self.unclassified_responses if code else None,
            client_events=self.client_events if client else None,
            top1_client_requests=self.top1_client_requests if client else None,
            top5_client_requests=self.top5_client_requests if client else None,
            client_sources=self.client_sources if client else None,
            top_clients=self.top_clients if client else None,
        )


@dataclass(frozen=True)
class BucketFacts:
    """The source-level record of one collected bucket."""

    bucket_start: datetime
    bucket_seconds: int
    completeness: BucketCompleteness
    endpoint_coverage_ratio: float
    metric_semantics: str | None
    in_maintenance: bool
    measurement: WafMeasurement | None
    sparse: bool
    breakdowns: BreakdownStatus = BreakdownStatus()
    #: Novelty inputs: whether discovery and admission were whole. Default to
    #: "not whole" so a caller that did not load them can never claim novelty.
    groups_truncated: bool = True
    admission_truncated: bool = True
    profile_version: int | None = None

    @classmethod
    def of(cls, bucket) -> BucketFacts:  # type: ignore[no-untyped-def]
        """From a `WafSourceBucket` row or any object with its attributes."""
        measurement = bucket.request_count_measurement
        return cls(
            bucket_start=bucket.bucket_start,
            bucket_seconds=bucket.bucket_seconds,
            completeness=BucketCompleteness(bucket.completeness),
            endpoint_coverage_ratio=bucket.endpoint_coverage_ratio,
            metric_semantics=bucket.metric_semantics,
            in_maintenance=bool(bucket.in_maintenance),
            measurement=WafMeasurement(measurement) if measurement is not None else None,
            sparse=bool(bucket.sparse_endpoint_rows),
            breakdowns=BreakdownStatus.of(bucket),
            groups_truncated=bool(getattr(bucket, "groups_truncated", True)),
            admission_truncated=bool(getattr(bucket, "admission_truncated", True)),
            profile_version=getattr(bucket, "profile_version", None),
        )


@dataclass(frozen=True)
class RowFacts:
    """One stored `waf_endpoint_metric` row."""

    bucket_start: datetime
    bucket_seconds: int
    request_count: int
    distinct_client_count: int | None
    completeness: BucketCompleteness
    endpoint_coverage_ratio: float
    metric_semantics: str | None
    normalizer_version: int
    counts: BreakdownCounts = BreakdownCounts()

    @classmethod
    def of(cls, row) -> RowFacts:  # type: ignore[no-untyped-def]
        return cls(
            bucket_start=row.bucket_start,
            bucket_seconds=row.bucket_seconds,
            request_count=row.request_count,
            distinct_client_count=row.distinct_client_count,
            completeness=BucketCompleteness(row.completeness),
            endpoint_coverage_ratio=row.endpoint_coverage_ratio,
            metric_semantics=row.metric_semantics,
            normalizer_version=row.normalizer_version,
            counts=BreakdownCounts.of(row),
        )


@dataclass(frozen=True)
class EndpointObservation:
    bucket_start: datetime
    bucket_seconds: int
    request_count: int
    distinct_client_count: int | None
    completeness: BucketCompleteness
    endpoint_coverage_ratio: float
    #: The identity `request_count` was produced under; None matches nothing.
    metric_semantics: str | None
    #: EXACT, LOWER_BOUND, or None when the semantics were never recorded.
    measurement: WafMeasurement | None
    #: The maintenance decision recorded at collection, or None when no bucket
    #: record exists and the caller must apply the rule itself.
    in_maintenance: bool | None
    normalizer_version: int
    #: No row existed; the value was reconstructed from the bucket record.
    inferred: bool = False
    #: Per-breakdown completeness and identity, from the bucket record.
    breakdowns: BreakdownStatus = BreakdownStatus()
    counts: BreakdownCounts = BreakdownCounts()

    @property
    def bucket_end(self) -> datetime:
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    @property
    def is_complete(self) -> bool:
        return self.completeness == BucketCompleteness.COMPLETE


def observe(
    row: RowFacts | None,
    bucket: BucketFacts | None,
    *,
    tracked: bool,
    normalizer_version: int,
) -> EndpointObservation | None:
    """One endpoint in one bucket, or None when nothing was observed."""
    if row is not None:
        if bucket is None:
            return EndpointObservation(
                bucket_start=row.bucket_start,
                bucket_seconds=row.bucket_seconds,
                request_count=row.request_count,
                distinct_client_count=row.distinct_client_count,
                completeness=row.completeness,
                endpoint_coverage_ratio=row.endpoint_coverage_ratio,
                metric_semantics=row.metric_semantics,
                measurement=default_measurement(row.completeness),
                in_maintenance=None,
                normalizer_version=row.normalizer_version,
            )
        return EndpointObservation(
            bucket_start=row.bucket_start,
            bucket_seconds=row.bucket_seconds,
            request_count=row.request_count,
            distinct_client_count=row.distinct_client_count,
            completeness=row.completeness,
            endpoint_coverage_ratio=row.endpoint_coverage_ratio,
            # Rows written since 0009 inherit the identity of the bucket they
            # were collected with; older rows carry their own.
            metric_semantics=row.metric_semantics or bucket.metric_semantics,
            measurement=bucket.measurement,
            in_maintenance=bucket.in_maintenance,
            normalizer_version=row.normalizer_version,
            breakdowns=bucket.breakdowns,
            counts=row.counts.masked(bucket.breakdowns),
        )
    if bucket is None or not bucket.sparse or not tracked:
        return None
    exact = bucket.measurement is WafMeasurement.EXACT
    return EndpointObservation(
        bucket_start=bucket.bucket_start,
        bucket_seconds=bucket.bucket_seconds,
        request_count=0,
        # Zero requests means zero clients -- but only when the zero is exact.
        distinct_client_count=0 if exact else None,
        completeness=bucket.completeness,
        endpoint_coverage_ratio=bucket.endpoint_coverage_ratio,
        metric_semantics=bucket.metric_semantics,
        measurement=bucket.measurement,
        in_maintenance=bucket.in_maintenance,
        normalizer_version=normalizer_version,
        inferred=True,
        breakdowns=bucket.breakdowns,
        counts=BreakdownCounts.absent(bucket.breakdowns),
    )


def endpoint_series(
    rows: Iterable[RowFacts],
    buckets: Mapping[datetime, BucketFacts],
    *,
    tracked_at: Callable[[datetime], bool],
    normalizer_version: int,
    start: datetime,
    end: datetime,
) -> list[EndpointObservation]:
    """Every observation of one endpoint with `start <= bucket_start < end`, oldest first.

    Only buckets in which the endpoint was tracked contribute, explicit rows
    included: after a retirement and reactivation, rows from the earlier tracking
    period no longer have the zeros that belonged with them, and training on the
    non-zero half of a period would bias the baseline upwards.
    """
    by_start = {r.bucket_start: r for r in rows if start <= r.bucket_start < end}
    starts = set(by_start)
    starts.update(
        s for s, b in buckets.items() if b.sparse and start <= s < end and s not in by_start
    )
    out: list[EndpointObservation] = []
    for s in sorted(starts):
        if not tracked_at(s):
            continue
        found = observe(
            by_start.get(s),
            buckets.get(s),
            tracked=True,
            normalizer_version=normalizer_version,
        )
        if found is not None:
            out.append(found)
    return out
