"""The history state machine: every transition, legal and illegal."""

from __future__ import annotations

import itertools

import pytest

from app.history.state import (
    SCOPE_TABLE_FAMILIES,
    HistoryFamily,
    HistoryState,
    HistoryTrigger,
    IllegalTransitionError,
    all_transitions,
    is_finished,
    legal_triggers,
    next_state,
)

S = HistoryState
T = HistoryTrigger

# The whole machine, written out independently of the module's table.
EXPECTED: dict[tuple[HistoryState, HistoryTrigger], HistoryState] = {
    (S.NOT_REQUESTED, T.REQUEST_AWAITING_ANCHOR): S.WAITING,
    (S.NOT_REQUESTED, T.REQUEST_READY): S.READING,
    (S.NOT_REQUESTED, T.NOT_ELIGIBLE): S.NOT_ELIGIBLE,
    (S.NOT_REQUESTED, T.SWITCHED_OFF): S.PARTIAL,
    (S.WAITING, T.ANCHOR_FOUND): S.READING,
    (S.WAITING, T.NOT_ELIGIBLE): S.NOT_ELIGIBLE,
    (S.WAITING, T.NOTHING_TO_READ): S.UNAVAILABLE,
    (S.WAITING, T.SWITCHED_OFF): S.PARTIAL,
    (S.WAITING, T.STOPPED_BY_OPERATOR): S.PARTIAL,
    (S.READING, T.NOTHING_TO_READ): S.UNAVAILABLE,
    (S.READING, T.WINDOW_TOO_SMALL): S.UNAVAILABLE,
    (S.READING, T.FINISHED_COMPLETE): S.COMPLETE,
    (S.READING, T.FINISHED_PARTIAL): S.PARTIAL,
    (S.READING, T.DENSITY_GATE): S.PARTIAL,
    (S.READING, T.TOO_MANY_GAPS): S.PARTIAL,
    (S.READING, T.TIME_LIMIT): S.PARTIAL,
    (S.READING, T.SWITCHED_OFF): S.PARTIAL,
    (S.READING, T.STOPPED_BY_OPERATOR): S.PARTIAL,
    (S.READING, T.RESTART): S.READING,
    (S.COMPLETE, T.RESTART): S.READING,
    (S.PARTIAL, T.RESTART): S.READING,
    (S.UNAVAILABLE, T.RESTART): S.READING,
}

ALL_PAIRS = list(itertools.product(S, T))
LEGAL = [pair for pair in ALL_PAIRS if pair in EXPECTED]
ILLEGAL = [pair for pair in ALL_PAIRS if pair not in EXPECTED]


def test_the_matrix_covers_every_pair() -> None:
    assert len(LEGAL) == len(EXPECTED) == 22
    assert len(LEGAL) + len(ILLEGAL) == len(S) * len(T)


@pytest.mark.parametrize(("state", "trigger"), LEGAL)
def test_legal_transition_moves_with_a_reason(state: S, trigger: T) -> None:
    change = next_state(state, trigger)
    assert change.state is EXPECTED[(state, trigger)]
    assert change.previous is state
    assert change.trigger is trigger
    assert len(change.reason.split()) >= 4  # words, not a code


@pytest.mark.parametrize(("state", "trigger"), ILLEGAL)
def test_illegal_transition_is_refused(state: S, trigger: T) -> None:
    with pytest.raises(IllegalTransitionError, match=state.value):
        next_state(state, trigger)


def test_not_eligible_is_a_dead_end() -> None:
    assert legal_triggers(S.NOT_ELIGIBLE) == frozenset()
    assert is_finished(S.NOT_ELIGIBLE)


def test_no_trigger_leaves_a_finished_state_except_restart() -> None:
    for state in (S.COMPLETE, S.PARTIAL, S.UNAVAILABLE):
        assert legal_triggers(state) == {T.RESTART}


def test_only_a_reading_scope_can_finish_complete() -> None:
    sources = {s for (s, t), to in EXPECTED.items() if to is S.COMPLETE}
    assert sources == {S.READING}


def test_all_transitions_lists_every_legal_pair_once() -> None:
    table = all_transitions()
    assert set(table) == set(EXPECTED)
    assert len({c.reason for c in table.values()}) >= 18


def test_scope_table_families_match_the_design() -> None:
    assert {f.value for f in SCOPE_TABLE_FAMILIES} == {
        "PHASE_A",
        "WAF",
        "IPS",
        "CATCHALL",
        "RULE_MATCH",
    }
    assert HistoryFamily.PROPERTY_WATCH not in SCOPE_TABLE_FAMILIES
