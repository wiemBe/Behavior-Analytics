"""WAF operational status for administrators.

Counts, timestamps, statuses and property names only. No request value -- no
URL, user agent, client address or discovered value -- appears in any field.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WafFieldStatusOut(BaseModel):
    field: str
    property_name: str | None = None
    status: str


class WafLastBucketOut(BaseModel):
    bucket_start: datetime
    completeness: str
    #: EXACT, or LOWER_BOUND when grouping was truncated or coverage was low.
    request_count_measurement: str
    #: The grouped result hit its cap: new-endpoint discovery was incomplete.
    groups_truncated: bool
    discovery_complete: bool
    #: `endpoint_coverage_ratio` is itself a lower bound when grouping truncated.
    coverage_measurement: str
    sparse_endpoint_rows: bool
    endpoint_coverage_ratio: float
    #: The source's whole volume from its own ungrouped aggregate: exact even
    #: when grouping was truncated.
    total_events: int
    observed_endpoints: int
    tracked_endpoints_observed: int
    admitted_endpoints: int
    untracked_endpoints: int
    untracked_requests: int
    admission_truncated: bool
    in_maintenance: bool
    profile_version: int
    metric_semantics: str


class WafSourceStatusOut(BaseModel):
    profile_id: uuid.UUID
    instance_id: uuid.UUID
    log_source_id: uuid.UUID
    qradar_log_source_id: int
    log_source_name: str
    product_label: str | None = None

    enabled: bool
    profile_version: int
    validation_status: str
    validated_profile_version: int | None = None
    validated_at: datetime | None = None
    validation_fields: list[WafFieldStatusOut] = Field(default_factory=list)

    #: Whether scheduled runs will process this source, and if not, why not.
    collection_eligible: bool
    ineligible_reason: str | None = None

    collection_watermark_at: datetime | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    #: Seconds from the end of the last collected bucket to now. None before
    #: the first successful collection.
    lag_seconds: int | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    last_failure_at: datetime | None = None

    #: Query pressure of the most recent run that reached collection.
    ariel_searches_last_run: int | None = None
    last_collection_duration_ms: int | None = None
    last_run_intervals_collected: int | None = None
    last_run_intervals_due: int | None = None
    last_permit_wait_ms: int | None = None
    #: The most recent skipped run: `source_locked` (another worker held the
    #: source) or `global_capacity` (no WAF Ariel permit freed up in time).
    last_skip_reason: str | None = None
    last_skipped_at: datetime | None = None
    #: Whether the most recent run of any outcome was a skip.
    previous_run_skipped: bool = False

    last_bucket: WafLastBucketOut | None = None
    admission_truncated_buckets_24h: int = 0
    #: Buckets whose endpoint counts were lower bounds, in the last 24 h.
    lower_bound_buckets_24h: int = 0
    active_endpoints: int = 0
    active_endpoint_ceiling: int
    #: More endpoints are tracked than one grouped query can return. A bucket in
    #: which more raw (URL, method) groups than the cap are requested -- which
    #: this many active endpoints make possible -- yields lower bounds only: it
    #: can prove a spike but never trains a baseline or proves an endpoint normal.
    active_endpoints_exceed_group_cap: bool = False

    baseline_active: bool
    baseline_rebuilt_at: datetime | None = None
    baseline_train_through: datetime | None = None
    endpoints_without_baseline_rebuild: int = 0

    detector_active: bool
    evaluated_through: datetime | None = None
    pending_evaluation_buckets: int = 0


class WafArielBudgetOut(BaseModel):
    """Global WAF Ariel concurrency. Counts only; no query text."""

    enforced: bool
    max_concurrent_sources: int
    #: Permits held right now, by any worker. None when not enforced.
    active_permits: int | None = None
    permit_wait_seconds: float
