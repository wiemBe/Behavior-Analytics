"""The history-backfill state machine (HISTORY-BACKFILL §B.7.1, PHASE-G §H.3.4, §H.5.4).

A scope (one console and family, or one source, or one property watch) is in exactly
one `HistoryState`. A state changes only through a `HistoryTrigger` listed in
`_TRANSITIONS`; each entry says in words why it moves. `next_state` refuses any other
pair, so a caller cannot invent a transition, and no transition leaves a scope in a
state that reads as healthy: `COMPLETE`, `PARTIAL`, `UNAVAILABLE` and `NOT_ELIGIBLE` are
honesty states, never a verdict.

Pure: no database, no clock, no settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.i18n.messages import MessageRef, render


class HistoryFamily(StrEnum):
    """The families that read their past (§B.2). Geo is left out on purpose."""

    PHASE_A = "PHASE_A"
    WAF = "WAF"
    IPS = "IPS"
    CATCHALL = "CATCHALL"
    RULE_MATCH = "RULE_MATCH"
    # Property watches keep their own columns (PHASE-G §H.5.1), not a scope-table row;
    # the member lets the shared core name them (§B.11).
    PROPERTY_WATCH = "PROPERTY_WATCH"


# The families stored in `history_backfill_scope` (§B.7.1): the table's CHECK takes these.
SCOPE_TABLE_FAMILIES: frozenset[HistoryFamily] = frozenset(
    set(HistoryFamily) - {HistoryFamily.PROPERTY_WATCH}
)


class HistoryState(StrEnum):
    """§B.7.1 (scope table) and §H.5.1 (property watch) name these states."""

    NOT_REQUESTED = "NOT_REQUESTED"
    WAITING = "WAITING"
    READING = "READING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class HistoryTrigger(StrEnum):
    """Why a scope changes state. The planner returns one; the adapter applies it."""

    REQUEST_AWAITING_ANCHOR = "REQUEST_AWAITING_ANCHOR"
    REQUEST_READY = "REQUEST_READY"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ANCHOR_FOUND = "ANCHOR_FOUND"
    NOTHING_TO_READ = "NOTHING_TO_READ"
    WINDOW_TOO_SMALL = "WINDOW_TOO_SMALL"
    FINISHED_COMPLETE = "FINISHED_COMPLETE"
    FINISHED_PARTIAL = "FINISHED_PARTIAL"
    DENSITY_GATE = "DENSITY_GATE"
    TOO_MANY_GAPS = "TOO_MANY_GAPS"
    TIME_LIMIT = "TIME_LIMIT"
    SWITCHED_OFF = "SWITCHED_OFF"
    STOPPED_BY_OPERATOR = "STOPPED_BY_OPERATOR"
    RESTART = "RESTART"


class IllegalTransitionError(ValueError):
    """A trigger that the current state does not accept."""


@dataclass(frozen=True)
class StateChange:
    """The outcome of an accepted trigger."""

    previous: HistoryState
    state: HistoryState
    trigger: HistoryTrigger
    reason: str
    #: The registry reference of `reason` (MSG-8a): every transition is coded.
    reason_message: MessageRef


S = HistoryState
T = HistoryTrigger

_FINISHED = (S.COMPLETE, S.PARTIAL, S.UNAVAILABLE)

# (state, trigger) -> (next state, the reason in words as a coded reference)
_TRANSITIONS: dict[tuple[HistoryState, HistoryTrigger], tuple[HistoryState, MessageRef]] = {
    (S.NOT_REQUESTED, T.REQUEST_AWAITING_ANCHOR): (
        S.WAITING,
        MessageRef("history.state.request_awaiting_anchor"),
    ),
    (S.NOT_REQUESTED, T.REQUEST_READY): (
        S.READING,
        MessageRef("history.state.request_ready"),
    ),
    (S.NOT_REQUESTED, T.NOT_ELIGIBLE): (
        S.NOT_ELIGIBLE,
        MessageRef("history.state.not_eligible_preceded"),
    ),
    (S.WAITING, T.ANCHOR_FOUND): (S.READING, MessageRef("history.state.anchor_found")),
    (S.WAITING, T.NOT_ELIGIBLE): (
        S.NOT_ELIGIBLE,
        MessageRef("history.state.not_eligible_late"),
    ),
    (S.READING, T.NOTHING_TO_READ): (
        S.UNAVAILABLE,
        MessageRef("history.state.nothing_to_read_scan"),
    ),
    (S.WAITING, T.NOTHING_TO_READ): (
        S.UNAVAILABLE,
        MessageRef("history.state.nothing_to_read"),
    ),
    (S.READING, T.WINDOW_TOO_SMALL): (
        S.UNAVAILABLE,
        MessageRef("history.state.window_too_small"),
    ),
    (S.READING, T.FINISHED_COMPLETE): (
        S.COMPLETE,
        MessageRef("history.state.finished_complete"),
    ),
    (S.READING, T.FINISHED_PARTIAL): (
        S.PARTIAL,
        MessageRef("history.state.finished_partial"),
    ),
    (S.READING, T.DENSITY_GATE): (
        S.PARTIAL,
        MessageRef("history.state.density_gate"),
    ),
    (S.READING, T.TOO_MANY_GAPS): (
        S.PARTIAL,
        MessageRef("history.state.too_many_gaps"),
    ),
    (S.READING, T.TIME_LIMIT): (
        S.PARTIAL,
        MessageRef("history.state.time_limit"),
    ),
    (S.NOT_REQUESTED, T.SWITCHED_OFF): (
        S.PARTIAL,
        MessageRef("history.state.switched_off_start"),
    ),
    (S.WAITING, T.SWITCHED_OFF): (
        S.PARTIAL,
        MessageRef("history.state.switched_off_waiting"),
    ),
    (S.READING, T.SWITCHED_OFF): (S.PARTIAL, MessageRef("history.state.switched_off")),
    (S.WAITING, T.STOPPED_BY_OPERATOR): (
        S.PARTIAL,
        MessageRef("history.state.stopped_waiting"),
    ),
    (S.READING, T.STOPPED_BY_OPERATOR): (S.PARTIAL, MessageRef("history.state.stopped")),
    **{
        (state, T.RESTART): (S.READING, MessageRef("history.state.restart"))
        for state in (S.READING, *_FINISHED)
    },
}


def next_state(current: HistoryState, trigger: HistoryTrigger) -> StateChange:
    """Apply a trigger, or raise `IllegalTransitionError` naming both."""
    entry = _TRANSITIONS.get((current, trigger))
    if entry is None:
        raise IllegalTransitionError(
            f"history state {current.value} does not accept {trigger.value}"
        )
    target, message = entry
    return StateChange(
        previous=current,
        state=target,
        trigger=trigger,
        reason=render(message),
        reason_message=message,
    )


def legal_triggers(current: HistoryState) -> frozenset[HistoryTrigger]:
    """Test-only: the triggers a state accepts. Production uses ``next_state``."""
    return frozenset(t for (s, t) in _TRANSITIONS if s is current)


def all_transitions() -> dict[tuple[HistoryState, HistoryTrigger], StateChange]:
    """Test-only: every legal transition. No admin page reads this table."""
    return {key: next_state(*key) for key in _TRANSITIONS}


def is_finished(state: HistoryState) -> bool:
    """Test-only: a finished reading. Production compares states directly."""
    return state in (S.COMPLETE, S.PARTIAL, S.UNAVAILABLE, S.NOT_ELIGIBLE)
