"""API schemas for property watches, their reads, the preview and the pins (design §G.11).

Domain rules (one or two properties, a reference that passes its pattern, a pin that passes
`PW_PINNED_VALUE_PATTERN`, the scope columns that match the kind) are not repeated here: the
service reports every one of them at once in a 422 of the F.11 shape `{"message", "reasons"}`.
A schema bound would stop at the first bad field and the analyst would fix them one at a time.
`scope_kind`, `severity`, a reference `kind` and a detector name stay enums: a value outside the
set is not a watch the service can describe, and FastAPI's list 422 says so.

Value text is returned to every caller who can read the route (design §G.7.2, D-22).
All timestamps are UTC. A count that was not measured is `None`, never 0.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    PwAnomalyType,
    PwReferenceKind,
    PwScopeKind,
    PwWatchStatus,
    Severity,
)

PwReadingLabel = Literal[
    "COUNT", "MEASURED_ZERO", "UNKNOWN", "NOT_AN_OBSERVATION", "NO_BUCKET"
]
Measurement = Literal["EXACT", "LOWER_BOUND"]
BaselineState = Literal["RELIABLE", "LEARNING", "NONE"]


class PwPropertyRef(BaseModel):
    """One watched property, the IPS `(kind, name)` shape."""

    model_config = ConfigDict(extra="forbid")

    kind: PwReferenceKind
    name: str


class PropertyWatchCreate(BaseModel):
    """A new watch. It is always created disabled: there is no `enabled` field."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    #: Only for a type or group scope; a source scope takes its instance from the source.
    #: Omitted: the single configured instance.
    instance_id: uuid.UUID | None = None
    scope_kind: PwScopeKind
    log_source_id: uuid.UUID | None = None
    scope_type_id: int | None = None
    scope_group_id: int | None = None
    properties: list[PwPropertyRef]
    #: One entry per pinned value, one string per property.
    pinned_values: list[list[str]] = Field(default_factory=list)
    #: Omitted: every detector that applies to the property count.
    detectors: list[PwAnomalyType] | None = None
    severity: Severity = Severity.MEDIUM


class PropertyWatchUpdate(BaseModel):
    """A patch made against `expected_updated_at`, the row's optimistic token.

    The scope travels as a unit: when any of `scope_kind`, `log_source_id`, `scope_type_id`
    or `scope_group_id` is present, the scope fields not present are cleared. Changing the
    scope or the properties bumps `watch_version`; pins, severity, detectors and `enabled`
    do not.
    """

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    scope_kind: PwScopeKind | None = None
    log_source_id: uuid.UUID | None = None
    scope_type_id: int | None = None
    scope_group_id: int | None = None
    properties: list[PwPropertyRef] | None = None
    pinned_values: list[list[str]] | None = None
    detectors: list[PwAnomalyType] | None = None
    severity: Severity | None = None
    enabled: bool | None = None

    @field_validator("expected_updated_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_at must include a timezone")
        return value.astimezone(UTC)


class PinsIn(BaseModel):
    """The whole pinned list. A pin on a combination watch is a full tuple."""

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime
    pinned_values: list[list[str]]

    @field_validator("expected_updated_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_at must include a timezone")
        return value.astimezone(UTC)


class ScopeSummaryOut(BaseModel):
    """The scope in words. `name` is `None` when the inventory no longer lists it."""

    kind: PwScopeKind
    name: str | None = None
    #: A source's type name, a group's path from the root.
    detail: str | None = None
    #: The QRadar id the generated AQL speaks (a source's `qradar_id`, a type or group id).
    qradar_id: int | None = None
    #: The resolved member count (design §G.4), from the same resolver as `/scope`. `None`
    #: when the scope could not be resolved or the read did not ask for it.
    member_count: int | None = None


class ScopeGroupOut(BaseModel):
    """One group the cycle-safe walk met, by id and name."""

    qradar_id: int
    name: str


class ScopeChangeOut(BaseModel):
    """One membership change: the first stored bucket under a fingerprint that differs.

    `member_count_before` is the previous stored bucket's count (or `None` when there was
    none); `in_grace_window` is the bucket's `scope_changed` flag (design §G.4).
    """

    bucket_start: datetime
    member_count_before: int | None = None
    member_count_after: int | None = None
    in_grace_window: bool


class WatchScopeOut(BaseModel):
    """`GET /property-watches/{id}/scope`: what the scope resolves to, and how it changed.

    `state` is `None` when the scope resolved; otherwise it is the resolver's status
    (`SCOPE_EMPTY`, `SCOPE_STALE` or `SCOPE_TOO_LARGE`) with its `reason`. A scope that did
    not resolve still returns what the inventory holds, marked: `member_count` may be absent
    and is never a zero drawn from nothing. `members` names at most `members_total` sources.
    """

    watch_id: uuid.UUID
    watch_version: int
    kind: PwScopeKind
    resolved: bool
    state: PwWatchStatus | None = None
    reason: str | None = None
    #: The resolved member count. `None` when the resolver would not state one.
    member_count: int | None = None
    #: Up to 200 member names, sorted; `members_total` is the true count.
    members: list[str] = Field(default_factory=list)
    members_total: int | None = None
    #: The groups the walk met, sorted by id (the resolver's own order, design §G.4).
    groups_walked: list[ScopeGroupOut] = Field(default_factory=list)
    #: The oldest `synced_at` of the walked group rows: the inventory age, UTC.
    inventory_synced_at: datetime | None = None
    inventory_age_seconds: int | None = None
    #: The stored membership changes, newest first (empty when the scope cannot change).
    changes: list[ScopeChangeOut] = Field(default_factory=list)


class CoverageOut(BaseModel):
    """The last collected bucket's coverage. `ratio` is `None` when unknown, never 0."""

    bucket_start: datetime
    total_events: int | None = None
    parsed_events: int | None = None
    ratio: float | None = None
    measurement: str


class DetectorStateOut(BaseModel):
    """One detector of the watch and the incidents it has not yet resolved."""

    detector_type: PwAnomalyType
    active_incidents: int


class PinRuleOut(BaseModel):
    """A custom rule that pins one value of the watch (design §G.10.2)."""

    id: uuid.UUID
    name: str
    pw_value_id: uuid.UUID
    enabled: bool


class PropertyWatchOut(BaseModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    name: str
    description: str | None = None
    scope_kind: PwScopeKind
    log_source_id: uuid.UUID | None = None
    scope_type_id: int | None = None
    scope_group_id: int | None = None
    scope: ScopeSummaryOut
    properties: list[PwPropertyRef]
    pinned_values: list[list[str]]
    detectors: list[PwAnomalyType]
    severity: Severity
    watch_version: int
    enabled: bool
    #: `None`: the watch has never run, which is not `OK`.
    status: PwWatchStatus | None = None
    status_reason: str | None = None
    collected_through: datetime | None = None
    evaluated_through: datetime | None = None
    last_run_at: datetime | None = None
    #: The newest collected bucket of the current `watch_version`, if any.
    coverage: CoverageOut | None = None
    open_incidents: int = 0
    #: Only on the single-watch read.
    detector_states: list[DetectorStateOut] | None = None
    #: Only on the single-watch read: the F.1 rules pinning a value of this watch, by name.
    threshold_rules: list[PinRuleOut] | None = None
    alerting_mode: Literal["shadow", "alerting"]
    #: The `PROPERTY_WATCH` search-family cadence in seconds (the same for every watch). The
    #: widget's `stale` is "older than twice this" (design §G.13.5).
    cadence_seconds: int
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    updated_by: str | None = None


class PinsOut(BaseModel):
    watch: PropertyWatchOut
    added: int
    removed: int
    #: The rules that pin a value of this watch after the change.
    rules: list[PinRuleOut] = []


# ------------------------------------------------------------------ editor reads
class WatchCatalogOut(BaseModel):
    """The editor's switches and bounds."""

    collection_enabled: bool
    alerting_mode: Literal["shadow", "alerting"]
    max_enabled: int
    enabled_count: int
    #: `pw_max_searches_per_tick`, and the static cost the enabled watches already claim.
    search_budget: int
    budget_used: int
    normalized_fields: list[str]
    reference_kinds: list[str]
    detectors: list[str]
    max_properties: int
    max_values_per_bucket: int
    max_new_values_per_bucket: int
    max_active_values_per_watch: int
    max_pinned_values: int
    value_max_length: int
    max_sources_per_search: int
    max_chunks_per_watch: int
    min_coverage: float
    hours_min: int
    hours_max: int
    hours_default: int
    preview_searches: int
    preview_rate_limit: str


class ScopeOptionOut(BaseModel):
    kind: PwScopeKind
    #: A source's UUID, or a type's or group's QRadar id as text.
    id: str
    qradar_id: int
    name: str
    #: A source's type name, a group's path from the root.
    detail: str | None = None
    #: A type's or group's resolved member count (design §G.4). `None` when the scope could
    #: not be resolved (`scope_state` says why); a measured zero is `0`, never `None`.
    synced_source_count: int | None = None
    #: `None`: the scope resolved. Otherwise the resolver's status (`SCOPE_STALE` and the
    #: like), with its `scope_reason`. A group or type with no count says why here.
    scope_state: PwWatchStatus | None = None
    scope_reason: str | None = None


class PropertyOptionOut(BaseModel):
    name: str
    property_type: str | None = None
    #: False: the name cannot pass the reference pattern; shown but not selectable.
    name_allowed: bool
    #: The scope's types that define this property. Empty: none does.
    defined_on_type_ids: list[int]
    #: Whether every type in scope defines it.
    covers_scope: bool


class PropertyOptionsOut(BaseModel):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    fetched_at: datetime | None = None
    #: The types the scope's sources belong to, whose definitions were consulted.
    scope_type_ids: list[int]
    properties: list[PropertyOptionOut]
    normalized_fields: list[str]
    detail: str | None = None


# --------------------------------------------------------------------- history reads
class WatchBucketOut(BaseModel):
    """One collected bucket. A bucket that was not collected has no row, never a zero."""

    bucket_start: datetime
    bucket_seconds: int
    watch_version: int
    total_events: int | None = None
    parsed_events: int | None = None
    unparsed_events: int | None = None
    untracked_events: int | None = None
    totals_complete: bool
    values_measurement: str
    pinned_measurement: str
    observed_values: int
    tracked_values_observed: int
    admitted_values: int
    below_admission_threshold: int
    rejected_over_quota: int
    admission_truncated: bool
    scope_member_count: int | None = None
    scope_changed: bool
    in_maintenance: bool
    collected_at: datetime


class WatchValueOut(BaseModel):
    id: uuid.UUID
    watch_version: int
    #: The value text; a combination's components joined for display are in `components`.
    value_key: str
    components: list[str]
    truncated: bool
    pinned: bool
    active: bool
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    tracked_since: datetime | None = None
    tracked_until: datetime | None = None
    #: The reading at the newest collected bucket.
    latest_bucket_start: datetime | None = None
    reading: PwReadingLabel
    events: int | None = None
    measurement: Measurement | None = None
    baseline_state: BaselineState
    expected_low: float | None = None
    expected_median: float | None = None
    expected_high: float | None = None
    #: Reliable hour-of-week cells out of 168.
    reliable_cells: int
    cells_total: int = 168


class PagedWatchValues(BaseModel):
    items: list[WatchValueOut]
    total: int
    limit: int
    offset: int


class SeriesPointOut(BaseModel):
    bucket_start: datetime
    reading: PwReadingLabel
    events: int | None = None
    measurement: Measurement | None = None
    baseline_state: BaselineState
    expected_low: float | None = None
    expected_median: float | None = None
    expected_high: float | None = None


class ValueSeriesOut(BaseModel):
    value_id: uuid.UUID
    value_key: str
    pinned: bool
    points: list[SeriesPointOut]


class WatchSeriesOut(BaseModel):
    hours: int
    series: list[ValueSeriesOut]


# ---------------------------------------------------------------------------- preview
class PreviewRowOut(BaseModel):
    value_key: str
    components: list[str]
    events: int
    #: Share of the parsed events; `None` when the parsed total is not known.
    share: float | None = None
    truncated: bool


class PreviewQueryOut(BaseModel):
    search: Literal["GROUPED", "TOTALS"]
    #: Read-only. The generated AQL; the browser can never send one back.
    text: str


class PreviewOut(BaseModel):
    mode: Literal["PREVIEW"] = "PREVIEW"
    watch_id: uuid.UUID
    watch_version: int
    window_start: datetime
    window_end: datetime
    bucket_seconds: int
    searches_sent: int
    #: MEASURED: every bucket measured. PARTIAL: some did not, or a bucket is a lower bound.
    #: NOT_MEASURED: nothing was measured (a failed or cut search).
    outcome: Literal["MEASURED", "PARTIAL", "NOT_MEASURED"]
    reason: str | None = None
    buckets_measured: int
    buckets_lower_bound: int
    buckets_unmeasured: int
    #: `None` unless every bucket's totals search was whole.
    total_events: int | None = None
    parsed_events: int | None = None
    unparsed_events: int | None = None
    unparsed_bound: Literal["EXACT", "UPPER_BOUND"] | None = None
    coverage: float | None = None
    #: Whether the property parses on the scope: `True` when it parsed on any event, `False`
    #: when it parsed on none although events arrived, `None` when that cannot be said.
    property_parses: bool | None = None
    #: The rows are lower bounds when any bucket was cut or any value was left untracked.
    rows_measurement: Measurement | None = None
    rows: list[PreviewRowOut]
    queries: list[PreviewQueryOut]


class WatchAqlQueryOut(BaseModel):
    """One generated search, read-only text. Nothing here is ever accepted back."""

    search: Literal["GROUPED", "TOTALS", "PINNED"]
    text: str


class WatchAqlLastBucketOut(BaseModel):
    """The newest collected bucket's stored query provenance, by whitelist.

    `queries` holds what the collector recorded for that bucket; a search that was not sent
    (budget) or has no text is absent, never an empty string.
    """

    bucket_start: datetime
    collected_at: datetime
    watch_version: int
    queries: list[WatchAqlQueryOut]


class WatchAqlOut(BaseModel):
    """The generated AQL of a stored watch (design §G.13.3, Definition tab). Text only.

    `current` is what the preview's generator makes of the stored watch for the last closed
    hour, with no Ariel call; the REST provider appends `START`/`STOP` when it runs one.
    `current_unavailable` says why it could not be built (the scope does not resolve), and then
    `current` is empty. `last_bucket` is None before the first collection.
    """

    watch_id: uuid.UUID
    watch_version: int
    window_start: datetime | None = None
    window_end: datetime | None = None
    bucket_seconds: int | None = None
    current: list[WatchAqlQueryOut]
    current_unavailable: str | None = None
    last_bucket: WatchAqlLastBucketOut | None = None
