"""The scope lifecycle contract (HISTORY-BACKFILL §B.5, §B.6.1, §B.7.1, §B.7.2; HB-2b).

The collector is the only writer of `history_backfill_scope`: each family adapter creates,
moves and updates its own scope rows inside its collection tick. The API and the CLI write
only `history_backfill_decision`; "Stop reading" and "Read history now" are *requests* on
that row (`stop_requested_*`, `start_requested_*`), applied at the family's next tick.

`scope_action` is the one function that says what happens to a scope, so the five family
adapters cannot diverge on §B.5. `new_scope_row` and `transition_row` build the full column
mapping the repository's `write_scopes` takes, so an adapter never assembles a row by hand.
A state change is always applied through `next_state`, never by hand.

Tie rules (pinned by tests): a stop request is "later" than a start request only when it is
strictly later, so equal times read `ACTIVE`; a start request restarts a finished
console-wide scope only when it is strictly later than `finished_at`.

Pure: no database, no clock, no settings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.history.planner import ScopeProgress
from app.history.progress_codec import DecodedProgress, decode_progress
from app.history.state import (
    SCOPE_TABLE_FAMILIES,
    HistoryFamily,
    HistoryState,
    HistoryTrigger,
    next_state,
)

#: The `reason` column is a `String(255)`.
MAX_REASON_LENGTH = 255

ANSWER_YES = "YES"
ANSWER_NO = "NO"

#: Every column of a scope row; `write_scopes` refuses a mapping that lacks one.
SCOPE_COLUMNS: tuple[str, ...] = (
    "instance_id",
    "family",
    "scope_key",
    "state",
    "days_requested",
    "days_target",
    "target_start",
    "floor",
    "through",
    "anchor",
    "started_at",
    "finished_at",
    "reason",
    "progress",
)

#: Per source (one scope per QRadar log source); the others are console-wide.
_PER_SOURCE = frozenset({HistoryFamily.WAF, HistoryFamily.IPS})
_READING_OR_WAITING = (HistoryState.READING, HistoryState.WAITING)
_FINISHED = (HistoryState.COMPLETE, HistoryState.PARTIAL, HistoryState.UNAVAILABLE)

NOT_ELIGIBLE_REASON = (
    "this source already collected live data; history cannot be read before it "
    "(disable the profile and create it again to read its history)"
)
RESTART_REASON = "read history now: topping up"
SWITCHED_OFF_REASON = "history reading was switched off"


class LifecycleError(ValueError):
    """An input that contradicts itself; the caller must not guess."""


def bounded_reason(text: str, limit: int = MAX_REASON_LENGTH) -> str:
    """`text` cut to `limit` characters, ending in "…" when it was cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _aware(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise LifecycleError(f"{name} must be timezone-aware")


class ConsoleMode(StrEnum):
    UNDECIDED = "UNDECIDED"  # no decision row: undecided is not Yes (§B.12 row 15)
    NO = "NO"
    ACTIVE = "ACTIVE"  # YES, not stopped
    STOPPED = "STOPPED"  # YES and a stop request later than any start request


@dataclass(frozen=True)
class DecisionView:
    """The decision row's columns the lifecycle reads; never an ORM object."""

    answer: str
    days: int | None = None
    start_requested_at: datetime | None = None
    start_requested_by: str | None = None
    stop_requested_at: datetime | None = None
    stop_requested_by: str | None = None

    def __post_init__(self) -> None:
        if self.answer not in (ANSWER_YES, ANSWER_NO):
            raise LifecycleError(f"unknown decision answer {self.answer!r}")
        if (self.answer == ANSWER_YES) != (self.days is not None):
            raise LifecycleError("a YES carries days and a NO carries none")
        for kind in ("start", "stop"):
            at = getattr(self, f"{kind}_requested_at")
            by = getattr(self, f"{kind}_requested_by")
            if (at is None) != (by is None):
                raise LifecycleError(f"{kind}_requested_at and _by are set together")
            _aware(at, f"{kind}_requested_at")
            if at is not None and self.answer != ANSWER_YES:
                raise LifecycleError("a request exists only on a YES")


def console_mode(decision: DecisionView | None) -> ConsoleMode:
    if decision is None:
        return ConsoleMode.UNDECIDED
    if decision.answer == ANSWER_NO:
        return ConsoleMode.NO
    stop, start = decision.stop_requested_at, decision.start_requested_at
    if stop is not None and (start is None or stop > start):
        return ConsoleMode.STOPPED
    return ConsoleMode.ACTIVE


@dataclass(frozen=True)
class ScopeRowView:
    """A scope row's columns (without its key), as the lifecycle reads them."""

    state: HistoryState
    days_requested: int | None = None
    days_target: int | None = None
    target_start: datetime | None = None
    floor: datetime | None = None
    through: datetime | None = None
    anchor: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None
    progress: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("target_start", "floor", "through", "anchor", "started_at", "finished_at"):
            _aware(getattr(self, name), name)


class ActionKind(StrEnum):
    NONE = "NONE"
    CREATE = "CREATE"
    TRANSITION = "TRANSITION"
    STEP = "STEP"


@dataclass(frozen=True)
class ScopeAction:
    """What a tick does to one scope. `trigger` is applied through `next_state`."""

    kind: ActionKind
    state: HistoryState | None = None
    trigger: HistoryTrigger | None = None
    reason: str = ""
    anchor: datetime | None = None


def _none(reason: str) -> ScopeAction:
    return ScopeAction(ActionKind.NONE, reason=reason)


def _moving(
    kind: ActionKind,
    current: HistoryState,
    trigger: HistoryTrigger,
    reason: str | None = None,
    anchor: datetime | None = None,
) -> ScopeAction:
    change = next_state(current, trigger)
    return ScopeAction(
        kind, change.state, trigger, bounded_reason(reason or change.reason), anchor
    )


def _check_live(has_live_data: bool, live_anchor: datetime | None, family: HistoryFamily) -> None:
    _aware(live_anchor, "live_anchor")
    if family in _PER_SOURCE:
        return
    if has_live_data != (live_anchor is not None):
        raise LifecycleError(
            "a console-wide scope has live data exactly when it has a live anchor"
        )


def scope_action(
    *,
    family: HistoryFamily,
    mode: ConsoleMode,
    decision: DecisionView | None,
    row: ScopeRowView | None,
    has_live_data: bool,
    live_anchor: datetime | None,
    switch_on: bool,
) -> ScopeAction:
    """What the next tick does to a scope. The rules are applied in this order."""
    if family not in SCOPE_TABLE_FAMILIES:
        raise LifecycleError(f"{family.value} has no scope row")
    _check_live(has_live_data, live_anchor, family)
    console_wide = family not in _PER_SOURCE
    current = row.state if row is not None else HistoryState.NOT_REQUESTED

    # 1. Undecided is not Yes; a No reads nothing. Nothing is created.
    if mode in (ConsoleMode.UNDECIDED, ConsoleMode.NO):
        return _none("the console has no Yes answer")
    if decision is None:
        raise LifecycleError("an ACTIVE or STOPPED console has a decision")

    # 2. The kill switch (§B.6.1): a reading scope ends PARTIAL, nothing is created.
    if not switch_on:
        if current in _READING_OR_WAITING:
            return _moving(
                ActionKind.TRANSITION, current, HistoryTrigger.SWITCHED_OFF, SWITCHED_OFF_REASON
            )
        return _none("history reading is switched off")

    # 3. The operator's stop request: a reading scope ends PARTIAL, nothing is created.
    if mode is ConsoleMode.STOPPED:
        if current in _READING_OR_WAITING:
            by = decision.stop_requested_by or "an operator"
            return _moving(
                ActionKind.TRANSITION,
                current,
                HistoryTrigger.STOPPED_BY_OPERATOR,
                f"stopped by {by}",
            )
        return _none("an operator stopped history reading")

    # 4. ACTIVE, no row: §B.5 says which family may be created.
    if row is None:
        if console_wide:
            if has_live_data:
                return _moving(
                    ActionKind.CREATE,
                    current,
                    HistoryTrigger.REQUEST_READY,
                    anchor=live_anchor,
                )
            return _moving(ActionKind.CREATE, current, HistoryTrigger.REQUEST_AWAITING_ANCHOR)
        if has_live_data:
            return _moving(
                ActionKind.CREATE, current, HistoryTrigger.NOT_ELIGIBLE, NOT_ELIGIBLE_REASON
            )
        return _moving(ActionKind.CREATE, current, HistoryTrigger.REQUEST_READY)

    # 5. ACTIVE, a row.
    if current is HistoryState.WAITING:
        if has_live_data:
            return _moving(
                ActionKind.TRANSITION, current, HistoryTrigger.ANCHOR_FOUND, anchor=live_anchor
            )
        return _none("the scope waits for its first live bucket")
    if current is HistoryState.READING:
        return ScopeAction(
            ActionKind.STEP, HistoryState.READING, None, "the scope reads its next step"
        )
    if current in _FINISHED:
        started = decision.start_requested_at
        if started is None:
            return _none("the scope finished and nobody asked to read again")
        if row.finished_at is None:
            raise LifecycleError(f"a {current.value} scope has no finished_at")
        if started <= row.finished_at:
            return _none("the scope finished after the last request to read history")
        if not console_wide:
            return _none("a finished source has live data; history cannot be read again")
        return _moving(
            ActionKind.TRANSITION,
            current,
            HistoryTrigger.RESTART,
            RESTART_REASON,
            anchor=live_anchor,
        )
    return _none("the scope is not eligible for history")


# --------------------------------------------------------------------------- rows


def _check_key(family: HistoryFamily, scope_key: str) -> None:
    if family not in SCOPE_TABLE_FAMILIES:
        raise LifecycleError(f"{family.value} has no scope row")
    if (family in _PER_SOURCE) != (scope_key != ""):
        raise LifecycleError("WAF and IPS scopes have a source key and the others have none")


def _require_decision(decision: DecisionView) -> int:
    if decision.days is None:
        raise LifecycleError("a scope is created from a Yes decision")
    return decision.days


def new_scope_row(
    action: ScopeAction,
    *,
    instance_id: Any,
    family: HistoryFamily,
    scope_key: str,
    decision: DecisionView,
    at: datetime,
) -> dict[str, Any]:
    """The full column mapping of a `CREATE` action (`progress = {}`: nothing searched)."""
    if action.kind is not ActionKind.CREATE or action.state is None:
        raise LifecycleError("new_scope_row needs a CREATE action")
    _check_key(family, scope_key)
    _aware(at, "at")
    days = _require_decision(decision)
    reading = action.state is HistoryState.READING
    finished = action.state is HistoryState.NOT_ELIGIBLE
    return {
        "instance_id": instance_id,
        "family": family.value,
        "scope_key": scope_key,
        "state": action.state.value,
        "days_requested": days,
        "days_target": None,
        "target_start": None,
        "floor": None,
        "through": None,
        "anchor": action.anchor,
        "started_at": at if reading else None,
        "finished_at": at if finished else None,
        "reason": bounded_reason(action.reason),
        "progress": {},
    }


def transition_row(
    action: ScopeAction,
    *,
    instance_id: Any,
    family: HistoryFamily,
    scope_key: str,
    row: ScopeRowView,
    decision: DecisionView,
    at: datetime,
) -> dict[str, Any]:
    """The full column mapping after a `TRANSITION` action; it keeps what the row holds."""
    if action.kind is not ActionKind.TRANSITION or action.state is None:
        raise LifecycleError("transition_row needs a TRANSITION action")
    _check_key(family, scope_key)
    _aware(at, "at")
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "family": family.value,
        "scope_key": scope_key,
        "state": action.state.value,
        "days_requested": decision.days if decision.days is not None else row.days_requested,
        "days_target": row.days_target,
        "target_start": row.target_start,
        "floor": row.floor,
        "through": row.through,
        "anchor": row.anchor,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "reason": bounded_reason(action.reason),
        "progress": dict(row.progress) if row.progress is not None else {},
    }
    if action.state is HistoryState.READING:
        # Empty progress says the new reading has searched nothing yet.  Keep the earlier
        # reading's window and evidence visible until the first step replaces them with its
        # own; a stop before that step must not erase what the aggregate tables still hold.
        values.update(
            anchor=action.anchor if action.anchor is not None else row.anchor,
            started_at=at,
            finished_at=None,
            progress={},
        )
    else:
        values["finished_at"] = at
    return values


# --------------------------------------------------------------------------- progress


def decode_scope(row: ScopeRowView) -> DecodedProgress | None:
    """The row's progress and adapter state, or None while the scope has not searched.

    `progress` is `{}` exactly while the scope has not searched in its current reading:
    `WAITING`, `NOT_ELIGIBLE`, a `READING` scope before its first step, or one that finished
    before it. A restarted scope may still expose the preceding reading's window and evidence
    columns until its first step replaces them. Otherwise progress decodes with
    `decode_progress` and agrees with the row's `target_start`. A contradiction raises
    `LifecycleError`; nothing is guessed.
    """
    body = row.progress
    if body is None:
        raise LifecycleError("a scope row has progress ({} while nothing was searched)")
    if len(body) == 0:
        return None
    if row.state in (HistoryState.WAITING, HistoryState.NOT_ELIGIBLE):
        raise LifecycleError(f"a {row.state.value} scope has not searched, so progress is {{}}")
    if row.target_start is None:
        raise LifecycleError("progress is set but the row has no target_start")
    decoded = decode_progress(body)
    if decoded.progress.target_start != row.target_start:
        raise LifecycleError("progress and the row disagree on target_start")
    return decoded


def scope_progress(row: ScopeRowView) -> ScopeProgress | None:
    """The planner's progress of a scope, or None while it has not searched."""
    decoded = decode_scope(row)
    return None if decoded is None else decoded.progress
