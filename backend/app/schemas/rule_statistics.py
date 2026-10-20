"""Rule-statistics and capacity-history API schemas (Phase E.1b).

A section the platform could not measure is `None` with a reason beside it, never
an empty list or a zero: an empty list reads as "measured, found nothing".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.offense import _Sanitized


class RuleCountRow(BaseModel):
    enabled: bool
    rule_type: str | None
    origin: str | None
    is_building_block: bool
    count: int


class StatisticsWindow(BaseModel):
    """The span asked for, and how much of it was measured."""

    days: int
    span_start: datetime
    span_end: datetime
    span_seconds: int
    window_seconds: int
    expected_windows: int
    complete_windows: int
    #: Of `complete_windows`, how many the history backfill read from QRadar's
    #: past rather than the cadence collection (HISTORY-BACKFILL §B.4.5, R6).
    history_windows: int
    truncated_windows: int
    failed_windows: int
    missing_windows: int
    #: Seconds covered by COMPLETE windows: the denominator of every match rate.
    measured_seconds: int
    #: Every expected window is COMPLETE (and there is at least one).
    complete: bool
    #: Why the measured sections are absent or partial; None when complete.
    reason: str | None = None


class SilentRule(_Sanitized):
    rule_id: uuid.UUID
    qradar_id: int
    name: str
    rule_type: str | None
    origin: str | None
    is_building_block: bool
    first_seen_at: datetime | None = None
    last_fired_at: datetime | None = None


class SilentExclusion(BaseModel):
    rule_type: str | None
    evidence: str
    count: int


class SilentSection(BaseModel):
    """Enabled rules with a *measured* zero over a complete span."""

    items: list[SilentRule]
    #: Matching rules before the item cap.
    total: int
    items_truncated: bool
    #: Enabled rules that were not judged, by type, and why (see `excluded_reason`).
    excluded: list[SilentExclusion]
    excluded_count: int
    excluded_reason: str


class MatchRankRow(_Sanitized):
    rule_id: uuid.UUID
    qradar_id: int
    name: str
    enabled: bool
    rule_type: str | None
    is_building_block: bool
    match_records: int
    matched_events: int
    #: Matched events per second over the measured seconds.
    match_rate_per_second: float
    #: The count proves firing but may undercount (flow matches are not searched).
    lower_bound: bool


class CostRow(_Sanitized):
    rule_id: uuid.UUID
    qradar_id: int
    name: str
    rule_type: str | None
    is_building_block: bool
    average_capacity: float


class CostSection(BaseModel):
    """QRadar's capacity benchmark. Lower = costlier to evaluate. NOT a firing rate."""

    label: str
    unit: str
    items: list[CostRow]
    #: Enabled rules whose benchmark is zero or missing, left out of the ranking.
    no_benchmark_count: int


class RuleStatistics(BaseModel):
    instance_id: uuid.UUID
    counts: list[RuleCountRow]
    window: StatisticsWindow
    silent: SilentSection | None
    silent_reason: str | None
    top_by_matches: list[MatchRankRow] | None
    top_by_events: list[MatchRankRow] | None
    top_reason: str | None
    costliest_by_benchmark: CostSection


class CapacitySampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capacity_timestamp: datetime
    average_capacity: float | None
    base_capacity: float | None
    base_host_id: int | None
    recorded_at: datetime
