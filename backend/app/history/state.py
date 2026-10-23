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


S = HistoryState
T = HistoryTrigger

_FINISHED = (S.COMPLETE, S.PARTIAL, S.UNAVAILABLE)

# (state, trigger) -> (next state, the reason in words)
_TRANSITIONS: dict[tuple[HistoryState, HistoryTrigger], tuple[HistoryState, str]] = {
    (S.NOT_REQUESTED, T.REQUEST_AWAITING_ANCHOR): (
        S.WAITING,
        "history was requested, and the scope waits for its first live bucket (the anchor)",
    ),
    (S.NOT_REQUESTED, T.REQUEST_READY): (
        S.READING,
        "history was requested, and the scope can be planned at its next collection",
    ),
    (S.NOT_REQUESTED, T.NOT_ELIGIBLE): (
        S.NOT_ELIGIBLE,
        "the scope already has live buckets, and a prepend would move first_seen_at (§B.5)",
    ),
    (S.WAITING, T.ANCHOR_FOUND): (S.READING, "the first live bucket exists; reading can start"),
    (S.WAITING, T.NOT_ELIGIBLE): (
        S.NOT_ELIGIBLE,
        "the scope turned out to have live buckets that history cannot precede",
    ),
    (S.READING, T.NOTHING_TO_READ): (
        S.UNAVAILABLE,
        "QRadar holds no event of the scope in the window, or the floor scan could not be done",
    ),
    (S.WAITING, T.NOTHING_TO_READ): (
        S.UNAVAILABLE,
        "QRadar holds no event of the scope in the window",
    ),
    (S.READING, T.WINDOW_TOO_SMALL): (
        S.UNAVAILABLE,
        "the estimate exceeds the time limit even at the smallest window",
    ),
    (S.READING, T.FINISHED_COMPLETE): (
        S.COMPLETE,
        "the reading reached the end; the floor is within one bucket of the asked start "
        "and no gap exists",
    ),
    (S.READING, T.FINISHED_PARTIAL): (
        S.PARTIAL,
        "the reading reached the end, but QRadar holds less than asked or a gap exists",
    ),
    (S.READING, T.DENSITY_GATE): (
        S.PARTIAL,
        "too many buckets would be lower bounds; reading further would store only those",
    ),
    (S.READING, T.TOO_MANY_GAPS): (
        S.PARTIAL,
        "the gap list is full; the scope has too many unreadable spans",
    ),
    (S.READING, T.TIME_LIMIT): (
        S.PARTIAL,
        "the reading did not finish inside its time limit",
    ),
    (S.NOT_REQUESTED, T.SWITCHED_OFF): (
        S.PARTIAL,
        "history reading is switched off; the scope starts live",
    ),
    (S.WAITING, T.SWITCHED_OFF): (S.PARTIAL, "history reading was switched off while waiting"),
    (S.READING, T.SWITCHED_OFF): (S.PARTIAL, "history reading was switched off"),
    (S.WAITING, T.STOPPED_BY_OPERATOR): (S.PARTIAL, "stopped by an operator while waiting"),
    (S.READING, T.STOPPED_BY_OPERATOR): (S.PARTIAL, "stopped by an operator"),
    **{
        (state, T.RESTART): (
            S.READING,
            "the reading restarts: a new answer, a topped-up source or a new watch version",
        )
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
    target, reason = entry
    return StateChange(previous=current, state=target, trigger=trigger, reason=reason)


def legal_triggers(current: HistoryState) -> frozenset[HistoryTrigger]:
    """Test-only: the triggers a state accepts. Production uses ``next_state``."""
    return frozenset(t for (s, t) in _TRANSITIONS if s is current)


def all_transitions() -> dict[tuple[HistoryState, HistoryTrigger], StateChange]:
    """Test-only: every legal transition. No admin page reads this table."""
    return {key: next_state(*key) for key in _TRANSITIONS}


def is_finished(state: HistoryState) -> bool:
    """Test-only: a finished reading. Production compares states directly."""
    return state in (S.COMPLETE, S.PARTIAL, S.UNAVAILABLE, S.NOT_ELIGIBLE)
