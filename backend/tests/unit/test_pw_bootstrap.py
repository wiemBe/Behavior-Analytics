"""The property-watch history adapter on the shared planner (PW-13b), pure."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.history.planner import (
    ChunkResult,
    ScanOutcome,
    ScanSpanResult,
    ScopeProgress,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    interpret_scan,
    split_on_truncation,
)
from app.history.progress_codec import encode_progress
from app.history.state import (
    HistoryState,
    IllegalTransitionError,
)
from app.history.state import (
    HistoryTrigger as T,
)
from app.models.enums import PwHistoryState as Ps
from app.property_watch import bootstrap as bs
from app.property_watch.bootstrap import (
    BootstrapStateError,
    apply_trigger,
    begin_reading,
    from_history_state,
    handoff_columns,
    history_columns,
    plan_pw_step,
    pw_history_budget,
    pw_planner_settings,
    read_progress,
    restart_columns,
    scan_totals_for,
    settle_pw_step,
    start_progress,
    to_history_state,
)

SETTINGS = Settings()
PSET = pw_planner_settings(SETTINGS, cadence_seconds=300)
B = PSET.bucket
BOUNDARY = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
NOW = BOUNDARY + timedelta(seconds=45)
T0 = BOUNDARY - timedelta(days=28)
FLOOR = T0
COMPLETE, FAILED, TRUNC, NOT_RUN = (
    SearchStatus.COMPLETE,
    SearchStatus.FAILED,
    SearchStatus.TRUNCATED,
    SearchStatus.NOT_RUN,
)


def known(**kw: Any) -> ScopeProgress:
    """A progress whose floor is proved at T0 (no scan to do)."""
    base = ScopeProgress(
        target_start=T0, started_at=NOW - timedelta(hours=1), floor=FLOOR, first_event_bucket=T0
    )
    return replace(base, **kw)


def plan(progress: ScopeProgress, budget: int, pins: int = 0, **kw: Any) -> bs.PwStepPlan:
    args: dict[str, Any] = {
        "now": NOW,
        "boundary": BOUNDARY,
        "budget": StepBudget(budget, 100.0),
        "pinned_rows_per_bucket": pins,
        "enabled": True,
        "settings": PSET,
    }
    args.update(kw)
    return plan_pw_step(progress, **args)


# --------------------------------------------------------------------------- settings


def test_planner_settings_map_every_field() -> None:
    s = Settings(
        collection_interval_seconds=600,
        pw_bootstrap_floor_margin_hours=30,
        pw_max_rows_per_search=7000,
        pw_bootstrap_max_failures=4,
        pw_bootstrap_max_lower_bound_share=0.1,
        pw_bootstrap_max_hours=50,
        pw_bootstrap_searches_per_tick=9,
        pw_max_searches_per_tick=60,
        ariel_multi_bucket_max_rows=20000,
    )
    p = pw_planner_settings(s, cadence_seconds=900)
    assert p.bucket_seconds == 600
    assert p.floor_margin_hours == 30
    assert p.max_span_hours == 168
    assert p.row_cap == 7000
    assert p.row_target_share == 0.8
    assert p.initial_chunk_buckets == 288
    assert p.min_chunk_buckets == 12
    assert p.max_failures == 4
    assert p.max_lower_bound_share == 0.1
    assert p.max_hours == 50
    assert p.max_gaps == 20
    assert p.searches_per_tick == 9
    assert p.cadence_seconds == 900


def test_constants_are_the_design_numbers() -> None:
    assert (bs.INITIAL_CHUNK_BUCKETS, bs.MIN_CHUNK_BUCKETS) == (288, 12)
    assert (bs.ROW_TARGET_SHARE, bs.MAX_GAPS) == (0.8, 20)
    assert PSET.row_target_rows == 4000


# --------------------------------------------------------------------------- width


def test_dense_scope_plans_the_twelve_bucket_minimum_not_three() -> None:
    dense = known(rows_seen=1000 * 50, non_silent_buckets=50)
    out = plan(dense, 10)
    assert out.step.chunk_buckets == 12
    assert all(c.buckets(PSET) == 12 for c in out.grouped_chunks)


def test_width_stays_in_bounds_over_random_densities() -> None:
    rng = random.Random(1307)
    for _ in range(300):
        progress = known(
            rows_seen=int(rng.uniform(0.05, 3000) * 40),
            non_silent_buckets=40,
            chunk_buckets=rng.choice([None, 12, 100, 288, 2016]),
        )
        out = plan(progress, rng.randint(2, 40), pins=rng.choice([0, 1, 10]))
        if out.step.action is StepAction.READ:
            assert 12 <= out.step.chunk_buckets <= 2016  # type: ignore[operator]


# --------------------------------------------------------------------------- reserved


def test_reserved_searches_without_and_with_pins() -> None:
    no_pin = plan(known(), 10)
    assert no_pin.searches == 1 + len(no_pin.grouped_chunks)
    assert no_pin.totals_span is not None and no_pin.pinned_span is None
    pinned = plan(known(), 10, pins=1)
    assert pinned.searches == 2 + len(pinned.grouped_chunks)
    assert pinned.pinned_span == pinned.totals_span


@pytest.mark.parametrize("pins", [0, 1])
def test_budget_equal_to_reserved_waits_and_one_more_reads_one_chunk(pins: int) -> None:
    reserved = 1 + pins
    waits = plan(known(), reserved, pins=pins)
    assert waits.step.action is StepAction.WAIT
    assert waits.step.reason == "waiting for search budget"
    assert waits.searches == 0
    one = plan(known(), reserved + 1, pins=pins)
    assert one.step.action is StepAction.READ
    assert len(one.grouped_chunks) == 1
    assert one.searches == reserved + 1


@pytest.mark.parametrize("pins", [0, 1, 10])
def test_a_step_never_costs_more_than_its_budget(pins: int) -> None:
    for budget in range(1, 41):
        for progress in (known(), known(rows_seen=2000, non_silent_buckets=10)):
            assert plan(progress, budget, pins=pins).searches <= budget


def test_core_checks_run_before_the_budget_wait() -> None:
    off = plan(known(), 0, enabled=False)
    assert off.step.action is StepAction.FINISH and off.step.trigger is T.SWITCHED_OFF
    late = plan(known(started_at=NOW - timedelta(hours=100)), 0)
    assert late.step.trigger is T.TIME_LIMIT


def test_scan_needs_one_search_and_does_not_reserve() -> None:
    fresh = ScopeProgress(target_start=T0, started_at=NOW, scan_cursor=T0 - PSET.margin)
    out = plan(fresh, 1, pins=10)
    assert out.step.action is StepAction.SCAN
    assert out.totals_span == out.step.scan_span and out.searches == 1
    assert plan(fresh, 0).step.action is StepAction.WAIT


# --------------------------------------------------------------------------- pinned cap


def test_ten_pins_cap_the_span_at_400_buckets_and_the_chunks_tile_it() -> None:
    out = plan(known(chunk_buckets=288), 12, pins=10)
    span = out.step.span
    assert span is not None and span.buckets(PSET) == 400
    assert span.start == FLOOR
    assert [c.buckets(PSET) for c in out.grouped_chunks] == [288, 112]
    cursor = span.start
    for chunk in out.grouped_chunks:
        assert chunk.start == cursor
        cursor = chunk.end
    assert cursor == span.end
    assert out.totals_span == span == out.pinned_span


def test_one_pin_is_capped_only_by_the_week() -> None:
    out = plan(known(chunk_buckets=2016), 12, pins=1)
    span = out.step.span
    assert span is not None and span.buckets(PSET) == 2016


def test_pinned_cap_follows_the_cap_setting() -> None:
    small = replace(PSET, row_cap=1000)  # target 800 rows, ten pins: 80 buckets
    out = plan(known(), 12, pins=10, settings=small)
    assert out.step.span is not None and out.step.span.buckets(small) == 80


# --------------------------------------------------------------------------- scan reuse


def found_floor() -> tuple[ScopeProgress, ScanSpanResult]:
    start = ScopeProgress(target_start=T0, started_at=NOW, scan_cursor=T0 - PSET.margin)
    span = Span(T0 - PSET.margin, T0 - PSET.margin + 168 * 12 * B)
    totals = tuple((span.start + i * B, 3) for i in range(0, 2016, 7))
    result = ScanSpanResult(span, COMPLETE, totals)
    decision = interpret_scan(start, [result], BOUNDARY, PSET)
    assert decision.outcome is ScanOutcome.FLOOR_FOUND
    return decision.progress, result


def test_the_first_read_after_the_floor_reuses_the_scan_totals() -> None:
    progress, scan = found_floor()
    out = plan(progress, 12, pins=1, scan=scan)
    assert out.reuses_scan_totals and out.totals_span is None
    assert out.pinned_span is not None
    assert out.searches == 1 + len(out.grouped_chunks)
    span = out.step.span
    assert span is not None and span.end <= scan.span.end
    pairs = scan_totals_for(scan, span)
    assert pairs == tuple(p for p in scan.totals if span.start <= p[0] < span.end)
    assert pairs and all(span.start <= b < span.end for b, _ in pairs)
    # one search fewer reserved: the span grows against the same budget
    plain = plan(progress, 12, pins=1)
    assert plain.searches == 2 + len(plain.grouped_chunks)


def test_the_span_is_capped_at_the_scan_end() -> None:
    progress, scan = found_floor()
    out = plan(progress, 40, scan=scan)
    span = out.step.span
    assert span is not None and span.end == scan.span.end
    assert out.reuses_scan_totals


@pytest.mark.parametrize("status", [FAILED, TRUNC, NOT_RUN])
def test_a_scan_that_was_not_whole_is_not_reused(status: SearchStatus) -> None:
    progress, scan = found_floor()
    bad = replace(scan, status=status)
    out = plan(progress, 12, scan=bad)
    assert not out.reuses_scan_totals and out.totals_span is not None
    with pytest.raises(BootstrapStateError):
        scan_totals_for(bad, out.totals_span)


def test_a_scan_that_does_not_cover_the_start_is_not_reused() -> None:
    progress, scan = found_floor()
    elsewhere = replace(scan, span=Span(scan.span.end, scan.span.end + 100 * B), totals=())
    out = plan(progress, 12, scan=elsewhere)
    assert not out.reuses_scan_totals and out.totals_span is not None
    with pytest.raises(BootstrapStateError):
        scan_totals_for(scan, Span(scan.span.start, scan.span.end + B))


# --------------------------------------------------------------------------- kill switch


def test_switch_off_finishes_a_reading_watch_partial() -> None:
    out = plan(known(), 12, enabled=False)
    assert out.step.action is StepAction.FINISH
    assert out.step.trigger is T.SWITCHED_OFF
    assert out.step.reason == "history reading was switched off"
    assert apply_trigger(Ps.READING, T.SWITCHED_OFF)[0] is Ps.PARTIAL
    assert out.searches == 0


def test_begin_with_the_switch_off_is_partial_with_nothing_written() -> None:
    begun = begin_reading(
        history_days=7, boundary=BOUNDARY, now=NOW, enabled=False, settings=PSET
    )
    assert begun.state is Ps.PARTIAL and begun.progress is None
    assert begun.reason == "history reading is switched off; the scope starts live"
    c = begun.columns
    assert c["collected_through"] is None and c["evaluated_through"] is None
    assert c["history_end"] is None and c["history_finished_at"] == NOW
    assert c["history_progress"] == {} and c["history_target_start"] is None


def test_begin_with_the_switch_on_reads_from_t0() -> None:
    begun = begin_reading(
        history_days=7, boundary=BOUNDARY, now=NOW, enabled=True, settings=PSET
    )
    assert begun.state is Ps.READING and begun.trigger is T.REQUEST_READY
    assert begun.progress is not None
    assert begun.progress.target_start == BOUNDARY - timedelta(days=7)
    assert begun.progress.scan_cursor == begun.progress.target_start - PSET.margin
    assert begun.columns["history_state"] is Ps.READING
    assert "history_finished_at" not in begun.columns


def test_begin_refuses_a_misaligned_boundary_and_no_days() -> None:
    from app.history.planner import PlannerInputError

    with pytest.raises(PlannerInputError):
        begin_reading(
            history_days=7, boundary=BOUNDARY + timedelta(seconds=1), now=NOW,
            enabled=True, settings=PSET,
        )
    with pytest.raises(BootstrapStateError):
        start_progress(history_days=0, boundary=BOUNDARY, now=NOW, settings=PSET)


# --------------------------------------------------------------------------- settling


def chunks(progress: ScopeProgress, n: int, width: int = 288) -> list[ChunkResult]:
    start = progress.floor if progress.through is None else progress.through + B
    assert start is not None
    return [
        ChunkResult(Span(start + i * width * B, start + (i + 1) * width * B), COMPLETE, rows=10,
                    non_silent_buckets=width)
        for i in range(n)
    ]


def settle(progress: ScopeProgress, totals: SearchStatus, pinned: SearchStatus | None,
           results: list[ChunkResult]) -> Any:
    return settle_pw_step(
        progress, totals=totals, pinned=pinned, grouped=results, end=BOUNDARY, settings=PSET
    )


@pytest.mark.parametrize(("totals", "pinned"), [(FAILED, COMPLETE), (COMPLETE, FAILED),
                                                (TRUNC, COMPLETE), (COMPLETE, TRUNC)])
def test_a_wide_search_that_is_not_whole_writes_nothing_and_halves(
    totals: SearchStatus, pinned: SearchStatus
) -> None:
    p = known(chunk_buckets=288)
    out = settle(p, totals, pinned, chunks(p, 2))
    assert out.write_spans == () and not out.advanced
    assert out.progress.through is None
    assert out.progress.chunk_buckets == 144
    assert out.progress.buckets_read == 0


def test_totals_not_run_writes_nothing_and_keeps_the_width() -> None:
    p = known(chunk_buckets=288)
    out = settle(p, NOT_RUN, COMPLETE, chunks(p, 2))
    assert out.write_spans == () and out.progress.through is None
    assert out.progress.chunk_buckets == 288 and out.progress.failures == 0


def test_a_failure_outranks_a_search_that_was_not_run() -> None:
    p = known(chunk_buckets=288)
    out = settle(p, NOT_RUN, FAILED, chunks(p, 1))
    assert out.progress.chunk_buckets == 144


def test_three_failures_at_the_minimum_width_make_one_gap() -> None:
    p = known(chunk_buckets=12, chunk_ceiling=12)
    for expected in (1, 2):
        out = settle(p, FAILED, None, chunks(p, 1, width=12))
        assert out.progress.failures == expected and out.new_gap is None
        assert out.progress.through is None
        p = out.progress
    out = settle(p, COMPLETE, FAILED, chunks(p, 1, width=12))
    assert out.new_gap is not None and out.new_gap.start == FLOOR
    assert out.progress.through == FLOOR + 11 * B
    assert out.progress.failures == 0 and out.write_spans == ()


def test_all_whole_passes_the_core_result_unchanged() -> None:
    p = known(chunk_buckets=288)
    results = chunks(p, 2)
    from app.history.planner import settle_step

    core = settle_step(p, results, end=BOUNDARY, settings=PSET)
    mine = settle_pw_step(
        p, totals=COMPLETE, pinned=None, grouped=results, totals_reused=True,
        end=BOUNDARY, settings=PSET,
    )
    assert mine == core
    assert len(mine.write_spans) == 2 and mine.advanced


def test_settle_counts_the_span_wide_searches_that_really_ran() -> None:
    p = known(chunk_buckets=288)
    r = chunks(p, 2)  # two grouped searches
    both = settle(p, COMPLETE, COMPLETE, r).progress.searches
    assert both == 2 + 1 + 1
    assert settle(p, COMPLETE, None, r).progress.searches == 3  # no pins: no PINNED
    reused = settle_pw_step(p, totals=COMPLETE, pinned=COMPLETE, grouped=r, totals_reused=True,
                            end=BOUNDARY, settings=PSET)
    assert reused.progress.searches == 3  # a reused scan TOTALS is not a new search
    assert settle(p, NOT_RUN, COMPLETE, r).progress.searches == 1  # only PINNED ran
    assert settle(p, COMPLETE, NOT_RUN, r).progress.searches == 1
    # a failed TOTALS still ran; the downgrade keeps each GROUPED count (first chunk blocks
    # but both ran)
    spent = [replace(x, searches=3) for x in r]
    assert settle(p, FAILED, COMPLETE, spent).progress.searches == 6 + 2
    # a chunk that was not run adds nothing, even downgraded to FAILED
    skipped = [r[0], ChunkResult(r[1].span, NOT_RUN)]
    assert settle(p, FAILED, None, skipped).progress.searches == 1 + 1


# --------------------------------------------------------------------------- states


def test_every_pw_state_maps_both_ways() -> None:
    for state in Ps:
        assert from_history_state(to_history_state(state)) is state


@pytest.mark.parametrize("state", [HistoryState.WAITING, HistoryState.NOT_ELIGIBLE])
def test_waiting_and_not_eligible_never_apply(state: HistoryState) -> None:
    with pytest.raises(BootstrapStateError):
        from_history_state(state)


def test_the_triggers_pw_uses_are_legal_where_used() -> None:
    expected = {
        (Ps.NOT_REQUESTED, T.REQUEST_READY): Ps.READING,
        (Ps.NOT_REQUESTED, T.SWITCHED_OFF): Ps.PARTIAL,
        (Ps.READING, T.NOTHING_TO_READ): Ps.UNAVAILABLE,
        (Ps.READING, T.FINISHED_COMPLETE): Ps.COMPLETE,
        (Ps.READING, T.FINISHED_PARTIAL): Ps.PARTIAL,
        (Ps.READING, T.DENSITY_GATE): Ps.PARTIAL,
        (Ps.READING, T.TOO_MANY_GAPS): Ps.PARTIAL,
        (Ps.READING, T.TIME_LIMIT): Ps.PARTIAL,
        (Ps.READING, T.SWITCHED_OFF): Ps.PARTIAL,
        (Ps.READING, T.STOPPED_BY_OPERATOR): Ps.PARTIAL,
        **{
            (s, T.RESTART): Ps.READING
            for s in (Ps.READING, Ps.COMPLETE, Ps.PARTIAL, Ps.UNAVAILABLE)
        },
    }
    for (state, trigger), target in expected.items():
        new, reason = apply_trigger(state, trigger)
        assert new is target and reason


def test_illegal_and_unmappable_triggers_raise() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_trigger(Ps.COMPLETE, T.TIME_LIMIT)
    with pytest.raises(IllegalTransitionError):
        apply_trigger(Ps.NOT_REQUESTED, T.FINISHED_COMPLETE)
    with pytest.raises(BootstrapStateError):
        apply_trigger(Ps.NOT_REQUESTED, T.REQUEST_AWAITING_ANCHOR)  # would be WAITING
    with pytest.raises(BootstrapStateError):
        apply_trigger(Ps.NOT_REQUESTED, T.NOT_ELIGIBLE)


# --------------------------------------------------------------------------- progress


def full_progress() -> ScopeProgress:
    from app.history.planner import Gap

    return ScopeProgress(
        target_start=T0, started_at=NOW, floor=T0 + 12 * B, first_event_bucket=T0 - 100 * B,
        through=T0 + 500 * B, scan_cursor=T0 + 2000 * B, scan_failures=1, chunk_buckets=24,
        chunk_ceiling=48, failures=2, searches=17, buckets_read=480, lower_bound_buckets=3,
        rows_seen=9000, non_silent_buckets=400, density_checked=True,
        gaps=(Gap(T0 + 100 * B, T0 + 112 * B, "search failed 3 times at the minimum width"),),
    )


def watch_of(columns: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(**{k: v for k, v in columns.items() if k.startswith("history_")})


def test_columns_round_trip_every_field_and_the_reason() -> None:
    p = full_progress()
    cols = history_columns(p, state=Ps.READING, reason="reading history")
    assert cols["history_state"] is Ps.READING
    assert cols["history_floor"] == p.floor and cols["history_through"] == p.through
    got = read_progress(watch_of(cols))
    assert got == (p, "reading history")


def test_not_started_reads_as_none() -> None:
    cols = history_columns(None, state=Ps.NOT_REQUESTED, reason="")
    assert cols["history_progress"] == {}
    assert read_progress(watch_of(cols)) is None


def test_an_empty_progress_with_a_column_set_is_an_error() -> None:
    w = watch_of(history_columns(None, state=Ps.READING, reason=""))
    w.history_target_start = T0
    with pytest.raises(BootstrapStateError, match="history_target_start"):
        read_progress(w)
    w.history_target_start = None
    w.history_through = T0
    with pytest.raises(BootstrapStateError, match="history_through"):
        read_progress(w)


@pytest.mark.parametrize(
    "column",
    ["history_target_start", "history_floor", "history_through", "history_started_at"],
)
def test_a_column_that_differs_from_the_progress_is_named(column: str) -> None:
    w = watch_of(history_columns(full_progress(), state=Ps.READING, reason="r"))
    setattr(w, column, getattr(w, column) + B)
    with pytest.raises(BootstrapStateError, match=column):
        read_progress(w)
    setattr(w, column, None)
    with pytest.raises(BootstrapStateError, match=column):
        read_progress(w)


def test_a_bad_codec_payload_is_an_error() -> None:
    cols = history_columns(full_progress(), state=Ps.READING, reason="r")
    w = watch_of(cols)
    w.history_progress = {**cols["history_progress"], "version": 3}
    with pytest.raises(BootstrapStateError):
        read_progress(w)
    w.history_progress = encode_progress(full_progress(), adapter={})
    with pytest.raises(BootstrapStateError, match="reason"):
        read_progress(w)
    w.history_progress = "nonsense"
    with pytest.raises(BootstrapStateError):
        read_progress(w)


# --------------------------------------------------------------------------- hand-off


def settled() -> ScopeProgress:
    return known(through=FLOOR + 100 * B)


@pytest.mark.parametrize("state", [Ps.COMPLETE, Ps.PARTIAL])
def test_handoff_sets_both_watermarks_to_the_last_bucket_before_the_boundary(
    state: Ps,
) -> None:
    c = handoff_columns(
        settled(), state=state, reason="r", boundary=BOUNDARY, now=NOW, settings=PSET
    )
    assert c["collected_through"] == c["evaluated_through"] == BOUNDARY - B
    assert c["history_end"] == BOUNDARY and c["history_finished_at"] == NOW
    assert c["history_state"] is state
    assert c["history_through"] == FLOOR + 100 * B  # the implicit gap is not the through


def test_handoff_with_nothing_settled_leaves_the_watermarks_null() -> None:
    for progress, state in ((known(), Ps.PARTIAL), (None, Ps.PARTIAL), (known(), Ps.UNAVAILABLE),
                            (settled(), Ps.UNAVAILABLE)):
        c = handoff_columns(
            progress, state=state, reason="r", boundary=BOUNDARY, now=NOW, settings=PSET
        )
        assert c["collected_through"] is None and c["evaluated_through"] is None
        assert c["history_end"] is None
        assert c["history_finished_at"] == NOW


def test_handoff_refuses_a_state_that_is_not_finished() -> None:
    for state in (Ps.READING, Ps.NOT_REQUESTED):
        with pytest.raises(BootstrapStateError):
            handoff_columns(
                settled(), state=state, reason="r", boundary=BOUNDARY, now=NOW, settings=PSET
            )


# --------------------------------------------------------------------------- restart


@pytest.mark.parametrize("state", [Ps.READING, Ps.COMPLETE, Ps.PARTIAL, Ps.UNAVAILABLE])
def test_restart_clears_the_history_columns_and_the_watermarks(state: Ps) -> None:
    c = restart_columns(state, history_days=7)
    assert c["history_state"] is Ps.READING
    for name in ("history_target_start", "history_floor", "history_through", "history_end",
                 "history_started_at", "history_finished_at", "collected_through",
                 "evaluated_through"):
        assert c[name] is None, name
    assert c["history_progress"] == {}
    assert read_progress(watch_of(c)) is None


@pytest.mark.parametrize("state", list(Ps))
@pytest.mark.parametrize("days", [None, 0])
def test_restart_without_history_days_changes_nothing(state: Ps, days: int | None) -> None:
    assert restart_columns(state, history_days=days) == {}


def test_restart_leaves_a_watch_that_never_started() -> None:
    assert restart_columns(Ps.NOT_REQUESTED, history_days=7) == {}


# --------------------------------------------------------------------------- budget


def test_history_budget_is_what_live_left_over() -> None:
    s = Settings(pw_bootstrap_searches_per_tick=12, pw_bootstrap_seconds_per_tick=180)
    assert pw_history_budget(settings=s, live_remaining=50, seconds_since_tick_start=30) == (
        StepBudget(12, 150)
    )
    def searches(live: int, since: float) -> StepBudget:
        return pw_history_budget(settings=s, live_remaining=live, seconds_since_tick_start=since)

    assert searches(5, 0).searches == 5
    assert searches(-3, 0).searches == 0
    assert searches(9, 200).seconds_left < 0


def test_the_smallest_step_of_a_reading_costs_two_or_three_searches() -> None:
    """TOTALS and one GROUPED, and PINNED with it (design §H.4.4)."""
    assert bs.history_step_searches(False) == 2
    assert bs.history_step_searches(True) == 3
    assert bs.history_step_parts(2) == "TOTALS and one GROUPED"
    assert bs.history_step_parts(3) == "TOTALS, PINNED and one GROUPED"


def test_the_sentence_that_names_the_setting_says_what_a_step_needs() -> None:
    assert bs.history_budget_sentence(3, setting=2) == (
        "PW_BOOTSTRAP_SEARCHES_PER_TICK is 2; reading history for this watch needs at least 3 "
        "searches per tick (TOTALS, PINNED and one GROUPED)"
    )
    assert bs.history_budget_sentence(2, setting=1) == (
        "PW_BOOTSTRAP_SEARCHES_PER_TICK is 1; reading history for this watch needs at least 2 "
        "searches per tick (TOTALS and one GROUPED)"
    )


# --------------------------------------------------------------------------- whole reading


class Sim:
    """One watch read tick by tick against a scripted Ariel (no I/O)."""

    def __init__(self, *, days: int, rho: int, pins: int = 1, per_tick: int = 12,
                 b0_offset_buckets: int = -3 * 288, fail_rate: float = 0.0, seed: int = 0,
                 empty: bool = False) -> None:
        self.boundary = BOUNDARY
        self.days, self.rho, self.pins = days, rho, pins
        self.per_tick = per_tick
        self.fail_rate = fail_rate
        self.rng = random.Random(seed)
        self.empty = empty
        self.t0 = self.boundary - timedelta(days=days)
        self.b0 = self.t0 + b0_offset_buckets * B
        self.searches = 0
        self.kinds: Counter[str] = Counter()
        self.ticks = 0
        self.state = Ps.NOT_REQUESTED
        self.progress: ScopeProgress | None = None
        self.covered_to: datetime | None = None
        self.written: list[Span] = []
        self.trigger: T | None = None
        self.columns: dict[str, Any] = {}
        self.max_tick_searches = 0

    def total(self, bucket: datetime) -> int:
        return 0 if self.empty or bucket < self.b0 else 5 * self.rho

    def scan_search(self, span: Span) -> ScanSpanResult:
        totals = tuple((b, self.total(b)) for b in _buckets(span) if self.total(b) > 0)
        return ScanSpanResult(span, self.status(), totals)

    def status(self) -> SearchStatus:
        return FAILED if self.rng.random() < self.fail_rate else COMPLETE

    def grouped(self, chunk: Span, left: list[int], whole: bool) -> list[ChunkResult]:
        """One chunk, split in time on truncation while the tick has budget (run_search)."""
        out: list[ChunkResult] = []
        queue = [chunk]
        spent = 0
        while queue:
            piece = queue.pop(0)
            if left[0] <= 0:
                out.append(ChunkResult(piece, NOT_RUN))
                continue
            left[0] -= 1
            self.searches += 1
            spent += 1
            self.kinds["grouped"] += 1
            n = piece.buckets(PSET)
            if self.fail_rate and self.rng.random() < self.fail_rate:
                out.append(ChunkResult(piece, FAILED))
            elif self.rho * n > PSET.row_cap:
                halves = split_on_truncation(piece, PSET)
                if halves is None:
                    out.append(ChunkResult(piece, TRUNC, rows=PSET.row_cap, non_silent_buckets=1,
                                           lower_bound_buckets=1))
                else:
                    queue[0:0] = list(halves)
            else:
                out.append(ChunkResult(piece, COMPLETE, rows=self.rho * n, non_silent_buckets=n))
        # a split parent is a search no piece carries: hand it to the first piece that ran
        ran = [i for i, r in enumerate(out) if r.status is not NOT_RUN]
        if not ran and spent:
            # the budget ended right after a truncation: the chunk stays truncated, unsplit
            return [ChunkResult(chunk, TRUNC, searches=spent)]
        if ran:
            first = out[ran[0]]
            extra = spent - sum(r.searches for r in out if r.status is not NOT_RUN)
            out[ran[0]] = replace(first, searches=first.searches + extra)
        return out

    def run(self, max_ticks: int = 3000) -> Sim:
        now = NOW
        while self.ticks < max_ticks and self.trigger is None:
            self.tick(now)
            if self.trigger is None:
                now += B
                self.boundary += B
        assert self.trigger is not None, "the reading did not finish"
        assert self.progress is not None
        # the stored count is what the simulation really spent
        assert self.progress.searches == self.searches
        return self

    def finish(self, trigger: T) -> None:
        self.trigger = trigger
        self.state, reason = apply_trigger(self.state, trigger)
        self.columns = handoff_columns(
            self.progress, state=self.state, reason=reason, boundary=self.boundary,
            now=self._now, settings=PSET,
        )

    def tick(self, now: datetime) -> None:
        self._now = now
        if self.progress is None:
            begun = begin_reading(history_days=self.days, boundary=self.boundary, now=now,
                                  enabled=True, settings=PSET)
            self.state, self.progress = begun.state, begun.progress
        assert self.progress is not None
        budget = pw_history_budget(
            settings=Settings(pw_bootstrap_searches_per_tick=self.per_tick),
            live_remaining=10_000, seconds_since_tick_start=0,
        )
        left = [budget.searches]
        spent_before = self.searches
        scan: ScanSpanResult | None = None
        self.ticks += 1
        while True:
            step = plan_pw_step(
                self.progress, now=now, boundary=self.boundary,
                budget=StepBudget(left[0], 100.0), pinned_rows_per_bucket=self.pins,
                enabled=True, settings=PSET, scan=scan,
            )
            assert step.searches <= left[0]
            if step.step.action is StepAction.FINISH:
                assert step.step.trigger is not None
                self.finish(step.step.trigger)
                break
            if step.step.action is StepAction.WAIT:
                break
            if step.step.action is StepAction.SCAN:
                assert step.totals_span is not None
                left[0] -= 1
                self.searches += 1
                self.kinds["scan"] += 1
                result = self.scan_search(step.totals_span)
                decision = interpret_scan(self.progress, [result], self.boundary, PSET)
                self.progress = decision.progress
                if decision.outcome is ScanOutcome.FLOOR_FOUND:
                    scan = result
                    self.covered_to = decision.progress.floor
                elif decision.outcome is ScanOutcome.UNAVAILABLE:
                    assert decision.trigger is not None
                    self.finish(decision.trigger)
                    break
                continue
            self.read(step, left, scan)
            break
        spent = self.searches - spent_before
        assert spent <= budget.searches
        self.max_tick_searches = max(self.max_tick_searches, spent)

    def read(self, step: bs.PwStepPlan, left: list[int], scan: ScanSpanResult | None) -> None:
        assert self.progress is not None and self.covered_to is not None
        span = step.step.span
        assert span is not None and span.start == self.covered_to
        totals = COMPLETE
        if step.reuses_scan_totals:
            assert scan is not None
            assert scan_totals_for(scan, span) is not None
        else:
            left[0] -= 1
            self.searches += 1
            self.kinds["totals"] += 1
            totals = self.status()
        pinned = None
        if step.pinned_span is not None:
            left[0] -= 1
            self.searches += 1
            self.kinds["pinned"] += 1
            pinned = self.status()
        results: list[ChunkResult] = []
        left_g = left
        for chunk in step.grouped_chunks:
            results.extend(self.grouped(chunk, left_g, True))
        before = self.progress
        out = settle_pw_step(before, totals=totals, pinned=pinned, grouped=results,
                             totals_reused=step.reuses_scan_totals,
                             end=self.boundary, settings=PSET)
        # a failed or truncated search never advances through
        if totals is not COMPLETE or pinned not in (None, COMPLETE):
            assert out.write_spans == ()
            # only a recorded gap (three failures at the minimum width) moves through
            assert out.progress.through == before.through or out.new_gap is not None
        for written in out.write_spans:
            assert written.start >= (before.floor or written.start)
            assert written.start == self.covered_to
            self.covered_to = written.end
            self.written.append(written)
        if out.new_gap is not None:
            assert out.new_gap.start == self.covered_to
            self.covered_to = out.new_gap.end
        if out.progress.through is None:
            assert self.covered_to == before.floor
        else:
            assert out.progress.through == self.covered_to - B
        self.progress = out.progress
        if out.trigger is not None:
            self.finish(out.trigger)


def _buckets(span: Span) -> list[datetime]:
    out, cur = [], span.start
    while cur < span.end:
        out.append(cur)
        cur += B
    return out


def test_simulated_reading_tiles_the_window_and_hands_off_at_the_boundary() -> None:
    sim = Sim(days=1, rho=6).run()
    assert sim.state is Ps.COMPLETE
    assert sim.written[0].start == sim.progress.floor  # type: ignore[union-attr]
    assert sim.covered_to == sim.boundary
    assert sim.columns["collected_through"] == sim.boundary - B
    assert sim.columns["history_end"] == sim.boundary
    assert sim.columns["collected_through"] == sim.columns["evaluated_through"]


def test_floor_cut_by_retention_hands_off_partial() -> None:
    sim = Sim(days=2, rho=6, b0_offset_buckets=188).run()
    assert sim.state is Ps.PARTIAL
    assert sim.progress is not None and sim.progress.floor is not None
    assert sim.progress.floor > sim.t0 + B
    assert sim.written[0].start == sim.progress.floor


def test_a_scope_with_no_event_is_unavailable_and_writes_nothing() -> None:
    sim = Sim(days=2, rho=6, empty=True).run()
    assert sim.state is Ps.UNAVAILABLE and sim.written == []
    assert sim.columns["collected_through"] is None and sim.columns["history_end"] is None


def test_a_late_first_event_scans_several_spans() -> None:
    sim = Sim(days=28, rho=6, b0_offset_buckets=12 * 288).run()
    assert sim.state is Ps.PARTIAL
    assert sim.covered_to == sim.boundary


@pytest.mark.parametrize("seed", range(6))
def test_failures_never_break_the_tiling(seed: int) -> None:
    sim = Sim(days=3, rho=40, fail_rate=0.25, seed=seed).run()
    assert sim.state in (Ps.COMPLETE, Ps.PARTIAL)
    # written spans plus recorded gaps tile [F, boundary) (checked at every write in Sim)
    assert sim.covered_to == sim.boundary or sim.state is Ps.PARTIAL
    if sim.progress is not None and sim.progress.gaps:
        assert sim.state is Ps.PARTIAL


COST_ROWS = [
    ("6 values", 1, 6, 3, 1),
    ("6 values", 7, 6, 7, 1),
    ("6 values", 28, 6, 22, 2),
    ("200 values", 1, 200, 17, 2),
    ("200 values", 7, 200, 104, 9),
    ("200 values", 28, 200, 413, 35),
]


@pytest.mark.parametrize(("name", "days", "rho", "table_searches", "table_ticks"), COST_ROWS)
def test_the_cost_table_rows_simulated(
    name: str, days: int, rho: int, table_searches: int, table_ticks: int
) -> None:
    sim = Sim(days=days, rho=rho, pins=1, per_tick=12).run()
    assert sim.state is Ps.COMPLETE
    assert sim.max_tick_searches <= 12
    print(f"COST {name} {days}d table={table_searches}/{table_ticks} "
          f"sim={sim.searches}/{sim.ticks} {dict(sim.kinds)}")
    assert sim.searches > 0 and sim.ticks > 0
