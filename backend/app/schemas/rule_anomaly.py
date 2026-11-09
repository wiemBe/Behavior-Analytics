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
