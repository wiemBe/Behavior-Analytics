"""The scope lifecycle contract (HISTORY-BACKFILL §B.5, §B.6.1, §B.7.1, §B.7.2; HB-2b).

The collector is the only writer of `history_backfill_scope`: each family adapter creates,
moves and updates its own scope rows inside its collection tick. The API and the CLI write
only `history_backfill_decision`; "Stop reading" and "Read history now" are *requests* on
that row (`stop_requested_*`, `start_requested_*`), applied at the family's next tick.

`scope_action` is the one function that says what happens to a scope, so the five family
adapters cannot diverge on §B.5. `new_scope_row`, `transition_row` and `step_row` build the
full column mapping the repository's `write_scopes` takes, so an adapter never assembles a
row by hand: a create or a state change goes through the first two, and a step that leaves
the state alone starts from `scope_row` (the row as it stands) and names what it changes.
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
from types import SimpleNamespace
from typing import Any, overload

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
    "last_error_class",
    "last_error_at",
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


@overload
def decision_view(row: None) -> None: ...


@overload
def decision_view(row: object) -> DecisionView: ...


def decision_view(row: object | None) -> DecisionView | None:
    """The lifecycle's view of a decision row, from an object or a mapping.

    ``None`` stays ``None``. An attribute row copies ``answer`` as stored. A
    mapping's ``answer`` is read as text, which is what a freshly built column
    dict holds. Times are copied as given: a caller that stores naive times
    coerces them before calling this.
    """
    if row is None:
        return None
    source: Any = row
    if isinstance(source, Mapping):
        answer = str(source["answer"])
        days = source["days"]
        start_requested_at = source["start_requested_at"]
        start_requested_by = source["start_requested_by"]
        stop_requested_at = source["stop_requested_at"]
        stop_requested_by = source["stop_requested_by"]
    else:
        answer = source.answer
        days = source.days
        start_requested_at = source.start_requested_at
        start_requested_by = source.start_requested_by
        stop_requested_at = source.stop_requested_at
        stop_requested_by = source.stop_requested_by
    return DecisionView(
        answer=answer,
        days=days,
        start_requested_at=start_requested_at,
        start_requested_by=start_requested_by,
        stop_requested_at=stop_requested_at,
        stop_requested_by=stop_requested_by,
    )


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
        "last_error_class": None,
        "last_error_at": None,
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
        "last_error_class": None,
        "last_error_at": None,
        "progress": dict(row.progress) if row.progress is not None else {},
    }
    if action.state is HistoryState.READING:
        # The new reading has not searched. Keep the earlier reading's progress as well as
        # its window columns: the page still shows those floors, searches and days until the
        # first step replaces them, and a stop before that step leaves them (HB-12 D5,
        # HB-22 D2a). `decode_scope` treats that progress as not the current reading, because
        # its `started_at` is older than the one this transition writes.
        values.update(
            anchor=action.anchor if action.anchor is not None else row.anchor,
            started_at=at,
            finished_at=None,
        )
    else:
        values["finished_at"] = at
    return values


@overload
def scope_view(row: None) -> None: ...


@overload
def scope_view(row: Mapping[str, Any]) -> ScopeRowView: ...


@overload
def scope_view(row: object) -> ScopeRowView: ...


def scope_view(row: object | None) -> ScopeRowView | None:
    """The lifecycle's view of a scope row, from an object or a column mapping.

    The stored row (the ORM object the repository hands back) is the usual case;
    a mapping is the row a family has just built. `None` reads as `None`.
    `state` is read as a `HistoryState`, so a caller never compares raw strings
    against the lifecycle's states.
    """
    if row is None:
        return None
    source: Any = SimpleNamespace(**row) if isinstance(row, Mapping) else row
    return ScopeRowView(
        state=HistoryState(source.state),
        days_requested=source.days_requested,
        days_target=source.days_target,
        target_start=source.target_start,
        floor=source.floor,
        through=source.through,
        anchor=source.anchor,
        started_at=source.started_at,
        finished_at=source.finished_at,
        reason=source.reason,
        progress=source.progress,
    )


def scope_row(
    view: ScopeRowView,
    *,
    instance_id: Any,
    family: HistoryFamily,
    scope_key: str,
) -> dict[str, Any]:
    """The full column mapping of a scope row as it stands, from a view of it.

    The lifecycle's `new_scope_row` and `transition_row` build the rows a
    scope's *state change* writes. A step that leaves the state alone (or
    settles it) needs this third builder; it writes every column the row holds,
    and the repository refuses a row that lacks one. `progress` is copied, never
    aliased, and the error marker is cleared: a written step clears it (HB-16).
    """
    _check_key(family, scope_key)
    return {
        "instance_id": instance_id,
        "family": family.value,
        "scope_key": scope_key,
        "state": view.state.value,
        "days_requested": view.days_requested,
        "days_target": view.days_target,
        "target_start": view.target_start,
        "floor": view.floor,
        "through": view.through,
        "anchor": view.anchor,
        "started_at": view.started_at,
        "finished_at": view.finished_at,
        "reason": view.reason,
        "last_error_class": None,
        "last_error_at": None,
        "progress": dict(view.progress) if view.progress else {},
    }


def step_row(base: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    """A copy of the scope row `base` with only the named columns replaced.

    A step names at its call site the columns it changes; everything else stays
    as the base holds it. `reason` always goes through `bounded_reason`, `state`
    accepts a `HistoryState`, `progress` is copied, never aliased, and the error
    marker is cleared (a written step clears it, HB-16). An unknown column name
    raises `LifecycleError`, and the result must hold exactly `SCOPE_COLUMNS`.
    """
    unknown = set(changes) - set(SCOPE_COLUMNS)
    if unknown:
        raise LifecycleError(f"unknown scope columns {sorted(unknown)}")
    values = dict(base)
    values.update(changes)
    if isinstance(values.get("state"), HistoryState):
        values["state"] = values["state"].value
    if "reason" in changes:
        values["reason"] = bounded_reason(changes["reason"])
    if "progress" in changes:
        values["progress"] = dict(changes["progress"]) if changes["progress"] else {}
    values["last_error_class"] = None
    values["last_error_at"] = None
    missing = set(SCOPE_COLUMNS) - set(values)
    if missing or set(values) - set(SCOPE_COLUMNS):
        raise LifecycleError(
            f"scope row differs from the columns: missing {sorted(missing)}, "
            f"unknown {sorted(set(values) - set(SCOPE_COLUMNS))}"
        )
    return values


def deferred_scope_row(
    stored: Any,
    *,
    instance_id: Any,
    family: HistoryFamily,
    scope_key: str,
    reason: str,
) -> dict[str, Any]:
    """Keep a stored step exactly where it is while naming a capacity deferral.

    Unlike an ordinary written step, a capacity refusal is not a successful step and
    therefore must not clear the previous provider-fault marker.
    """

    view = scope_view(stored)
    if view is None:
        raise LifecycleError("a deferred history step needs a stored scope")
    values = scope_row(
        view,
        instance_id=instance_id,
        family=family,
        scope_key=scope_key,
    )
    values["reason"] = bounded_reason(reason)
    values["last_error_class"] = stored.last_error_class
    values["last_error_at"] = stored.last_error_at
    return values


# --------------------------------------------------------------------------- progress


def decode_scope(row: ScopeRowView) -> DecodedProgress | None:
    """The row's progress and adapter state, or None while the scope has not searched.

    `progress` is `{}` exactly while the scope has not searched in its current reading:
    `WAITING`, `NOT_ELIGIBLE`, a `READING` scope before its first step, or one that finished
    before it. A restarted scope keeps the preceding reading's progress until its first step
    replaces it. That body is not this reading's: its `started_at` is older than the row's,
    and this returns None so the collector plans a fresh window. The page reads the stored
    body directly and still shows it. Otherwise progress decodes with `decode_progress` and
    agrees with the row's `target_start`. A row whose reason is still the restart
    reason has not taken that step either, including when the clock did not move
    and the body's `started_at` equals the row's. A contradiction raises
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
    if row.started_at is not None and decoded.progress.started_at < row.started_at:
        return None
    # A restart keeps this reason until the first step replaces it. The body's
    # started_at can equal the row's when the collector clock does not move, and
    # that body is still the finished reading (catch-all, rule matches).
    if row.reason == RESTART_REASON:
        return None
    if decoded.progress.target_start != row.target_start:
        raise LifecycleError("progress and the row disagree on target_start")
    return decoded


def scope_progress(row: ScopeRowView) -> ScopeProgress | None:
    """The planner's progress of a scope, or None while it has not searched."""
    decoded = decode_scope(row)
    return None if decoded is None else decoded.progress
