"""API contracts for the history-backfill admin page (HISTORY-BACKFILL §B.10.3).

Reasons and states are words; nothing here carries an event value, a property value or
AQL. A measurement that is missing is `None` with a reason, never a zero (rule 8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.history.lifecycle import ConsoleMode


class DecisionAnswerIn(BaseModel):
    """The only thing the browser sends: Yes or No. `days` is always the stored setting."""

    model_config = ConfigDict(extra="forbid")

    answer: Literal["YES", "NO"]


class DecisionOut(BaseModel):
    answer: str
    days: int | None
    decided_at: datetime
    decided_by: str
    source: str
    updated_at: datetime | None
    updated_by: str | None
    start_requested_at: datetime | None
    start_requested_by: str | None
    stop_requested_at: datetime | None
    stop_requested_by: str | None


class WindowOut(BaseModel):
    """`fit_family_window`: the days the family may read and what limited them."""

    days_requested: int
    #: None when the family is unavailable; `reason` then says why.
    days: int | None
    available: bool
    limited_by: list[str]
    reason: str | None


class GapOut(BaseModel):
    start: datetime
    end: datetime
    reason: str


class ScopeOut(BaseModel):
    scope_key: str
    #: The QRadar log source's name for a WAF or IPS scope row; None for a console-wide
    #: row and for a source that is no longer known.
    name: str | None
    state: str
    days_requested: int | None
    days_target: int | None
    target_start: datetime | None
    #: What `floor` is depends on the family (`floor_proved` says which):
    #: - WAF, IPS and rule matches: the floor QRadar's own read proved for this scope
    #:   (§B.3.2), so nothing before it may be written;
    #: - Phase A and catch-all: only where the read started, `T0 - m`. Their real floors
    #:   are per source, in the progress (§B.3.2); a scope-wide column cannot hold them.
    floor: datetime | None
    #: True when the row carries the floor QRadar proved for this scope; false when
    #: `floor` is only the read start (Phase A, catch-all) or when the row has no
    #: floor to prove (`NOT_ELIGIBLE`, `UNAVAILABLE`): neither reading applies.
    floor_proved: bool
    through: datetime | None
    anchor: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    reason: str | None
    last_error_class: str | None
    last_error_at: datetime | None
    #: From the decoded progress; None for a `{}` progress or one that does not decode.
    searches: int | None
    buckets_read: int | None
    #: Whole buckets from the family's proven read start through `through`, per day, one
    #: decimal; never an ETA. Phase A and catch-all count from `max(floor, target_start)`:
    #: their `floor` is the read start, which is earlier than anything they could prove.
    days_read: float | None
    gaps: list[GapOut] | None
    #: True when the stored progress did not decode: the three fields above are None.
    progress_unreadable: bool
    later_yes: str


class SourceOut(BaseModel):
    """A configured WAF or IPS source that has no scope row."""

    scope_key: str
    name: str
    profile_enabled: bool
    later_yes: str


class FamilyOut(BaseModel):
    family: str
    collection_enabled: bool
    collection_reason: str
    window: WindowOut
    scopes: list[ScopeOut]
    #: WAF and IPS only: the configured sources without a scope row. None elsewhere.
    sources: list[SourceOut] | None
    #: Console-wide families: "possible". None for WAF and IPS (it is per source).
    later_yes: str | None


class ConsoleOut(BaseModel):
    instance_id: uuid.UUID
    name: str
    decision: DecisionOut | None
    mode: ConsoleMode
    families: list[FamilyOut]
    #: Set by the three writes: what was recorded and when the scopes change.
    message: str | None = None


class ConflictOut(BaseModel):
    detail: str
    console: ConsoleOut | None = None


class EstimateOut(BaseModel):
    family: str
    #: None, with `reason`, when the count the model needs is not available.
    searches: int | None
    ticks: int | None
    hours: float | None
    days_requested: int
    days_fitted: int | None
    reduced: bool
    refused: bool
    reason: str
    assumptions: list[str]


class ConsoleEstimateOut(BaseModel):
    instance_id: uuid.UUID
    name: str
    #: Always "estimate": the figure is a design model, not a measurement (§B.10.2).
    kind: Literal["estimate"] = "estimate"
    families: list[EstimateOut]
