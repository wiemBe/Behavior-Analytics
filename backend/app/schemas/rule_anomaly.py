"""Rule-match anomalies on the rule-statistics page (SOC-6 design §7.1).

One read, beside `GET /rules/statistics` and not part of it. The block answers "which rules
changed match rate this span?" and is honest about the span it can judge:

  * `NOT_COLLECTED` -- the collector is off, or has never recorded a window. No rows, no
    window: an empty table would read as "measured, found nothing".
  * `INSUFFICIENT_DATA` -- the span is not `COMPLETE`. Silence rows are withheld (absence
    proves nothing over a gappy span); storm rows can still stand, each carrying the
    span's window status.
  * `OK` -- the span is complete; `items` may be empty, and that is a measurement.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.models.enums import AnomalyState, RuleAnomalyType
from app.schemas.messages import MessageParamValue
from app.schemas.offense import _Sanitized


class MatchAnomalyBlockState(StrEnum):
    NOT_COLLECTED = "NOT_COLLECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    OK = "OK"


class MatchAnomalyRowState(StrEnum):
    """What the row's observed window was worth."""

    #: The observed window was read in full.
    OK = "OK"
    #: The observed window was truncated: the count is at least this, never an exact figure.
    TRUNCATED = "TRUNCATED"


class MatchAnomalySpan(BaseModel):
    """The span the block was judged over, and how much of it was measured."""

    days: int
    span_start: datetime
    span_end: datetime
    window_seconds: int
    expected_windows: int
    complete_windows: int
    truncated_windows: int
    failed_windows: int
    missing_windows: int
    #: Every expected window is COMPLETE (and there is at least one).
    complete: bool
    #: The coverage state of the span: `none`, `complete` or `incomplete`.
    window_status: str
    #: Why the span is not complete; None when it is.
    reason: str | None = None
    reason_code: str | None = None
    reason_params: dict[str, MessageParamValue] | None = None


class MatchAnomalyRow(_Sanitized):
    anomaly_id: uuid.UUID
    rule_id: uuid.UUID
    name: str
    qradar_id: int
    is_building_block: bool
    detector: RuleAnomalyType
    #: Matched events in the observed window; None when the detector stored none.
    observed: float | None = None
    expected: float | None = None
    #: The incident's detector state (OPEN, RECOVERING or RESOLVED).
    state: AnomalyState
    #: When the incident's first abnormal window began (UTC).
    started: datetime
    #: TRUNCATED when the observed window was truncated (a storm is then a lower bound).
    row_state: MatchAnomalyRowState
    #: The span's coverage state, repeated on the row so a storm shown over an incomplete
    #: span says so where it is read.
    window_status: str


class RuleMatchAnomalies(BaseModel):
    instance_id: uuid.UUID
    state: MatchAnomalyBlockState
    #: None unless the collector has recorded windows (state is not NOT_COLLECTED).
    span: MatchAnomalySpan | None = None
    #: None when NOT_COLLECTED: nothing was measured, so there is no list to be empty.
    items: list[MatchAnomalyRow] | None = None
    #: Matching incidents before the cap; None when NOT_COLLECTED.
    total: int | None = None
    items_truncated: bool = False
    #: Why the block is not OK; None when it is.
    reason: str | None = None
    reason_code: str | None = None
    reason_params: dict[str, MessageParamValue] | None = None


class RuleMatchSectionState(StrEnum):
    """The one answer `/rules/{id}` gives for "is this match rate being judged?"

    Precedence is the design §7.3 order the route applies to the detector skips:
    collection, then type, disabled, monitoring, modified, truncated, then a
    specific insufficient reason, then OK. `incidents` is not a state.
    """

    NOT_COLLECTED = "NOT_COLLECTED"
    NOT_EVENT = "NOT_EVENT"
    DISABLED = "DISABLED"
    MONITORING_OFF = "MONITORING_OFF"
    MODIFIED = "MODIFIED"
    TRUNCATED = "TRUNCATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    OK = "OK"


class RuleMatchSectionCounts(BaseModel):
    """Tallies an `INSUFFICIENT_DATA` reason names.

    Window, sample and week counts only. A match rate is not here: a gap is not
    reported as zero events per window.
    """

    expected_windows: int | None = None
    complete_windows: int | None = None
    missing_windows: int | None = None
    truncated_windows: int | None = None
    failed_windows: int | None = None
    wrong_width_windows: int | None = None
    #: Share of baseline windows that matched, when the presence gate refused.
    presence: float | None = None
    presence_minimum: float | None = None
    sample_count: int | None = None
    visits: int | None = None
    minimum_samples: int | None = None
    minimum_visits: int | None = None
    baseline_days: int | None = None
    #: Median matched events, when the median floor refused. Not a rate for a gap.
    median: float | None = None
    median_minimum: float | None = None


class RuleMatchSectionIncident(_Sanitized):
    """One open incident, or the most recent closed one, for this rule."""

    anomaly_id: uuid.UUID
    detector: RuleAnomalyType
    observed: float | None = None
    expected: float | None = None
    baseline_low: float | None = None
    baseline_high: float | None = None
    #: Silence streak (`consecutive_buckets`). Null when the row stored none.
    consecutive_buckets: int | None = None
    #: Storm ratio, or the silence ratio to the baseline when one was stored.
    ratio: float | None = None
    started: datetime
    ended: datetime | None = None
    state: AnomalyState


class RuleMatchSection(BaseModel):
    """`GET /rules/{id}/match-anomalies` (design §7.3).

    One state, plus the open incidents and the newest closed one whatever that
    state is. `judged_through` is the end of the newest settled window, null
    when no window has settled.
    """

    rule_id: uuid.UUID
    state: RuleMatchSectionState
    reason: str | None = None
    reason_code: str | None = None
    reason_params: dict[str, MessageParamValue] | None = None
    #: `LOWER_BOUND` or `NOT_MEASURED` when the state is `NOT_EVENT`.
    match_evidence: str | None = None
    #: Inventory watermark, when the state is `DISABLED` or the sync is the reason.
    inventory_synced_at: datetime | None = None
    #: QRadar's modification time, when the state is `MODIFIED`.
    modified_at: datetime | None = None
    judged_through: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    counts: RuleMatchSectionCounts | None = None
    incidents: list[RuleMatchSectionIncident]
