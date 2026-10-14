"""IPS analyst behavior reads (design §D.11.2): presentation shapes.

The same two rules run through these as through the WAF behavior schemas: a
value that was not measured is `None`, never 0 and never a placeholder, and
availability travels with the data so a client can say "not collected" instead
of drawing an invented series.

Nothing here carries a source IP. Source addresses live only inside the stored,
bounded contributor lists on `ips_source_bucket`, which these reads never
serve; entity `value_key` and `display_name` are the estate's own ports,
destination addresses and signature QIDs (§D.4), which is what an analyst
investigates.

All timestamps are UTC.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AnomalyState, IpsDimension


class IpsDimensionCoverageOut(BaseModel):
    """What one IPS search dimension was for one bucket.

    `measurement` is EXACT or LOWER_BOUND when the dimension carries numbers,
    and NOT_COLLECTED, FAILED or REJECTED when it does not — the reason there is
    no measurement, never a zero in disguise.
    """

    dimension: IpsDimension
    measurement: str
    #: Dimension events over the bucket's totals. None when not measurable.
    coverage_ratio: float | None = None
    #: True when the ratio is a floor (the dimension's search was truncated).
    coverage_lower_bound: bool | None = None


class IpsAdmissionOut(BaseModel):
    """One entity dimension's admission outcome for one bucket.

    The counts are per (source, bucket), as collection recorded them. None means
    the dimension had no measurement; it is never a zero standing in for one.
    """

    dimension: IpsDimension
    admitted: int | None = None
    below_admission_threshold: int | None = None
    rejected_over_quota: int | None = None
    untracked_entities: int | None = None
    untracked_events: int | None = None
    #: True when admission lost entities to the quota, ceiling or top-N cut.
    admission_truncated: bool | None = None


class IpsLatestBucketOut(BaseModel):
    """The most recent bucket an IPS source collected, and how whole it was."""

    bucket_start: datetime
    bucket_seconds: int
    total_events: int
    total_records: int
    in_maintenance: bool
    #: Every search dimension's own measurement and coverage.
    dimensions: list[IpsDimensionCoverageOut]
    #: The three entity dimensions' admission outcomes for this bucket.
    admission: list[IpsAdmissionOut]


class IpsEntityPressureOut(BaseModel):
    """One entity dimension's inventory pressure against its ceiling."""

    dimension: IpsDimension
    active: int
    total: int
    #: `ips_max_active_entities_per_source_dimension`.
    active_ceiling: int


class IpsSourceBehaviorOut(BaseModel):
    """One IPS source: profile status, latest coverage, entity pressure."""

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
    #: None before the first bucket is collected.
    latest_bucket: IpsLatestBucketOut | None = None
    #: One entry per entity dimension, ceiling included.
    entities: list[IpsEntityPressureOut]


class PagedIpsSources(BaseModel):
    items: list[IpsSourceBehaviorOut]
    total: int
    limit: int
    offset: int


class IpsBaselineAdequacyOut(BaseModel):
    """How many seasonal cells of the entity-events baseline can drive a verdict."""

    cells_total: int
    reliable_cells: int


class IpsEntityInventoryOut(BaseModel):
    """One admitted entity of one IPS source, as the inventory lists it."""

    id: uuid.UUID
    dimension: IpsDimension
    #: The port (`443/tcp`), destination address, or QID (`qid:5000123`).
    value_key: str
    #: The signature's name for SIGNATURE entities; otherwise None or the key.
    display_name: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
    retired_at: datetime | None = None
    baseline: IpsBaselineAdequacyOut
    #: Worst state across the entity's detectors. None when no detector has
    #: evaluated it: unevaluated is not NORMAL.
    state: AnomalyState | None = None
    active_anomaly_count: int = 0


class PagedIpsEntities(BaseModel):
    items: list[IpsEntityInventoryOut]
    total: int
    limit: int
    offset: int


class IpsEntityMetricBucketOut(BaseModel):
    """One entity-bucket. A missing measurement is None, never 0.

    A bucket with no row for this entity is absent from the response, not
    synthesized as a zero: whether absence means zero is the detectors' own
    reasoning (design §D.6 inputs), and the UI must not reconstruct it.
    """

    bucket_start: datetime
    bucket_seconds: int
    events: int
    #: The entity's dimension measurement, from the source's bucket record.
    #: None when that record is missing: what the bucket would have said is
    #: unknown, not exact.
    measurement: str | None = None
    coverage_ratio: float | None = None
    coverage_lower_bound: bool | None = None
    admission_truncated: bool | None = None
    in_maintenance: bool | None = None


class IpsSourceMetricBucketOut(BaseModel):
    """One source-bucket: the totals and the facts the IPS_SOURCE detectors judge.

    Every source-level fact is None when its search did not measure it, never
    0. The bounded contributor lists the bucket also holds are not served.
    """

    bucket_start: datetime
    bucket_seconds: int
    total_events: int
    total_records: int
    in_maintenance: bool
    dimensions: list[IpsDimensionCoverageOut]
    #: Source-IP concentration.
    source_events: int | None = None
    distinct_sources: int | None = None
    distinct_sources_lower_bound: bool | None = None
    top1_events: int | None = None
    top5_events: int | None = None
    #: Port fan-out: the busiest source IP's distinct destination ports.
    max_distinct_ports: int | None = None
    #: Action split, when the profile maps an action property.
    blocked_events: int | None = None
    allowed_events: int | None = None
    unmapped_events: int | None = None


class PagedIpsMetrics(BaseModel):
    items: list[IpsEntityMetricBucketOut | IpsSourceMetricBucketOut]
    total: int
    limit: int
    offset: int
    range_start: datetime
    range_end: datetime


class IpsBaselineCellOut(BaseModel):
    """One seasonal cell of an IPS baseline, entity or source level.

    Raw training observations are not served; a cell reports its sample count
    and whether it is reliable enough to drive a verdict. Ratio and share cells
    of the source level also carry the pooled sums the detector divides; entity
    cells leave them None.
    """

    metric_name: str
    weekday: int
    hour: int
    median: float
    mad: float
    p05: float | None = None
    p95: float | None = None
    #: Ratio and share metrics only: the pooled sums behind the expected value.
    numerator_sum: float | None = None
    denominator_sum: float | None = None
    sample_count: int
    #: False means too few samples to drive a verdict: "still learning".
    is_reliable: bool
    completeness: float
    baseline_version: int
    #: The profile version the cell was built under; a detector refuses a cell
    #: from another version.
    profile_version: int
    normalizer_version: int
    excluded_sample_count: int
    metric_semantics: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    computed_at: datetime | None = None


class PagedIpsBaselines(BaseModel):
    items: list[IpsBaselineCellOut]
    total: int
    limit: int
    offset: int
