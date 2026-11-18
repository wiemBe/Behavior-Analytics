"""API schemas for the Phase C fleet view and classification rules.

Completeness is part of the contract: every volume figure carries the bucket
counts it was computed from, and a figure with no complete bucket behind it is
null, never zero.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    ClassificationReviewStatus,
    ClassificationRuleKind,
    OutagePolicyMode,
    SourceClass,
)

FleetWindow = Literal["1h", "24h", "7d"]
WINDOW_SECONDS: dict[str, int] = {"1h": 3600, "24h": 86_400, "7d": 604_800}

# What `basis` can hold: a rule kind, or one of the three non-rule outcomes.
ClassificationBasis = Literal[
    "OVERRIDE",
    "INTERNAL",
    "UNCLASSIFIED",
    "GROUP",
    "TYPE",
    "TYPE_NAME_REGEX",
    "NAME_REGEX",
    "IDENTIFIER_REGEX",
]

MAX_PATTERN_LENGTH = 256


class SourceCounts(BaseModel):
    enabled: int
    disabled: int


class BucketCounts(BaseModel):
    """Expected versus measured 5-minute buckets for the monitored sources of a class.

    `expected` is monitored, enabled sources times buckets in the window.
    `missing` is what is neither complete nor partial, so the three always add up
    to `expected` unless a class has more rows than expected (a disabled source
    that still reports), in which case `missing` is 0.
    """

    expected: int
    complete: int
    # COMPLETE buckets read from QRadar's past. This is a subset of `complete`.
    complete_from_history: int
    partial: int
    missing: int


class TopSource(BaseModel):
    id: uuid.UUID
    name: str
    events: int


class ClassSummary(BaseModel):
    source_class: SourceClass
    sources: SourceCounts
    buckets: BucketCounts
    # Null when no complete bucket exists. Complete buckets only; the bucket
    # counts say how much of the window that is.
    events: int | None
    mean_eps: float | None
    # Sum, over the class's sources, of each source's mean reliable baseline
    # median across every (UTC weekday, hour) cell the window spans; a source
    # counts only when all of its spanned cells are reliable. Null when none does.
    expected_eps: float | None
    baseline_sources: int
    baseline_share: float | None
    open_incidents: dict[str, int]
    top_sources: list[TopSource]
    # The class's resolved outage mode ignoring source overrides (the global
    # class row or the default), and how many source rows override it.
    outage_mode: OutagePolicyMode
    outage_overrides: int
    # Sources that are enabled and monitored: the ones an outage policy governs.
    # A sibling of `sources` rather than a field of it, so `sources` keeps its
    # two-key shape.
    monitored_sources: int = 0


class InternalSummary(BaseModel):
    sources: SourceCounts


class FleetSummary(BaseModel):
    window: FleetWindow
    window_start: datetime
    window_end: datetime
    bucket_seconds: int
    classes: list[ClassSummary]
    internal: InternalSummary
    new_sources: int
    conflict_sources: int
    unclassified_sources: int
    # From settings, not the database: what a class with no policy row resolves
    # to, and the silence grace (in buckets) when no row overrides it.
    outage_default: OutagePolicyMode = OutagePolicyMode.ALERT
    default_silence_grace_buckets: int | None = None


class FleetSourceOut(BaseModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    qradar_id: int
    name: str
    type_name: str | None
    enabled: bool
    auto_discovered: bool | None
    first_seen_at: datetime
    source_class: SourceClass
    basis: str
    rule_id: uuid.UUID | None
    override_class: SourceClass | None
    override_by: str | None
    override_at: datetime | None
    conflicts: list[dict]
    classified_at: datetime | None
    review_status: ClassificationReviewStatus | None
    reviewed_at: datetime | None
    reviewed_by: str | None


def fleet_source_out(source, classification) -> FleetSourceOut:  # type: ignore[no-untyped-def]
    """Join a log source with its (possibly missing) classification row.

    No row means UNCLASSIFIED, never a guess: the list stays complete and the
    review fields are null rather than defaulted to REVIEWED.
    """
    cl = classification
    return FleetSourceOut(
        id=source.id,
        instance_id=source.instance_id,
        qradar_id=source.qradar_id,
        name=source.name,
        type_name=source.type_name,
        enabled=source.enabled,
        auto_discovered=source.auto_discovered,
        first_seen_at=source.first_seen_at,
        source_class=cl.source_class if cl else SourceClass.UNCLASSIFIED,
        basis=cl.basis if cl else "UNCLASSIFIED",
        rule_id=cl.rule_id if cl else None,
        override_class=cl.override_class if cl else None,
        override_by=cl.override_by if cl else None,
        override_at=cl.override_at if cl else None,
        conflicts=list(cl.conflicts) if cl else [],
        classified_at=cl.classified_at if cl else None,
        review_status=cl.review_status if cl else None,
        reviewed_at=cl.reviewed_at if cl else None,
        reviewed_by=cl.reviewed_by if cl else None,
    )


FleetFlow = Literal["FLOWING", "SILENT", "DISABLED_IN_QRADAR", "NEVER"]
FleetEpsState = Literal["BELOW_EXPECTED", "ABOVE_EXPECTED", "WITHIN_EXPECTED", "NO_BASELINE"]
FleetProfileKind = Literal["NONE", "WAF", "IPS", "IDENTITY"]
FleetSourceSort = Literal["name", "type", "class", "eps", "last_event", "silent_for"]
ProfileValidation = Literal["UNVALIDATED", "VALID", "INVALID", "INCONCLUSIVE"]


class ProfileState(BaseModel):
    """One source's WAF, IPS or identity profile: its validation verdict and switch."""

    status: ProfileValidation
    enabled: bool


class FleetSourceRow(FleetSourceOut):
    """A fleet list row: the classified source plus what the list searches and sorts on."""

    identifier: str | None
    type_id: int | None
    # At most FLEET_GROUP_NAMES names, by name; `group_count` is the real number.
    group_names: list[str]
    group_count: int
    flow: FleetFlow
    last_event_at: datetime | None
    # Seconds since the last event, only for a SILENT source; null otherwise.
    silent_for_seconds: int | None
    # Latest COMPLETE bucket of the last hour; null when none was measured.
    current_eps: float | None
    # The reliable baseline cell for the current UTC weekday and hour.
    expected_eps: float | None
    expected_eps_low: float | None
    expected_eps_high: float | None
    # Null when there is a baseline but no current measurement.
    eps_state: FleetEpsState | None
    waf_profile: ProfileState | None
    ips_profile: ProfileState | None
    identity_profile: ProfileState | None
    # The log-source details (UIQ-7d): mirrored from QRadar, then the SOC-owned fields.
    description: str | None
    protocol_type_id: int | None
    target_event_collector_id: int | None
    credibility: int | None
    qradar_status: str | None
    qradar_created_at: datetime | None
    qradar_modified_at: datetime | None
    criticality: str
    owner: str | None
    monitoring_enabled: bool
    maintenance_mode: bool
    maintenance_until: datetime | None
    # Every group name, by name, at most FLEET_ALL_GROUP_NAMES; `group_count` is the real number.
    groups: list[str]


class FacetCount(BaseModel):
    value: str
    count: int


class TypeFacetCount(BaseModel):
    type_id: int | None
    name: str | None
    count: int


class GroupFacetCount(BaseModel):
    group_id: int
    name: str
    count: int


class FleetSourceFacets(BaseModel):
    """Counts per value, each under every active filter except its own family."""

    source_class: list[FacetCount]
    type: list[TypeFacetCount]
    group: list[GroupFacetCount]
    criticality: list[FacetCount]
    owner: list[FacetCount]
    auto_discovered: list[FacetCount]
    monitoring: list[FacetCount]
    maintenance: list[FacetCount]
    collector: list[FacetCount]
    flow: list[FacetCount]
    profile: list[FacetCount]


class FleetSourcePage(BaseModel):
    items: list[FleetSourceRow]
    total: int
    limit: int
    offset: int
    facets: FleetSourceFacets


MAX_BULK_SOURCES = 500


class FleetBulkClassificationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_source_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BULK_SOURCES)
    # Required, and null clears the override: an omitted field is a 422.
    source_class: SourceClass | None = Field(alias="class")
    mark_reviewed: bool = False


class FleetBulkClassificationOut(BaseModel):
    updated: int
    items: list[FleetSourceRow]


class FleetSyncPass(BaseModel):
    """What the last finished inventory pass did (FIX-74); counts are of that pass only."""

    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    finished_at: datetime | None
    elapsed_ms: int | None
    seen: int
    created: int
    updated: int
    pages_done: int
    pages_failed: int
    failed_page: int | None
    # timeout, auth, rate_limited, upstream, network, malformed; None when nothing failed.
    failure_kind: str | None


class FleetSyncInstance(BaseModel):
    """The last log-source inventory sync of one enabled console."""

    instance_id: uuid.UUID
    instance_name: str
    # RUNNING (the collector holds its lock now), FAILED (the last pass read nothing),
    # PARTIAL (it read some pages and upserted them), OK (complete), NEVER.
    status: Literal["RUNNING", "FAILED", "PARTIAL", "OK", "NEVER"]
    last_pass: FleetSyncPass | None = None
    last_run_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int


class FleetSyncStatus(BaseModel):
    instances: list[FleetSyncInstance]
    running: bool


class FleetSyncQueued(BaseModel):
    queued: bool
    already_running: bool
    task_id: str | None = None


class ClassificationOverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required, and null clears: an omitted field is a 422, not a silent clear.
    override_class: SourceClass | None


class ClassificationRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instance_id: uuid.UUID | None
    kind: ClassificationRuleKind
    pattern: str
    target_class: SourceClass
    priority: int
    enabled: bool
    built_in: bool
    description: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


def check_pattern(kind: ClassificationRuleKind, pattern: str) -> str:
    if not pattern.strip():
        raise ValueError("pattern must not be empty")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"pattern must be at most {MAX_PATTERN_LENGTH} characters")
    if kind in (
        ClassificationRuleKind.TYPE_NAME_REGEX,
        ClassificationRuleKind.NAME_REGEX,
        ClassificationRuleKind.IDENTIFIER_REGEX,
    ):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"pattern is not a valid regular expression: {exc}") from exc
    elif not pattern.strip().isdigit():
        # GROUP and TYPE compare integers; a non-numeric pattern could never match.
        raise ValueError(f"a {kind.value} rule needs a numeric id as its pattern")
    return pattern


class ClassificationRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID | None = None
    kind: ClassificationRuleKind
    pattern: str = Field(min_length=1)
    target_class: SourceClass
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("target_class")
    @classmethod
    def _not_unclassified(cls, value: SourceClass) -> SourceClass:
        if value == SourceClass.UNCLASSIFIED:
            raise ValueError("a rule cannot target UNCLASSIFIED")
        return value

    @model_validator(mode="after")
    def _valid_pattern(self) -> ClassificationRuleCreate:
        check_pattern(self.kind, self.pattern)
        return self


class ClassificationRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str | None = Field(default=None, min_length=1)
    target_class: SourceClass | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None
    description: str | None = Field(default=None, max_length=1000)


FleetSeriesGroup = Literal["none", "class"]


class EpsSeriesPoint(BaseModel):
    """One step of one series. `events` and `eps` are null unless every expected cell is complete.

    `cells_expected` is monitored, non-internal sources times the collection buckets in the step;
    `cells_complete` is how many of those cells hold a COMPLETE metric row. A step with a missing
    cell has no sum at all: a smaller sum would read as a drop.
    """

    step_start: datetime
    events: int | None
    eps: float | None
    # The sum of per-source expected EPS (reliable `average_eps` medians), not a fleet
    # band. Null unless every in-scope source has a reliable cell for the step.
    expected_eps: float | None
    cells_complete: int
    # Of `cells_complete`, how many were read from QRadar's past by the history
    # backfill rather than collected live (HISTORY-BACKFILL §B.4.1, R6). A step
    # whose cells are all live reports 0, which is a measured zero.
    cells_from_history: int
    cells_expected: int
    sources_expected: int


class EpsSeries(BaseModel):
    key: str
    label: str
    sources_expected: int
    points: list[EpsSeriesPoint]


class EpsCoverage(BaseModel):
    # Steps with a non-null `eps`, and all steps, each summed across the series.
    steps_total: int
    steps_measured: int


class FleetEpsSeries(BaseModel):
    window: FleetWindow
    group_by: FleetSeriesGroup
    window_start: datetime
    # The last closed bucket floored to `step_seconds`, so every step is whole. Up to
    # one step minus one bucket of the newest data lies after it and is not shown.
    window_end: datetime
    bucket_seconds: int
    step_seconds: int
    series: list[EpsSeries]
    coverage: EpsCoverage
