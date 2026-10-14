"""Phase A behavioral-analytics API schemas.

Presentation shapes, deliberately distinct from both the ORM models and the
provider DTOs. Two rules run through all of them:

  * A value that was not measured is `None`, never 0 and never a placeholder.
    `percent_delta=None` on a newly observed contributor means "this value has
    no baseline to be a percentage of", which is a different fact from "it did
    not change".
  * Availability and completeness travel with the data, so a client can render
    "we could not measure this" instead of silently drawing an empty chart.

All timestamps are UTC. Timezone conversion is a presentation concern and
happens in the browser, against the user's selected timezone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DimensionAvailability,
    EvidenceStatus,
    RobustScoreStatus,
    Severity,
    WafAnomalyType,
)


class SubjectKind(StrEnum):
    """What an anomaly is about. A response shape, never persisted.

    The analyst list unifies several detector families through this one
    discriminator. A new family (design Phase D: IPS subjects) adds a member and
    a branch in the repository's union; it does not reshape the response.
    """

    LOG_SOURCE = "LOG_SOURCE"
    WAF_ENDPOINT = "WAF_ENDPOINT"


class WafEndpointRefOut(BaseModel):
    """The WAF endpoint an anomaly is about. Identity only, no measurements."""

    id: uuid.UUID
    normalized_endpoint: str
    http_method: str
    #: None when the source exposes no virtual host. Never an empty string.
    virtual_host: str | None = None
    #: Whether this WAF source can tell applications apart by host. False means
    #: the endpoint may combine traffic of different applications sharing a path.
    virtual_host_available: bool
    normalizer_version: int


class MetricBucketOut(BaseModel):
    """One time bucket of observed volume."""

    model_config = ConfigDict(from_attributes=True)

    bucket_start: datetime
    bucket_seconds: int
    event_count: int
    average_eps: float
    #: None when the bucket's collection path does not measure a peak. The
    #: column is NOT NULL with a 0.0 default, and the live Ariel aggregate never
    #: computes a peak, so serializing the column would publish an invented 0.
    peak_eps: float | None = None
    completeness: BucketCompleteness
    last_event_at: datetime | None = None


class InputRequirementOut(BaseModel):
    """What one measured input of a WAF detector must be for a verdict."""

    input: str
    role: str
    requirement: str


class DetectorCapabilityOut(BaseModel):
    """One named detector: what it needs, and whether anything supplies it.

    An anomaly type existing says nothing about whether it can fire. Clients
    must read coverage from here rather than infer it from the type enum.
    """

    domain: str
    detector_type: str
    #: OPERATIONAL, NOT_ENABLED, NOT_SCHEDULED, SYNTHETIC_ONLY or UNSUPPORTED.
    status: str
    required_telemetry: list[str] = Field(default_factory=list)
    required_baselines: list[str] = Field(default_factory=list)
    scheduled_task: str | None = None
    #: What stops the detector running on live data. None when nothing does.
    limitation: str | None = None
    #: WAF only: EXACT, or LOWER_BOUND_PROVES_INCREASE when an anomalous verdict
    #: may be proven from a count that is a lower bound. None for log sources.
    measurement_requirement: str | None = None
    #: WAF only: the setting that must be true before the detector's telemetry is
    #: collected. None when it needs no opt-in.
    enabling_setting: str | None = None
    #: What must be configured, beyond the setting, before it reaches a verdict.
    activation: str | None = None
    #: WAF only: the requirement on each measured input. Empty without inputs.
    input_requirements: list[InputRequirementOut] = Field(default_factory=list)


class BaselineCellOut(BaseModel):
    """The expected band for one (weekday, hour) seasonal cell."""

    model_config = ConfigDict(from_attributes=True)

    metric_name: str
    weekday: int
    hour: int
    median: float
    mad: float
    p05: float | None = None
    p95: float | None = None
    sample_count: int
    #: False means the cell exists but has too few samples to drive a verdict.
    #: The UI must render this as "still learning", never as a healthy band.
    is_reliable: bool
    completeness: float
    baseline_version: int
    computed_at: datetime | None = None


class SourceBehaviorOut(BaseModel):
    """Current observed-vs-expected behavior for one log source."""

    log_source_id: uuid.UUID
    name: str
    criticality: str
    #: None when no bucket has been collected yet.
    observed_eps: float | None = None
    #: None when the seasonal cell has no adequate baseline.
    expected_eps: float | None = None
    expected_low: float | None = None
    expected_high: float | None = None
    #: observed / expected. None when expected is zero — a ratio against nothing
    #: does not exist and must not be rendered as a large number.
    deviation_ratio: float | None = None
    state: AnomalyState
    baseline_sample_count: int = 0
    baseline_completeness: float = 0.0
    last_bucket_at: datetime | None = None
    #: The completeness of the latest bucket (`COMPLETE`, `PARTIAL` or
    #: `MISSING`), or None when no bucket exists. `observed_eps` comes from
    #: this bucket, so a client must not rank or present a non-`COMPLETE`
    #: value as a measurement.
    last_bucket_completeness: str | None = None
    last_event_at: datetime | None = None
    open_anomaly_count: int = 0


class AnomalyTransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_state: AnomalyState | None = None
    to_state: AnomalyState
    occurred_at: datetime
    bucket_start: datetime | None = None
    reason: str | None = None
    actor: str
    observed_value: float | None = None
    expected_value: float | None = None


class AnomalyListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: What the anomaly is about. Phase A rows are LOG_SOURCE.
    subject_kind: SubjectKind = SubjectKind.LOG_SOURCE
    #: For a WAF_ENDPOINT subject, the WAF log source that carries the endpoint.
    log_source_id: uuid.UUID
    #: Resolved by the route from the owning source. None only when the source
    #: row is gone, which the UI renders as the id rather than as a blank cell.
    log_source_name: str | None = None
    #: Set for a WAF_ENDPOINT subject, otherwise None.
    waf_endpoint: WafEndpointRefOut | None = None
    #: A Phase A type, or for a WAF subject the row's detector type.
    anomaly_type: AnomalyType | WafAnomalyType
    state: AnomalyState
    severity: Severity
    observed_value: float | None = None
    expected_value: float | None = None
    deviation_ratio: float | None = None
    robust_z: float | None = None
    absolute_delta: float | None = None
    consecutive_buckets: int = 0
    confidence: float | None = None
    detected_at: datetime
    opened_at: datetime | None = None
    anomaly_start: datetime | None = None
    anomaly_end: datetime | None = None
    resolved_at: datetime | None = None
    evidence_status: EvidenceStatus
    suppressed: bool = False
    #: WAF only: EXACT, or LOWER_BOUND when `observed_value` is "at least n".
    #: None for Phase A rows, which have no such distinction.
    observed_measurement: str | None = None
    #: Server-rendered detector reasoning, so the UI never reconstructs
    #: detection logic to explain a verdict.
    explanation: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_seconds(self) -> float | None:
        """How long the abnormal telemetry lasted, in seconds.

        A computed field rather than a plain property so it is actually
        serialized: every client would otherwise recompute this arithmetic, and
        the "still running" case (no end yet) is exactly where an independent
        reimplementation tends to substitute `now` and invent a duration.
        """
        if self.anomaly_start is None:
            return None
        end = self.anomaly_end or self.resolved_at
        return (end - self.anomaly_start).total_seconds() if end else None


class ContributorOut(BaseModel):
    """One (dimension, value) pair and its contribution to the change."""

    model_config = ConfigDict(from_attributes=True)

    dimension: str
    value: str
    label: str | None = None
    baseline_count: int
    anomaly_count: int
    absolute_delta: int
    #: None for a newly observed value: it has no baseline to be a percent of.
    percent_delta: float | None = None
    anomaly_share: float | None = None
    baseline_share: float | None = None
    contribution_share: float | None = None
    baseline_rank: int | None = None
    anomaly_rank: int | None = None
    rank: int
    is_new: bool
    is_disappeared: bool


class ExplanationDimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dimension: str
    availability: DimensionAvailability
    #: Why a dimension is UNAVAILABLE or FAILED. Sanitized.
    detail: str | None = None
    baseline_distinct_count: int | None = None
    anomaly_distinct_count: int | None = None
    cardinality_ratio: float | None = None
    new_value_count: int = 0
    disappeared_value_count: int = 0
    baseline_top_share: float | None = None
    anomaly_top_share: float | None = None
    truncated: bool = False
    contributors: list[ContributorOut] = Field(default_factory=list)


class ExplanationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: EvidenceStatus
    error: str | None = None
    anomaly_window_start: datetime
    anomaly_window_end: datetime
    baseline_window_start: datetime
    baseline_window_end: datetime
    comparison_strategy: str
    anomaly_total_events: int
    baseline_total_events: int
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    collection_duration_ms: int | None = None
    #: The AQL behind each aggregate, plus row counts and truncation flags.
    #: Non-secret by construction: no token, no headers, no response bodies.
    query_provenance: dict = Field(default_factory=dict)
    schema_version: int
    dimensions: list[ExplanationDimensionOut] = Field(default_factory=list)


class DetectionDetailOut(BaseModel):
    """The detector's own working, as it stood when the verdict was reached.

    Assembled from the stored evidence dict by an explicit whitelist rather
    than serialized wholesale: that column is a detector-owned payload whose
    keys vary per detector, and forwarding it verbatim would make any future
    field a silent, unreviewed addition to a public API response.

    Every field is optional. An older anomaly, or one from a detector that does
    not compute a given quantity, simply has no value for it — which the UI
    must render as "not recorded", never as 0.
    """

    #: The detector's own sentence explaining the verdict.
    reason: str | None = None
    #: The expected band the observation was judged against.
    expected_low: float | None = None
    expected_high: float | None = None
    #: The robust z-score threshold that had to be cleared.
    threshold: float | None = None
    baseline_sample_count: int | None = None
    baseline_completeness: float | None = None
    baseline_version: int | None = None

    observed_eps: float | None = None
    expected_eps: float | None = None
    observed_events: float | None = None
    expected_events: float | None = None
    absolute_delta_events: float | None = None
    bucket_seconds: float | None = None

    ratio: float | None = None
    #: "expected_zero" when the baseline expected no traffic, so the ratio does
    #: not exist and the absolute-delta guard carried the verdict alone.
    ratio_basis: str | None = None

    #: DEGENERATE means MAD was zero and the z-score below is an artefact of the
    #: floored scale. The verdict then rests on `fallback_bound`, which is
    #: sound but weaker evidence — the UI must say so next to the confidence.
    robust_score_status: RobustScoreStatus | None = None
    robust_z: float | None = None
    fallback_bound: float | None = None


class WafDetectionDetailOut(BaseModel):
    """The WAF detector's working, projected from the stored evidence by whitelist.

    Same rule as `DetectionDetailOut`: named keys only, from the two known levels
    of the evidence dict. The evidence also holds contributor lists and the
    detector's entity block; none of that is forwarded here. A missing value is
    None, never 0.
    """

    reason: str | None = None
    expected_low: float | None = None
    expected_high: float | None = None
    threshold: float | None = None
    baseline_sample_count: int | None = None
    baseline_completeness: float | None = None
    baseline_version: int | None = None
    normalizer_version: int | None = None

    #: Volume detector: requests in the anomalous bucket and the baseline median.
    observed_requests: float | None = None
    expected_requests: float | None = None
    absolute_delta_requests: float | None = None
    #: Share of the source's events that carried a parseable endpoint.
    endpoint_coverage_ratio: float | None = None
    #: EXACT, or LOWER_BOUND when `observed_requests` is "at least n".
    request_count_measurement: str | None = None

    #: Ratio detectors: the observed and expected proportion of the endpoint's
    #: requests, and the counts the observed proportion was computed from.
    observed_proportion: float | None = None
    expected_proportion: float | None = None
    observed_numerator: float | None = None
    observed_denominator: float | None = None
    percentage_point_change: float | None = None

    ratio: float | None = None
    ratio_basis: str | None = None
    robust_score_status: RobustScoreStatus | None = None
    robust_z: float | None = None
    fallback_bound: float | None = None


class AnomalyDetailOut(AnomalyListItem):
    """Everything the investigation page needs for one anomaly."""

    baseline_version: int | None = None
    policy_version: int = 1
    transitions: list[AnomalyTransitionOut] = Field(default_factory=list)
    #: None when evidence was never requested or has not yet been collected.
    explanation_package: ExplanationOut | None = None
    #: None when the anomaly recorded no structured detector evidence.
    #: A WAF subject carries the WAF projection; a log source the Phase A one.
    detection: WafDetectionDetailOut | DetectionDetailOut | None = None


class BehaviorSummaryOut(BaseModel):
    """Fleet-level behavioral posture for the overview page."""

    open_anomalies: int = 0
    spikes: int = 0
    drops: int = 0
    silent_sources: int = 0
    candidates: int = 0
    recovering: int = 0
    #: Sources whose seasonal cell has too little history to judge. Reported
    #: separately from healthy sources: an unbaselined source is an
    #: observability gap, not a clean bill of health.
    insufficient_data_sources: int = 0
    monitored_sources: int = 0
    #: Explanation jobs still queued, and jobs that ran and could not complete.
    #: Reported separately because a backlog is a capacity signal while a
    #: failure is a defect, and an investigation page with no evidence looks
    #: identical in both cases.
    evidence_pending: int = 0
    evidence_failed: int = 0
    recently_resolved: list[AnomalyListItem] = Field(default_factory=list)
    highest_deviation: list[AnomalyListItem] = Field(default_factory=list)


def detection_detail(details: dict[str, Any] | None) -> DetectionDetailOut | None:
    """Project a stored detector evidence dict onto the typed detection block.

    Reads named keys only, and reads nothing from anywhere but the two known
    levels (`details` and `details["extra"]`). Unrecognized keys are dropped
    rather than passed through, so a detector gaining a new internal fact does
    not thereby publish it.
    """
    if not details:
        return None
    extra = details.get("extra")
    facts: dict[str, Any] = extra if isinstance(extra, dict) else {}

    def num(source: dict[str, Any], key: str) -> float | None:
        value = source.get(key)
        # bool is an int subclass; a flag reaching a numeric field is a bug in
        # the detector, and coercing it to 1.0 would hide that.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def text(source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        return value if isinstance(value, str) else None

    sample_count = num(details, "sample_count")
    baseline_version = num(facts, "baseline_version")
    status = text(facts, "robust_score_status")

    return DetectionDetailOut(
        reason=text(details, "reason"),
        expected_low=num(details, "baseline_low"),
        expected_high=num(details, "baseline_high"),
        threshold=num(details, "threshold"),
        baseline_sample_count=int(sample_count) if sample_count is not None else None,
        baseline_completeness=num(facts, "baseline_completeness"),
        baseline_version=int(baseline_version) if baseline_version is not None else None,
        observed_eps=num(facts, "observed_eps"),
        expected_eps=num(facts, "expected_eps"),
        observed_events=num(facts, "observed_events"),
        expected_events=num(facts, "expected_events"),
        absolute_delta_events=num(facts, "absolute_delta_events"),
        bucket_seconds=num(facts, "bucket_seconds"),
        ratio=num(facts, "ratio"),
        ratio_basis=text(facts, "ratio_basis"),
        # An unrecognized status is dropped rather than guessed: claiming the
        # score was OK when we cannot tell would overstate the evidence.
        robust_score_status=(
            RobustScoreStatus(status)
            if status in set(RobustScoreStatus)
            else None
        ),
        robust_z=num(facts, "robust_z"),
        fallback_bound=num(facts, "fallback_bound"),
    )


class PagedAnomalies(BaseModel):
    items: list[AnomalyListItem]
    total: int
    limit: int
    offset: int


def _num(source: dict[str, Any], key: str) -> float | None:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    return value if isinstance(value, str) else None


def _section(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def waf_detection_detail(
    details: dict[str, Any] | None, *, normalizer_version: int | None = None
) -> WafDetectionDetailOut | None:
    """Project a stored WAF evidence dict onto the typed WAF detection block.

    Reads named keys only. The nested `observed` and `expected` sections of the
    ratio detectors are read by key too, so their contributor lists, entity block
    and provenance never reach the response.
    """
    if not details:
        return None
    extra = _section(details, "extra")
    observed = _section(extra, "observed")
    expected = _section(extra, "expected")

    sample_count = _num(details, "sample_count")
    version = _num(extra, "baseline_version")
    if version is None:
        version = _num(expected, "baseline_version")
    completeness = _num(extra, "baseline_completeness")
    if completeness is None:
        completeness = _num(expected, "completeness")
    status = _text(extra, "robust_score_status")
    measurement = _text(extra, "request_count_measurement")

    return WafDetectionDetailOut(
        reason=_text(details, "reason"),
        expected_low=_num(details, "baseline_low"),
        expected_high=_num(details, "baseline_high"),
        threshold=_num(details, "threshold"),
        baseline_sample_count=int(sample_count) if sample_count is not None else None,
        baseline_completeness=completeness,
        baseline_version=int(version) if version is not None else None,
        normalizer_version=normalizer_version,
        observed_requests=_num(extra, "observed_requests"),
        expected_requests=_num(extra, "expected_requests"),
        absolute_delta_requests=_num(extra, "absolute_delta_requests"),
        endpoint_coverage_ratio=_num(extra, "endpoint_coverage_ratio"),
        request_count_measurement=measurement if measurement in ("EXACT", "LOWER_BOUND") else None,
        observed_proportion=_num(observed, "proportion"),
        expected_proportion=_num(expected, "proportion"),
        observed_numerator=_num(observed, "numerator"),
        observed_denominator=_num(observed, "denominator"),
        percentage_point_change=_num(extra, "percentage_point_change"),
        ratio=_num(extra, "ratio"),
        ratio_basis=_text(extra, "ratio_basis"),
        robust_score_status=(
            RobustScoreStatus(status) if status in set(RobustScoreStatus) else None
        ),
        robust_z=_num(extra, "robust_z"),
        fallback_bound=_num(extra, "fallback_bound"),
    )


# ------------------------------------------------------------ WAF behavior
class WafLatestBucketOut(BaseModel):
    """The most recent bucket a WAF source collected, and how whole it was."""

    bucket_start: datetime
    bucket_seconds: int
    completeness: str
    #: Share of the source's events that carried a parseable endpoint.
    endpoint_coverage_ratio: float
    #: LOWER_BOUND when grouping was truncated: the ratio is then itself a floor.
    coverage_measurement: str
    #: EXACT, or LOWER_BOUND when endpoint request counts are "at least n".
    request_count_measurement: str
    total_events: int
    in_maintenance: bool
    groups_truncated: bool
    admission_truncated: bool


class WafAdmissionOverflowOut(BaseModel):
    """Endpoints refused tracking in the range. Counts over the collected buckets.

    The sums are None when no bucket was collected in the range: "nothing was
    refused" is a claim only a collected bucket can make.
    """

    buckets_in_range: int
    buckets_admission_truncated: int | None = None
    rejected_over_quota: int | None = None
    below_admission_threshold: int | None = None
    untracked_requests: int | None = None


class WafSourceBehaviorOut(BaseModel):
    """One WAF source: profile status, latest coverage, endpoint pressure."""

    log_source_id: uuid.UUID
    log_source_name: str
    product_label: str | None = None
    enabled: bool
    profile_version: int
    #: Validation status of the profile. Collection requires VALID for the
    #: current version; `validation_current` says whether that holds.
    validation_status: str
    validated_profile_version: int | None = None
    validated_at: datetime | None = None
    validation_current: bool
    virtual_host_available: bool
    #: None before the first bucket is collected.
    latest_bucket: WafLatestBucketOut | None = None
    active_endpoints: int
    total_endpoints: int
    active_endpoint_ceiling: int
    admission: WafAdmissionOverflowOut


class WafValidationFieldOut(BaseModel):
    """One logical field's validation outcome. Counts and ratios only."""

    field: str
    property_name: str | None = None
    status: str
    used_by_collection: bool
    populated_events: int | None = None
    populated_ratio: float | None = None
    distinct_values: int | None = None
    plausible_ratio: float | None = None
    sampled_events: int | None = None
    values_truncated: bool | None = None
    #: How many bounded sample values the report holds. The values are not served.
    discovered_value_count: int | None = None
    detail: str | None = None


class WafValidationReportOut(BaseModel):
    """The last validation report with no queries and no sampled values."""

    status: str | None = None
    reasons: list[str] = Field(default_factory=list)
    log_source_found: bool | None = None
    total_events: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    profile_version: int | None = None
    validated_at: str | None = None
    fields: list[WafValidationFieldOut] = Field(default_factory=list)


class WafSourceDetailOut(WafSourceBehaviorOut):
    #: None when the profile has never been validated.
    validation_report: WafValidationReportOut | None = None


class PagedWafSources(BaseModel):
    items: list[WafSourceBehaviorOut]
    total: int
    limit: int
    offset: int
    range_start: datetime
    range_end: datetime


class WafBaselineAdequacyOut(BaseModel):
    """How many seasonal cells of the request-count baseline can drive a verdict."""

    cells_total: int
    reliable_cells: int


class WafEndpointInventoryOut(BaseModel):
    id: uuid.UUID
    normalized_endpoint: str
    http_method: str
    virtual_host: str | None = None
    normalizer_version: int
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    retired_at: datetime | None = None
    tracked_since: datetime
    baseline: WafBaselineAdequacyOut
    baseline_rebuilt_at: datetime | None = None
    #: Worst state across the endpoint's detectors. None when no detector has
    #: evaluated it: unevaluated is not NORMAL.
    state: AnomalyState | None = None
    active_anomaly_count: int = 0


class PagedWafEndpoints(BaseModel):
    items: list[WafEndpointInventoryOut]
    total: int
    limit: int
    offset: int


class WafMetricBucketOut(BaseModel):
    """One endpoint-bucket. A missing measurement is None, never 0.

    A bucket with no row for this endpoint is absent here, not synthesized as a
    zero: whether absence means zero depends on the bucket record's measurement.
    """

    bucket_start: datetime
    bucket_seconds: int
    request_count: int | None = None
    #: From the source's bucket record. None when that record is missing.
    request_count_measurement: str | None = None
    completeness: str
    #: PARTIAL buckets are displayable but never train or judge a baseline.
    partial: bool
    endpoint_coverage_ratio: float | None = None
    in_maintenance: bool | None = None
    normalizer_version: int
    #: Always a lower bound: the maximum over folded raw groups.
    distinct_client_count: int | None = None
    #: Each breakdown's own status. Its counts below are None when it was not collected.
    action_measurement: str | None = None
    response_code_measurement: str | None = None
    client_measurement: str | None = None
    blocked_count: int | None = None
    action_events: int | None = None
    unmapped_action_count: int | None = None
    response_code_events: int | None = None
    client_error_count: int | None = None
    server_error_count: int | None = None
    unclassified_response_count: int | None = None
    client_events: int | None = None
    client_sources: int | None = None
    top1_client_requests: int | None = None
    top5_client_requests: int | None = None


class PagedWafMetrics(BaseModel):
    items: list[WafMetricBucketOut]
    total: int
    limit: int
    offset: int
    range_start: datetime
    range_end: datetime


class WafBaselineCellOut(BaseModel):
    metric_name: str
    weekday: int
    hour: int
    median: float
    mad: float
    p05: float | None = None
    p95: float | None = None
    #: Ratio and share metrics only: the pooled sums the detector divides
    #: (expected proportion = numerator_sum / denominator_sum). None for count metrics.
    numerator_sum: float | None = None
    denominator_sum: float | None = None
    sample_count: int
    #: False means too few samples to drive a verdict: "still learning".
    is_reliable: bool
    completeness: float
    baseline_version: int
    normalizer_version: int
    excluded_sample_count: int
    metric_semantics: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    computed_at: datetime | None = None


class PagedWafBaselines(BaseModel):
    items: list[WafBaselineCellOut]
    total: int
    limit: int
    offset: int
