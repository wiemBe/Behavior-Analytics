"""The history planner: floor scan, steps, failures, the density gate, window fitting."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.history.planner import (
    ChunkResult,
    PlannerInputError,
    PlannerSettings,
    ScanOutcome,
    ScanSpanResult,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    fit_window,
    floor_is_cut,
    interpret_scan,
    new_progress,
    next_start,
    plan_scan,
    plan_step,
    row_fit_buckets,
    searches_for,
    settle_step,
    split_on_truncation,
    ticks_for,
    window_buckets,
)
from app.history.state import HistoryTrigger as T

SET = PlannerSettings()
B = SET.bucket
END = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
T0 = END - timedelta(days=28)
STARTED = END - timedelta(hours=1)
BUDGET = StepBudget(searches=10, seconds_left=120)


def prog(**kw):
    base = new_progress(T0, STARTED, SET)
    from dataclasses import replace

    return replace(base, **kw)


def ok(start: datetime, n: int, *, rows: int = 0, ns: int | None = None, lb: int = 0):
    return ChunkResult(
        Span(start, start + n * B), SearchStatus.COMPLETE, rows, n if ns is None else ns, lb
    )


def bad(start: datetime, n: int, status: SearchStatus):
    return ChunkResult(Span(start, start + n * B), status)


def scan_ok(span: Span, event_at: datetime | None = None) -> ScanSpanResult:
    totals = ((event_at, 7),) if event_at is not None else ()
    return ScanSpanResult(span, SearchStatus.COMPLETE, totals)


# ----------------------------------------------------------------------------- settings


def test_settings_reject_nonsense() -> None:
    for kw in (
        {"bucket_seconds": 7},
        {"max_span_hours": 169},
        {"row_target_share": 0},
        {"floor_margin_hours": 0},
        {"max_failures": 0},
        {"min_chunk_buckets": 0},
        {"max_hours": 0},
    ):
        with pytest.raises(PlannerInputError):
            PlannerSettings(**kw)


def test_settings_derived_numbers() -> None:
    assert SET.max_span_buckets == 2016
    assert SET.row_target_rows == 4000
    assert SET.margin == timedelta(hours=24)
    assert window_buckets(28, SET) == 8352
    assert window_buckets(30, PlannerSettings(bucket_seconds=3600, row_cap=2000)) == 744


def test_naive_and_unaligned_times_are_refused() -> None:
    with pytest.raises(PlannerInputError):
        new_progress(datetime(2026, 1, 1), STARTED, SET)
    with pytest.raises(PlannerInputError):
        new_progress(T0 + timedelta(seconds=60), STARTED, SET)


# ----------------------------------------------------------------------------- floor scan


def test_first_scan_starts_one_margin_before_t0_and_is_at_most_168h() -> None:
    plan = plan_scan(prog(), END, SET)
    first = T0 - timedelta(hours=24)
    assert plan.span == Span(first, first + timedelta(hours=168))


def test_floor_is_b0_plus_margin_when_the_scope_is_young() -> None:
    p = prog()
    plan = plan_scan(p, END, SET)
    b0 = T0 + timedelta(days=3, minutes=5)
    d = interpret_scan(p, [scan_ok(plan.span, b0)], END, SET)
    assert d.outcome is ScanOutcome.FLOOR_FOUND
    assert d.progress.floor == b0 + timedelta(hours=24)
    assert d.progress.first_event_bucket == b0
    assert "later than the asked start" in d.reason
    assert floor_is_cut(d.progress, SET)


def test_floor_is_t0_when_events_precede_t0_by_the_margin() -> None:
    p = prog()
    plan = plan_scan(p, END, SET)
    d = interpret_scan(p, [scan_ok(plan.span, T0 - timedelta(hours=24))], END, SET)
    assert d.progress.floor == T0
    assert not floor_is_cut(d.progress, SET)
    # an event inside the margin proves less than T0: the floor moves later
    d2 = interpret_scan(p, [scan_ok(plan.span, T0 - timedelta(hours=23, minutes=55))], END, SET)
    assert d2.progress.floor == T0 + B


def test_floor_uses_the_first_event_not_a_later_one() -> None:
    p = prog()
    span = plan_scan(p, END, SET).span
    totals = (
        (T0 + timedelta(days=2), 5),
        (T0 + timedelta(days=1), 9),
        (T0 + timedelta(days=1, minutes=5), 0),
    )
    d = interpret_scan(p, [ScanSpanResult(span, SearchStatus.COMPLETE, totals)], END, SET)
    assert d.progress.first_event_bucket == T0 + timedelta(days=1)


def test_empty_spans_continue_then_end_unavailable() -> None:
    p = prog()
    end = T0 + timedelta(days=10)
    decisions = []
    while True:
        plan = plan_scan(p, end, SET)
        if plan.span is None:
            break
        d = interpret_scan(p, [scan_ok(plan.span)], end, SET)
        decisions.append(d)
        p = d.progress
        if d.outcome is ScanOutcome.UNAVAILABLE:
            break
    assert [d.outcome for d in decisions][:-1] == [ScanOutcome.CONTINUE] * (len(decisions) - 1)
    assert decisions[-1].outcome is ScanOutcome.UNAVAILABLE
    assert decisions[-1].trigger is T.NOTHING_TO_READ
    assert "no event of the scope" in decisions[-1].reason
    assert p.floor is None


def test_a_failed_or_truncated_scan_proves_nothing_and_is_retried() -> None:
    p = prog()
    span = plan_scan(p, END, SET).span
    for status in (SearchStatus.FAILED, SearchStatus.TRUNCATED, SearchStatus.NOT_RUN):
        d = interpret_scan(p, [ScanSpanResult(span, status, ((T0, 5),))], END, SET)
        assert d.outcome is ScanOutcome.RETRY
        assert d.progress.floor is None  # events in a failed span are not evidence
        assert d.progress.scan_cursor == p.scan_cursor
        assert d.progress.scan_failures == 1


def test_a_failed_span_stops_the_scan_even_when_a_later_span_has_events() -> None:
    p = prog()
    s1 = plan_scan(p, END, SET).span
    s2 = Span(s1.end, s1.end + timedelta(hours=168))
    d = interpret_scan(
        p, [ScanSpanResult(s1, SearchStatus.FAILED), scan_ok(s2, s2.start)], END, SET
    )
    assert d.outcome is ScanOutcome.RETRY
    assert d.progress.floor is None


def test_repeated_scan_failure_is_unavailable_never_a_floor() -> None:
    p = prog()
    span = plan_scan(p, END, SET).span
    for _ in range(SET.max_failures - 1):
        d = interpret_scan(p, [ScanSpanResult(span, SearchStatus.FAILED)], END, SET)
        assert d.outcome is ScanOutcome.RETRY
        p = d.progress
    d = interpret_scan(p, [ScanSpanResult(span, SearchStatus.FAILED)], END, SET)
    assert d.outcome is ScanOutcome.UNAVAILABLE
    assert d.trigger is T.NOTHING_TO_READ
    assert d.progress.floor is None


def test_event_whose_margin_reaches_the_end_is_unavailable() -> None:
    p = prog()
    end = T0 + timedelta(days=2)
    span = plan_scan(p, end, SET).span
    d = interpret_scan(p, [scan_ok(span, end - timedelta(hours=2))], end, SET)
    assert d.outcome is ScanOutcome.UNAVAILABLE
    assert "too recent" in d.reason


def test_scan_results_must_be_contiguous_and_inside_their_span() -> None:
    p = prog()
    span = plan_scan(p, END, SET).span
    with pytest.raises(PlannerInputError):
        interpret_scan(p, [scan_ok(Span(span.start + B, span.end))], END, SET)
    with pytest.raises(PlannerInputError):
        interpret_scan(p, [scan_ok(span, span.end)], END, SET)
    with pytest.raises(PlannerInputError):
        negative = ScanSpanResult(span, SearchStatus.COMPLETE, ((span.start, -1),))
        interpret_scan(p, [negative], END, SET)


def test_scan_refuses_to_run_once_the_floor_is_known() -> None:
    with pytest.raises(PlannerInputError):
        interpret_scan(prog(floor=T0), [], END, SET)


# ----------------------------------------------------------------------------- step planning


def test_first_step_without_a_floor_is_a_scan() -> None:
    plan = plan_step(prog(), now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.action is StepAction.SCAN
    assert plan.scan_span is not None


def test_read_starts_at_the_floor_then_at_through_plus_one_bucket() -> None:
    p = prog(floor=T0)
    plan = plan_step(p, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.action is StepAction.READ
    assert plan.chunks[0].start == T0
    p2 = prog(floor=T0, through=T0 + 99 * B)
    plan2 = plan_step(p2, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan2.chunks[0].start == T0 + 100 * B == next_start(p2, SET)


def test_span_is_bounded_by_168h_and_by_the_search_budget() -> None:
    p = prog(floor=T0, chunk_buckets=1000)
    plan = plan_step(p, now=STARTED, end=END, budget=StepBudget(10, 100), settings=SET)
    assert plan.span is not None
    assert plan.span.buckets(SET) == 2016
    small = plan_step(p, now=STARTED, end=END, budget=StepBudget(1, 100), settings=SET)
    assert small.span is not None and small.span.buckets(SET) == 1000


def test_chunk_width_follows_density_and_the_failure_ceiling() -> None:
    dense = prog(floor=T0, rows_seen=1000, non_silent_buckets=10)  # 100 rows per bucket
    plan = plan_step(dense, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.chunk_buckets == row_fit_buckets(100, SET) == 32
    assert all(c.buckets(SET) * 100 <= SET.row_target_rows for c in plan.chunks)
    ceil = prog(floor=T0, chunk_buckets=500, chunk_ceiling=40)
    plan = plan_step(ceil, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.chunk_buckets == 40


def test_a_bucket_denser_than_the_cap_still_reads_one_bucket_at_a_time() -> None:
    p = prog(floor=T0, rows_seen=9000, non_silent_buckets=1)
    plan = plan_step(p, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.chunk_buckets == 1


def test_last_span_stops_at_the_moving_end() -> None:
    p = prog(floor=T0, through=END - 8 * B)
    plan = plan_step(p, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.span == Span(END - 7 * B, END)


def test_completion_is_judged_against_the_boundary_passed_in() -> None:
    p = prog(floor=T0, through=END - B)
    plan = plan_step(p, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.action is StepAction.FINISH
    assert plan.trigger is T.FINISHED_COMPLETE
    # the boundary moved on: more to read, no longer finished
    plan2 = plan_step(p, now=STARTED, end=END + 3 * B, budget=BUDGET, settings=SET)
    assert plan2.action is StepAction.READ
    assert plan2.span == Span(END, END + 3 * B)


def test_finish_is_partial_when_the_floor_is_cut_or_a_gap_exists() -> None:
    cut = prog(floor=T0 + timedelta(days=5), through=END - B)
    cut_plan = plan_step(cut, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert cut_plan.trigger is T.FINISHED_PARTIAL
    from app.history.planner import Gap

    gapped = prog(floor=T0, through=END - B, gaps=(Gap(T0, T0 + B, "x"),))
    plan = plan_step(gapped, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.trigger is T.FINISHED_PARTIAL
    assert "could not be read" in plan.reason


def test_switch_off_and_stop_end_the_reading_with_words() -> None:
    base = {"now": STARTED, "end": END, "budget": BUDGET, "settings": SET}
    off = plan_step(prog(floor=T0), **base, enabled=False)
    assert (off.action, off.trigger) == (StepAction.FINISH, T.SWITCHED_OFF)
    stop = plan_step(prog(floor=T0), **base, stop_requested=True)
    assert stop.trigger is T.STOPPED_BY_OPERATOR
    assert "operator" in stop.reason


def test_time_limit_ends_a_reading_that_ran_too_long() -> None:
    late = STARTED + timedelta(hours=SET.max_hours, minutes=1)
    plan = plan_step(prog(floor=T0), now=late, end=END, budget=BUDGET, settings=SET)
    assert plan.trigger is T.TIME_LIMIT
    on_time = STARTED + timedelta(hours=SET.max_hours)
    plan = plan_step(prog(floor=T0), now=on_time, end=END, budget=BUDGET, settings=SET)
    assert plan.action is StepAction.READ


def test_no_budget_waits_and_says_so() -> None:
    for budget in (StepBudget(0, 100), StepBudget(5, 0)):
        for p in (prog(), prog(floor=T0)):
            plan = plan_step(p, now=STARTED, end=END, budget=budget, settings=SET)
            assert plan.action is StepAction.WAIT
            assert plan.reason == "waiting for search budget"
            assert plan.chunks == ()


def test_scan_that_reached_the_end_finishes_unavailable() -> None:
    p = prog(scan_cursor=END)
    plan = plan_step(p, now=STARTED, end=END, budget=BUDGET, settings=SET)
    assert plan.trigger is T.NOTHING_TO_READ


# ----------------------------------------------------------------------------- settling


def test_whole_chunks_advance_through_and_record_density() -> None:
    p = prog(floor=T0)
    results = [ok(T0, 100, rows=500, ns=50), ok(T0 + 100 * B, 100, rows=500, ns=50)]
    s = settle_step(p, results, end=END, settings=SET)
    assert s.advanced
    assert s.progress.through == T0 + 199 * B
    assert s.progress.buckets_read == 200
    assert s.progress.rows_per_bucket == 10
    assert s.progress.searches == 2
    assert s.trigger is None
    assert s.progress.chunk_buckets == row_fit_buckets(10, SET)


def test_a_failed_chunk_never_advances_through() -> None:
    p = prog(floor=T0, through=T0 + 9 * B)
    s = settle_step(p, [bad(T0 + 10 * B, 100, SearchStatus.FAILED)], end=END, settings=SET)
    assert not s.advanced
    assert s.write_spans == ()
    assert s.progress.through == p.through
    assert s.progress.chunk_buckets == 50 == s.progress.chunk_ceiling
    assert "halves" in s.reason


def test_a_truncated_chunk_keeps_nothing_and_halves_the_width() -> None:
    p = prog(floor=T0)
    s = settle_step(p, [bad(T0, 64, SearchStatus.TRUNCATED)], end=END, settings=SET)
    assert not s.advanced and s.progress.through is None
    assert s.progress.chunk_buckets == 32
    assert s.progress.failures == 0  # truncation is not a failure
    assert s.progress.chunk_ceiling is None


def test_a_prefix_survives_a_later_failure_but_nothing_after_it() -> None:
    p = prog(floor=T0)
    results = [
        ok(T0, 50),
        bad(T0 + 50 * B, 50, SearchStatus.FAILED),
        ok(T0 + 100 * B, 50),  # complete, but not contiguous with the prefix: not written
    ]
    s = settle_step(p, results, end=END, settings=SET)
    assert s.write_spans == (Span(T0, T0 + 50 * B),)
    assert s.progress.through == T0 + 49 * B


def test_truncated_single_bucket_is_a_lower_bound_and_is_written() -> None:
    p = prog(floor=T0)
    s = settle_step(p, [bad(T0, 1, SearchStatus.TRUNCATED), ok(T0 + B, 9)], end=END, settings=SET)
    assert s.progress.through == T0 + 9 * B
    assert s.progress.lower_bound_buckets == 1


def test_three_failures_at_the_minimum_width_record_a_gap_and_move_past_it() -> None:
    p = prog(floor=T0, chunk_buckets=1, chunk_ceiling=1)
    for i in range(SET.max_failures - 1):
        s = settle_step(p, [bad(T0, 1, SearchStatus.FAILED)], end=END, settings=SET)
        assert s.progress.through is None and s.new_gap is None
        assert s.progress.failures == i + 1
        p = s.progress
    s = settle_step(p, [bad(T0, 1, SearchStatus.FAILED)], end=END, settings=SET)
    assert s.new_gap is not None
    assert s.new_gap.start == T0 and s.new_gap.end == T0 + B
    assert s.progress.through == T0
    assert s.progress.failures == 0
    assert s.progress.gaps == (s.new_gap,)
    assert not s.advanced and s.write_spans == ()  # no row is written for a gap


def test_failures_above_the_minimum_width_do_not_count_toward_a_gap() -> None:
    p = prog(floor=T0, chunk_buckets=64)
    for width in (64, 32, 16, 8, 4, 2):
        s = settle_step(p, [bad(T0, width, SearchStatus.FAILED)], end=END, settings=SET)
        assert s.progress.failures == 0 and s.new_gap is None
        p = s.progress
    assert p.chunk_buckets == 1


def test_a_success_resets_the_failure_count() -> None:
    p = prog(floor=T0, failures=2, chunk_buckets=1)
    s = settle_step(p, [ok(T0, 1)], end=END, settings=SET)
    assert s.progress.failures == 0


def test_the_gap_list_is_bounded() -> None:
    from app.history.planner import Gap

    gaps = tuple(Gap(T0 + i * B, T0 + (i + 1) * B, "x") for i in range(SET.max_gaps))
    p = prog(
        floor=T0,
        through=T0 + (SET.max_gaps - 1) * B,
        gaps=gaps,
        failures=SET.max_failures - 1,
    )
    start = T0 + SET.max_gaps * B
    s = settle_step(p, [bad(start, 1, SearchStatus.FAILED)], end=END, settings=SET)
    assert s.trigger is T.TOO_MANY_GAPS
    assert len(s.progress.gaps) == SET.max_gaps
    assert s.progress.through == p.through


def test_density_gate_stops_the_reading_after_the_first_span() -> None:
    p = prog(floor=T0)
    results = [ok(T0, 100, lb=10)]  # 10 % lower bounds, limit 5 %
    s = settle_step(p, results, end=END, settings=SET)
    assert s.trigger is T.DENSITY_GATE
    assert "lower bounds" in s.reason
    assert s.progress.through == T0 + 99 * B  # the buckets read stay
    assert s.write_spans == (Span(T0, T0 + 100 * B),)


def test_density_gate_passes_at_the_limit_and_is_checked_once() -> None:
    p = prog(floor=T0)
    s = settle_step(p, [ok(T0, 100, lb=5)], end=END, settings=SET)  # exactly 5 %
    assert s.trigger is None and s.progress.density_checked
    s2 = settle_step(s.progress, [ok(T0 + 100 * B, 100, lb=90)], end=END, settings=SET)
    assert s2.trigger is None  # not re-evaluated after the first span


def test_settling_the_last_chunk_completes_against_the_end() -> None:
    p = prog(floor=T0, through=END - 3 * B)
    s = settle_step(p, [ok(END - 2 * B, 2)], end=END, settings=SET)
    assert s.trigger is T.FINISHED_COMPLETE
    s_partial = settle_step(
        prog(floor=T0 + timedelta(days=3), through=END - 3 * B),
        [ok(END - 2 * B, 2)],
        end=END,
        settings=SET,
    )
    assert s_partial.trigger is T.FINISHED_PARTIAL


def test_a_step_that_stops_short_of_the_end_does_not_finish() -> None:
    p = prog(floor=T0, through=END - 10 * B)
    s = settle_step(p, [ok(END - 9 * B, 2)], end=END, settings=SET)
    assert s.trigger is None


def test_non_contiguous_results_are_a_caller_bug() -> None:
    p = prog(floor=T0)
    with pytest.raises(PlannerInputError):
        settle_step(p, [ok(T0 + B, 5)], end=END, settings=SET)
    with pytest.raises(PlannerInputError):
        settle_step(p, [ok(T0, 5), ok(T0 + 6 * B, 5)], end=END, settings=SET)
    with pytest.raises(PlannerInputError):
        settle_step(prog(), [ok(T0, 5)], end=END, settings=SET)  # no floor yet


def test_not_run_chunks_leave_the_state_alone() -> None:
    p = prog(floor=T0)
    s = settle_step(p, [bad(T0, 10, SearchStatus.NOT_RUN)], end=END, settings=SET)
    assert not s.advanced and s.progress.searches == 0
    assert s.progress.chunk_buckets is None


def test_split_on_truncation_halves_left_first_and_stops_at_one_bucket() -> None:
    left, right = split_on_truncation(Span(T0, T0 + 7 * B), SET) or (None, None)
    assert left == Span(T0, T0 + 3 * B) and right == Span(T0 + 3 * B, T0 + 7 * B)
    assert split_on_truncation(Span(T0, T0 + B), SET) is None


# ----------------------------------------------------------------------------- window fit


def test_window_that_fits_is_kept_and_says_so() -> None:
    fit = fit_window(28, lambda d: 10 * d, end=END, settings=SET)
    assert fit.days_fitted == 28 and not fit.reduced and not fit.refused
    assert fit.target_start == END - timedelta(days=28)
    assert "fits" in fit.reason


def test_window_is_shortened_to_the_largest_that_fits_and_it_is_stated() -> None:
    # 100 searches a day, 10 per tick, 5-minute cadence: 72 h = 864 ticks = 8 640 searches
    fit = fit_window(28, lambda d: 100 * d, end=END, settings=SET)
    assert fit.days_fitted == 28 and fit.hours == 28 * 100 / 10 * 300 / 3600
    heavy = fit_window(28, lambda d: 400 * d, end=END, settings=SET)
    assert heavy.reduced and heavy.days_fitted == 21
    assert heavy.hours <= SET.max_hours
    assert fit_window(28, lambda d: 400 * (d + 1), end=END, settings=SET).days_fitted == 20
    assert heavy.reason == (
        "window reduced from 28 to 21 days: the estimate exceeded 72 hours"
    )
    assert heavy.target_start == END - timedelta(days=21)  # the newest days


def test_window_is_never_below_the_family_minimum_it_is_refused() -> None:
    fit = fit_window(28, lambda d: 2000 * d, end=END, settings=SET)
    assert fit.refused and fit.days_fitted is None and fit.target_start is None
    assert "unavailable" in fit.reason and "7 days" in fit.reason
    ten = fit_window(30, lambda d: 2000 * d, end=END, settings=SET, min_days=10)
    assert ten.refused and "10 days" in ten.reason
    with pytest.raises(PlannerInputError):
        fit_window(5, lambda d: d, end=END, settings=SET)


def test_window_exactly_at_the_limit_fits() -> None:
    limit = SET.max_hours * 3600 // SET.cadence_seconds * SET.searches_per_tick  # 8 640
    assert fit_window(28, lambda d: limit, end=END, settings=SET).days_fitted == 28
    assert fit_window(28, lambda d: limit + 1, end=END, settings=SET).refused


# ----------------------------------------------------------------------------- cost model §B.8


def rows(count: float, buckets: int = 8352, target: int | None = None, st=SET) -> int:
    return searches_for(buckets, count, st, target_rows=target)


@pytest.mark.parametrize(
    ("sources", "searches", "ticks"), [(20, 42, 5), (100, 209, 21), (1000, 2088, 209)]
)
def test_phase_a_cost_small_typical_large(sources: int, searches: int, ticks: int) -> None:
    assert window_buckets(28, SET) == 8352
    assert rows(sources) == searches
    assert ticks_for(searches, 10) == ticks
    assert ticks * 300 / 3600 == pytest.approx({5: 0.4167, 21: 1.75, 209: 17.4167}[ticks], abs=1e-3)


@pytest.mark.parametrize(("rho", "searches"), [(30, 63), (100, 209), (300, 643)])
def test_waf_volume_cost(rho: int, searches: int) -> None:
    assert rows(rho) == searches


def test_waf_large_is_over_the_limit_and_the_window_is_fitted() -> None:
    assert ticks_for(46460, 20) == 2323
    fit = fit_window(
        28, lambda d: math.ceil(46460 * window_buckets(d, SET) / window_buckets(28, SET)),
        end=END, settings=SET, searches_per_tick=20,
    )
    assert fit.reduced and fit.days_fitted is not None and 9 <= fit.days_fitted <= 11
    assert fit.hours <= 72


@pytest.mark.parametrize(
    ("n", "rho_d", "searches"),
    [
        (1, 20, 42),
        (1, 40, 84),
        (1, 50, 105),
        (5, 20, 209),
        (5, 40, 418),
        (5, 50, 522),
        (30, 20, 1392),
        (30, 40, 2784),
        (30, 50, 4176),
    ],
)
def test_ips_dimension_cost(n: int, rho_d: int, searches: int) -> None:
    assert rows(n * rho_d) == searches


def test_ips_large_is_fitted_to_about_twenty_days() -> None:
    assert ticks_for(12032, 10) == 1204
    fit = fit_window(
        28, lambda d: math.ceil(12032 * window_buckets(d, SET) / window_buckets(28, SET)),
        end=END, settings=SET,
    )
    assert fit.reduced and 19 <= fit.days_fitted <= 21  # type: ignore[operator]


@pytest.mark.parametrize(("rho", "searches"), [(20, 105), (150, 836), (800, 4176)])
def test_catchall_cost_uses_its_own_row_cap(rho: int, searches: int) -> None:
    catchall = PlannerSettings(row_cap=2000)
    assert catchall.row_target_rows == 1600
    assert searches_for(8352, rho, catchall) == searches


@pytest.mark.parametrize(("rho", "searches"), [(30, 7), (80, 16), (150, 30)])
def test_rule_match_cost_adds_the_console_total(rho: int, searches: int) -> None:
    hourly = PlannerSettings(bucket_seconds=3600, row_cap=5000)
    assert window_buckets(30, hourly) == 744
    assert searches_for(744, rho, hourly, target_rows=4000) + 1 == searches


def test_cost_edge_cases() -> None:
    assert searches_for(0, 10, SET) == 0
    assert searches_for(100, 0, SET) == 1
    assert searches_for(100, 10**9, SET) == 100  # a bucket denser than the cap: one per bucket
    with pytest.raises(PlannerInputError):
        ticks_for(10, 0)


# ----------------------------------------------------------------------------- searches counted


def test_settle_sums_the_searches_each_chunk_spent() -> None:
    p = prog(floor=T0, through=None)
    assert ChunkResult(Span(T0, T0 + B), SearchStatus.COMPLETE).searches == 1  # the default
    one = ok(T0, 12)
    # a chunk that split on truncation
    two = ChunkResult(Span(T0 + 12 * B, T0 + 24 * B), SearchStatus.COMPLETE, searches=4)
    out = settle_step(p, [one, two], end=END, settings=SET)
    assert out.progress.searches == 1 + 4


def test_settle_counts_a_failed_chunk_and_later_chunks_that_ran_but_not_not_run() -> None:
    p = prog(floor=T0, through=None)
    failed = ChunkResult(Span(T0, T0 + 12 * B), SearchStatus.FAILED, searches=2)
    later = ChunkResult(Span(T0 + 12 * B, T0 + 24 * B), SearchStatus.COMPLETE, searches=3)
    skipped = ChunkResult(Span(T0 + 24 * B, T0 + 36 * B), SearchStatus.NOT_RUN, searches=9)
    out = settle_step(p, [failed, later, skipped], end=END, settings=SET)
    assert out.progress.searches == 2 + 3 + 0


def test_a_negative_chunk_search_count_is_refused() -> None:
    with pytest.raises(PlannerInputError):
        ChunkResult(Span(T0, T0 + B), SearchStatus.COMPLETE, searches=-1)


def test_interpret_scan_counts_the_scan_searches_on_every_outcome() -> None:
    from dataclasses import replace

    p = replace(prog(), searches=5)
    span = plan_scan(p, END, SET).span
    assert span is not None
    # FLOOR_FOUND
    found = interpret_scan(p, [scan_ok(span, span.start)], END, SET)
    assert found.outcome is ScanOutcome.FLOOR_FOUND and found.progress.searches == 6
    # CONTINUE (an empty span)
    nothing = interpret_scan(p, [scan_ok(span)], END, SET)
    assert nothing.outcome is ScanOutcome.CONTINUE and nothing.progress.searches == 6
    # RETRY for a failed search, and NOT_RUN adds nothing
    failed = interpret_scan(p, [ScanSpanResult(span, SearchStatus.FAILED)], END, SET)
    assert failed.outcome is ScanOutcome.RETRY and failed.progress.searches == 6
    unrun = interpret_scan(p, [ScanSpanResult(span, SearchStatus.NOT_RUN)], END, SET)
    assert unrun.outcome is ScanOutcome.RETRY and unrun.progress.searches == 5
    # UNAVAILABLE: the last failure
    last = replace(p, scan_failures=SET.max_failures - 1)
    dead = interpret_scan(last, [ScanSpanResult(span, SearchStatus.FAILED)], END, SET)
    assert dead.outcome is ScanOutcome.UNAVAILABLE and dead.progress.searches == 6
    # UNAVAILABLE: no event in the whole window (one result reaching the end)
    wide = replace(p, scan_cursor=END - 12 * B)
    tail = Span(END - 12 * B, END)
    over = interpret_scan(wide, [scan_ok(tail)], END, SET)
    assert over.outcome is ScanOutcome.UNAVAILABLE and over.progress.searches == 6
    # two results, two searches
    two = interpret_scan(p, [scan_ok(span), scan_ok(Span(span.end, span.end + 12 * B))],
                         END, SET)
    assert two.progress.searches == 7
