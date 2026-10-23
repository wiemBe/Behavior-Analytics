"""The scope lifecycle contract (HB-2b): modes, the action per rule, the rows, the `{}` rule."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.history.lifecycle import (
    ANSWER_NO,
    ANSWER_YES,
    MAX_REASON_LENGTH,
    NOT_ELIGIBLE_REASON,
    SCOPE_COLUMNS,
    ActionKind,
    ConsoleMode,
    DecisionView,
    LifecycleError,
    ScopeAction,
    ScopeRowView,
    bounded_reason,
    console_mode,
    decision_view,
    new_scope_row,
    scope_action,
    scope_progress,
    scope_view,
    transition_row,
)
from app.history.planner import PlannerSettings, new_progress
from app.history.progress_codec import ProgressCodecError, encode_progress
from app.history.state import HistoryFamily, HistoryState, HistoryTrigger, next_state
from app.models.history_backfill import HistoryBackfillScope

F = HistoryFamily
S = HistoryState
T = HistoryTrigger
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
ANCHOR = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
CONSOLE_WIDE = [F.PHASE_A, F.CATCHALL, F.RULE_MATCH]
PER_SOURCE = [F.WAF, F.IPS]
SCOPE_FAMILIES = CONSOLE_WIDE + PER_SOURCE
YES = DecisionView(ANSWER_YES, 28)
NO = DecisionView(ANSWER_NO)
INSTANCE = uuid.uuid4()


def key(family: F) -> str:
    return "17" if family in PER_SOURCE else ""


def row(state: S, **extra: object) -> ScopeRowView:
    return ScopeRowView(state=state, progress={}, **extra)  # type: ignore[arg-type]


def act(family, mode, *, decision=YES, scope=None, live=False, switch=True):  # type: ignore[no-untyped-def]
    wide = family in CONSOLE_WIDE
    return scope_action(
        family=family,
        mode=mode,
        decision=decision,
        row=scope,
        has_live_data=live,
        live_anchor=ANCHOR if live and wide else None,
        switch_on=switch,
    )


def stopped_decision(**extra: object) -> DecisionView:
    return DecisionView("YES", 28, stop_requested_at=NOW, stop_requested_by="ayse", **extra)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the mode


def test_console_mode_of_each_decision() -> None:
    assert console_mode(None) is ConsoleMode.UNDECIDED
    assert console_mode(NO) is ConsoleMode.NO
    assert console_mode(YES) is ConsoleMode.ACTIVE


def test_a_stop_then_a_start_is_active_again() -> None:
    view = DecisionView(
        "YES", 28,
        stop_requested_at=NOW, stop_requested_by="a",
        start_requested_at=NOW + timedelta(minutes=1), start_requested_by="b",
    )
    assert console_mode(view) is ConsoleMode.ACTIVE


def test_a_start_then_a_stop_is_stopped() -> None:
    view = DecisionView(
        "YES", 28,
        start_requested_at=NOW, start_requested_by="a",
        stop_requested_at=NOW + timedelta(minutes=1), stop_requested_by="b",
    )
    assert console_mode(view) is ConsoleMode.STOPPED


def test_a_stop_alone_is_stopped() -> None:
    assert console_mode(stopped_decision()) is ConsoleMode.STOPPED


def test_the_tie_rule_equal_times_read_active() -> None:
    view = DecisionView(
        "YES", 28,
        start_requested_at=NOW, start_requested_by="a",
        stop_requested_at=NOW, stop_requested_by="b",
    )
    assert console_mode(view) is ConsoleMode.ACTIVE


class _TextAnswer:
    def __str__(self) -> str:
        return "NO"


def test_decision_view_reads_none_attributes_and_mappings() -> None:
    assert decision_view(None) is None
    attributes = decision_view(
        type(
            "Row",
            (),
            {
                "answer": "YES",
                "days": 28,
                "start_requested_at": NOW,
                "start_requested_by": "a",
                "stop_requested_at": None,
                "stop_requested_by": None,
            },
        )()
    )
    assert attributes == DecisionView(
        "YES", 28, start_requested_at=NOW, start_requested_by="a"
    )
    mapped = decision_view(
        {
            "answer": _TextAnswer(),
            "days": None,
            "start_requested_at": None,
            "start_requested_by": None,
            "stop_requested_at": None,
            "stop_requested_by": None,
        }
    )
    assert mapped == DecisionView("NO")


def test_scope_view_reads_none_attributes_and_mappings() -> None:
    assert scope_view(None) is None
    columns = {
        "state": "WAITING",
        "days_requested": None,
        "days_target": 7,
        "target_start": None,
        "floor": None,
        "through": None,
        "anchor": None,
        "started_at": None,
        "finished_at": None,
        "reason": "waiting",
        "progress": {},
    }
    from_attributes = scope_view(type("Scope", (), columns)())
    from_mapping = scope_view(columns)
    assert from_attributes == from_mapping
    assert from_mapping is not None
    assert from_mapping.state is S.WAITING
    assert from_mapping.days_target == 7
    assert from_mapping.reason == "waiting"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"answer": "MAYBE"},
        {"answer": "YES", "days": None},
        {"answer": "NO", "days": 28},
        {"answer": "YES", "days": 28, "stop_requested_at": NOW},
        {"answer": "YES", "days": 28, "start_requested_by": "a"},
        {"answer": "YES", "days": 28, "stop_requested_at": datetime(2026, 1, 1),
         "stop_requested_by": "a"},
        {"answer": "NO", "stop_requested_at": NOW, "stop_requested_by": "a"},
    ],
)
def test_a_contradictory_decision_view_raises(kwargs: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(LifecycleError):
        DecisionView(**kwargs)


# --------------------------------------------------------------------------- rules 1 to 3


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("mode", [ConsoleMode.UNDECIDED, ConsoleMode.NO])
@pytest.mark.parametrize("state", [None, S.WAITING, S.READING, S.COMPLETE])
def test_undecided_and_no_do_nothing(family, live, mode, state) -> None:  # type: ignore[no-untyped-def]
    scope = None if state is None else row(state, finished_at=NOW)
    decision = {ConsoleMode.UNDECIDED: None, ConsoleMode.NO: NO}[mode]
    action = act(family, mode, decision=decision, scope=scope, live=live)
    assert action.kind is ActionKind.NONE


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("state", [S.WAITING, S.READING])
def test_switched_off_ends_a_reading_or_waiting_scope_partial(family, live, state) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE, scope=row(state), live=live, switch=False)
    assert (action.kind, action.state, action.trigger) == (
        ActionKind.TRANSITION, S.PARTIAL, T.SWITCHED_OFF)
    assert action.reason == "history reading was switched off"


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("mode", [ConsoleMode.ACTIVE, ConsoleMode.STOPPED])
@pytest.mark.parametrize("state", [None, S.COMPLETE, S.PARTIAL, S.UNAVAILABLE, S.NOT_ELIGIBLE])
def test_switched_off_creates_and_changes_nothing_else(family, live, mode, state) -> None:  # type: ignore[no-untyped-def]
    scope = None if state is None else row(state, finished_at=NOW - timedelta(days=1))
    decision = YES if mode is ConsoleMode.ACTIVE else stopped_decision()
    assert act(family, mode, decision=decision, scope=scope, live=live,
               switch=False).kind is ActionKind.NONE


def test_the_switch_wins_over_a_stop() -> None:
    action = act(F.WAF, ConsoleMode.STOPPED, decision=stopped_decision(), scope=row(S.READING),
                 switch=False)
    assert action.trigger is T.SWITCHED_OFF


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("state", [S.WAITING, S.READING])
def test_stopped_ends_a_reading_or_waiting_scope_partial_naming_who(family, live, state) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.STOPPED, decision=stopped_decision(), scope=row(state),
                 live=live)
    assert (action.kind, action.state, action.trigger) == (
        ActionKind.TRANSITION, S.PARTIAL, T.STOPPED_BY_OPERATOR)
    assert action.reason == "stopped by ayse"


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("state", [None, S.COMPLETE, S.PARTIAL, S.UNAVAILABLE, S.NOT_ELIGIBLE])
def test_stopped_creates_nothing_and_leaves_a_finished_scope(family, live, state) -> None:  # type: ignore[no-untyped-def]
    scope = None if state is None else row(state, finished_at=NOW - timedelta(days=1))
    action = act(family, ConsoleMode.STOPPED, decision=stopped_decision(), scope=scope, live=live)
    assert action.kind is ActionKind.NONE


def test_a_long_stopper_name_is_bounded() -> None:
    decision = DecisionView("YES", 28, stop_requested_at=NOW, stop_requested_by="x" * 400)
    action = act(F.PHASE_A, ConsoleMode.STOPPED, decision=decision, scope=row(S.READING))
    assert len(action.reason) == MAX_REASON_LENGTH and action.reason.endswith("…")


# --------------------------------------------------------------------------- rule 4


@pytest.mark.parametrize("family", CONSOLE_WIDE)
def test_active_console_wide_without_live_data_creates_waiting(family) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE)
    assert (action.kind, action.state, action.trigger, action.anchor) == (
        ActionKind.CREATE, S.WAITING, T.REQUEST_AWAITING_ANCHOR, None)


@pytest.mark.parametrize("family", CONSOLE_WIDE)
def test_active_console_wide_with_live_data_creates_reading_with_the_anchor(family) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE, live=True)
    assert (action.kind, action.state, action.trigger, action.anchor) == (
        ActionKind.CREATE, S.READING, T.REQUEST_READY, ANCHOR)


@pytest.mark.parametrize("family", PER_SOURCE)
def test_active_per_source_without_live_data_creates_reading_with_a_moving_end(family) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE)
    assert (action.kind, action.state, action.trigger, action.anchor) == (
        ActionKind.CREATE, S.READING, T.REQUEST_READY, None)


@pytest.mark.parametrize("family", PER_SOURCE)
def test_active_per_source_with_live_data_is_not_eligible(family) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE, live=True)
    assert (action.kind, action.state, action.trigger) == (
        ActionKind.CREATE, S.NOT_ELIGIBLE, T.NOT_ELIGIBLE)
    assert action.reason == NOT_ELIGIBLE_REASON
    assert "disable the profile and create it again" in action.reason


# --------------------------------------------------------------------------- rule 5


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
def test_waiting_with_live_data_finds_the_anchor(family) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE, scope=row(S.WAITING), live=True)
    assert (action.kind, action.state, action.trigger) == (
        ActionKind.TRANSITION, S.READING, T.ANCHOR_FOUND)
    assert action.anchor == (ANCHOR if family in CONSOLE_WIDE else None)


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
def test_waiting_without_live_data_waits(family) -> None:  # type: ignore[no-untyped-def]
    assert act(family, ConsoleMode.ACTIVE, scope=row(S.WAITING)).kind is ActionKind.NONE


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
def test_reading_steps(family, live) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE, scope=row(S.READING), live=live)
    assert (action.kind, action.state, action.trigger) == (ActionKind.STEP, S.READING, None)


START = NOW + timedelta(hours=1)
FINISHED = NOW


@pytest.mark.parametrize("state", [S.COMPLETE, S.PARTIAL, S.UNAVAILABLE])
@pytest.mark.parametrize("family", CONSOLE_WIDE)
def test_a_later_start_request_restarts_a_finished_console_wide_scope(state, family) -> None:  # type: ignore[no-untyped-def]
    decision = DecisionView("YES", 28, start_requested_at=START, start_requested_by="ayse")
    action = act(family, ConsoleMode.ACTIVE, decision=decision,
                 scope=row(state, finished_at=FINISHED), live=True)
    assert (action.kind, action.state, action.trigger) == (
        ActionKind.TRANSITION, S.READING, T.RESTART)
    assert action.reason == "read history now: topping up"


@pytest.mark.parametrize("state", [S.COMPLETE, S.PARTIAL, S.UNAVAILABLE])
@pytest.mark.parametrize("family", PER_SOURCE)
def test_a_finished_per_source_scope_never_restarts(state, family) -> None:  # type: ignore[no-untyped-def]
    decision = DecisionView("YES", 28, start_requested_at=START, start_requested_by="ayse")
    assert act(family, ConsoleMode.ACTIVE, decision=decision,
               scope=row(state, finished_at=FINISHED), live=True).kind is ActionKind.NONE


@pytest.mark.parametrize("family", CONSOLE_WIDE)
@pytest.mark.parametrize("when", [FINISHED, FINISHED - timedelta(seconds=1), None])
def test_an_earlier_equal_or_absent_start_request_does_not_restart(family, when) -> None:  # type: ignore[no-untyped-def]
    # The tie rule: a start request restarts a scope only when strictly later than finished_at.
    decision = DecisionView(
        "YES", 28, start_requested_at=when, start_requested_by=None if when is None else "a")
    action = act(family, ConsoleMode.ACTIVE, decision=decision,
                 scope=row(S.COMPLETE, finished_at=FINISHED), live=True)
    assert action.kind is ActionKind.NONE


def test_a_finished_scope_without_finished_at_and_a_start_request_raises() -> None:
    decision = DecisionView("YES", 28, start_requested_at=START, start_requested_by="a")
    with pytest.raises(LifecycleError):
        act(F.PHASE_A, ConsoleMode.ACTIVE, decision=decision, scope=row(S.COMPLETE), live=True)


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
def test_not_eligible_stays(family, live) -> None:  # type: ignore[no-untyped-def]
    decision = DecisionView("YES", 28, start_requested_at=START, start_requested_by="a")
    assert act(family, ConsoleMode.ACTIVE, decision=decision,
               scope=row(S.NOT_ELIGIBLE, finished_at=FINISHED), live=live).kind is ActionKind.NONE


def test_inconsistent_inputs_raise() -> None:
    with pytest.raises(LifecycleError):  # console-wide live data without an anchor
        scope_action(family=F.PHASE_A, mode=ConsoleMode.ACTIVE, decision=YES, row=None,
                     has_live_data=True, live_anchor=None, switch_on=True)
    with pytest.raises(LifecycleError):  # an anchor without live data
        scope_action(family=F.CATCHALL, mode=ConsoleMode.ACTIVE, decision=YES, row=None,
                     has_live_data=False, live_anchor=ANCHOR, switch_on=True)
    with pytest.raises(LifecycleError):  # property watches have no scope row
        act(F.PROPERTY_WATCH, ConsoleMode.ACTIVE)
    with pytest.raises(LifecycleError):  # ACTIVE without a decision
        act(F.WAF, ConsoleMode.ACTIVE, decision=None)


# --------------------------------------------------------------------------- legality


def _all_actions() -> list[tuple[F, ScopeAction, S]]:
    out = []
    states = [None, S.WAITING, S.READING, S.COMPLETE, S.PARTIAL, S.UNAVAILABLE, S.NOT_ELIGIBLE]
    start = DecisionView("YES", 28, start_requested_at=START, start_requested_by="a")
    for family in SCOPE_FAMILIES:
        for state in states:
            for live in (False, True):
                for switch in (True, False):
                    for decision, mode in ((YES, ConsoleMode.ACTIVE), (start, ConsoleMode.ACTIVE),
                                           (stopped_decision(), ConsoleMode.STOPPED)):
                        scope = None if state is None else row(state, finished_at=FINISHED)
                        action = act(family, mode, decision=decision, scope=scope, live=live,
                                     switch=switch)
                        out.append((family, action, state or S.NOT_REQUESTED))
    return out


def test_every_action_trigger_is_legal_for_its_state() -> None:
    seen = set()
    for _, action, current in _all_actions():
        seen.add(action.kind)
        if action.trigger is None:
            assert action.kind in (ActionKind.NONE, ActionKind.STEP)
            continue
        change = next_state(current, action.trigger)  # raises when illegal
        assert change.state is action.state
        assert action.reason
    assert seen == set(ActionKind)


# --------------------------------------------------------------------------- the rows


def test_scope_columns_are_the_models_columns() -> None:
    assert set(SCOPE_COLUMNS) == {c.name for c in HistoryBackfillScope.__table__.columns}


@pytest.mark.parametrize("family", SCOPE_FAMILIES)
@pytest.mark.parametrize("live", [False, True])
def test_new_scope_row_has_every_column(family, live) -> None:  # type: ignore[no-untyped-def]
    action = act(family, ConsoleMode.ACTIVE, live=live)
    values = new_scope_row(action, instance_id=INSTANCE, family=family, scope_key=key(family),
                           decision=YES, at=NOW)
    assert set(values) == set(SCOPE_COLUMNS)
    assert values["state"] == action.state.value  # type: ignore[union-attr]
    assert values["days_requested"] == 28
    assert values["progress"] == {}
    assert values["reason"] == action.reason
    assert values["last_error_class"] is None and values["last_error_at"] is None
    reading = action.state is S.READING
    not_eligible = action.state is S.NOT_ELIGIBLE
    assert (values["started_at"] == NOW) is reading
    assert (values["finished_at"] == NOW) is not_eligible
    assert values["anchor"] == action.anchor
    for name in ("days_target", "target_start", "floor", "through"):
        assert values[name] is None


def test_new_scope_row_refuses_a_bad_key_or_action() -> None:
    created = act(F.WAF, ConsoleMode.ACTIVE)
    with pytest.raises(LifecycleError):
        new_scope_row(created, instance_id=INSTANCE, family=F.WAF, scope_key="",
                      decision=YES, at=NOW)
    with pytest.raises(LifecycleError):
        new_scope_row(created, instance_id=INSTANCE, family=F.PHASE_A, scope_key="3",
                      decision=YES, at=NOW)
    with pytest.raises(LifecycleError):
        new_scope_row(ScopeAction(ActionKind.NONE), instance_id=INSTANCE, family=F.WAF,
                      scope_key="1", decision=YES, at=NOW)
    with pytest.raises(LifecycleError):
        new_scope_row(created, instance_id=INSTANCE, family=F.WAF, scope_key="1",
                      decision=YES, at=datetime(2026, 1, 1))
    with pytest.raises(LifecycleError):
        new_scope_row(created, instance_id=INSTANCE, family=F.WAF, scope_key="1",
                      decision=NO, at=NOW)


def test_transition_row_finishing_keeps_the_row_and_sets_finished_at() -> None:
    searched = ScopeRowView(
        state=S.READING, days_requested=28, days_target=21, target_start=NOW, floor=NOW,
        through=NOW, anchor=ANCHOR, started_at=NOW - timedelta(hours=2), progress={"a": 1})
    action = act(F.PHASE_A, ConsoleMode.STOPPED, decision=stopped_decision(), scope=searched)
    values = transition_row(action, instance_id=INSTANCE, family=F.PHASE_A, scope_key="",
                            row=searched, decision=stopped_decision(), at=NOW)
    assert set(values) == set(SCOPE_COLUMNS)
    assert values["state"] == "PARTIAL"
    assert values["finished_at"] == NOW
    assert values["reason"] == "stopped by ayse"
    assert values["progress"] == {"a": 1}
    assert (values["days_target"], values["through"], values["anchor"]) == (21, NOW, ANCHOR)
    assert values["started_at"] == searched.started_at


def test_transition_row_anchor_found_starts_reading() -> None:
    waiting = row(S.WAITING, days_requested=28)
    action = act(F.CATCHALL, ConsoleMode.ACTIVE, scope=waiting, live=True)
    values = transition_row(action, instance_id=INSTANCE, family=F.CATCHALL, scope_key="",
                            row=waiting, decision=YES, at=NOW)
    assert set(values) == set(SCOPE_COLUMNS)
    assert (values["state"], values["anchor"], values["started_at"], values["finished_at"]) == (
        "READING", ANCHOR, NOW, None)
    assert values["progress"] == {}


def test_transition_row_clears_the_previous_step_error() -> None:
    reading = row(S.READING, days_requested=28, started_at=NOW - timedelta(hours=1))
    action = act(F.PHASE_A, ConsoleMode.STOPPED, decision=stopped_decision(), scope=reading)

    values = transition_row(
        action,
        instance_id=INSTANCE,
        family=F.PHASE_A,
        scope_key="",
        row=reading,
        decision=stopped_decision(),
        at=NOW,
    )

    assert values["state"] == "PARTIAL"
    assert values["last_error_class"] is None and values["last_error_at"] is None


@pytest.mark.parametrize("state", [S.PARTIAL, S.COMPLETE])
def test_transition_row_restart_keeps_evidence_and_clears_progress(state: S) -> None:
    target = ANCHOR - timedelta(days=28)
    floor = target - timedelta(hours=1)
    through = ANCHOR - timedelta(minutes=5)
    old = ScopeRowView(
        state=state, days_requested=28, days_target=28, target_start=target, floor=floor,
        through=through, anchor=ANCHOR, started_at=ANCHOR, finished_at=FINISHED,
        progress={"a": 1})
    decision = DecisionView("YES", 35, start_requested_at=START, start_requested_by="a")
    action = act(F.PHASE_A, ConsoleMode.ACTIVE, decision=decision, scope=old, live=True)
    values = transition_row(action, instance_id=INSTANCE, family=F.PHASE_A, scope_key="",
                            row=old, decision=decision, at=START)
    assert set(values) == set(SCOPE_COLUMNS)
    assert values["state"] == "READING"
    assert values["days_requested"] == 35
    assert values["started_at"] == START and values["finished_at"] is None
    # HB-22 D2a: the previous progress stays until the new reading's first step.
    assert values["progress"] == {"a": 1}
    assert (values["days_target"], values["target_start"], values["floor"], values["through"]) == (
        28,
        target,
        floor,
        through,
    )
    assert values["reason"] == "read history now: topping up"


def test_transition_row_refuses_other_actions() -> None:
    with pytest.raises(LifecycleError):
        transition_row(ScopeAction(ActionKind.STEP), instance_id=INSTANCE, family=F.WAF,
                       scope_key="1", row=row(S.READING), decision=YES, at=NOW)


def test_bounded_reason() -> None:
    assert bounded_reason("short") == "short"
    exact = "x" * MAX_REASON_LENGTH
    assert bounded_reason(exact) == exact
    cut = bounded_reason("x" * (MAX_REASON_LENGTH + 1))
    assert len(cut) == MAX_REASON_LENGTH and cut.endswith("…")


# --------------------------------------------------------------------------- the {} rule

SETTINGS = PlannerSettings()
T0 = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)


def searched_body() -> dict:  # type: ignore[type-arg]
    return encode_progress(new_progress(T0, NOW, SETTINGS), {"floors": {}})


def test_empty_progress_is_none_for_every_unsearched_state() -> None:
    for state in (S.WAITING, S.NOT_ELIGIBLE, S.READING, S.PARTIAL, S.UNAVAILABLE):
        assert scope_progress(row(state)) is None


def test_a_searched_scope_decodes() -> None:
    view = ScopeRowView(state=S.READING, target_start=T0, progress=searched_body())
    progress = scope_progress(view)
    assert progress is not None and progress.target_start == T0


def test_a_restarted_reading_decodes_as_not_started_until_its_first_step() -> None:
    """The stored body is the previous reading; the collector must plan a new one."""
    body = searched_body()  # progress.started_at is NOW; the restart's started_at is later
    restarted = ScopeRowView(
        state=S.READING, target_start=T0, started_at=START, progress=body
    )
    assert scope_progress(restarted) is None
    current = ScopeRowView(state=S.READING, target_start=T0, started_at=NOW, progress=body)
    assert scope_progress(current) is not None
    # A frozen collector clock restarts at the same instant the body started.
    # The restart reason is what still says this body is the previous reading.
    frozen = ScopeRowView(
        state=S.READING,
        target_start=T0,
        started_at=NOW,
        reason="read history now: topping up",
        progress=body,
    )
    assert scope_progress(frozen) is None


def test_empty_progress_with_kept_evidence_is_an_unsearched_restart() -> None:
    assert scope_progress(
        ScopeRowView(state=S.READING, target_start=T0, floor=T0, through=NOW, progress={})
    ) is None


def test_progress_without_a_target_start_is_a_contradiction() -> None:
    with pytest.raises(LifecycleError, match="no target_start"):
        scope_progress(ScopeRowView(state=S.READING, progress=searched_body()))


def test_progress_and_row_disagree_on_target_start() -> None:
    with pytest.raises(LifecycleError, match="disagree"):
        scope_progress(ScopeRowView(state=S.READING, target_start=T0 + timedelta(days=1),
                                    progress=searched_body()))


@pytest.mark.parametrize("state", [S.WAITING, S.NOT_ELIGIBLE])
def test_a_scope_that_cannot_have_searched_refuses_progress(state) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(LifecycleError):
        scope_progress(ScopeRowView(state=state, target_start=T0, progress=searched_body()))


def test_malformed_progress_raises_the_codec_error() -> None:
    with pytest.raises(ProgressCodecError):
        scope_progress(ScopeRowView(state=S.READING, target_start=T0, progress={"x": 1}))


def test_a_missing_progress_raises() -> None:
    with pytest.raises(LifecycleError):
        scope_progress(ScopeRowView(state=S.READING))


def test_the_answer_strings_are_the_decision_enums_values() -> None:
    from app.models.enums import HistoryDecisionAnswer

    assert {ANSWER_YES, ANSWER_NO} == {m.value for m in HistoryDecisionAnswer}
    assert HistoryDecisionAnswer.YES.value == ANSWER_YES
    assert HistoryDecisionAnswer.NO.value == ANSWER_NO
