"""Seeded property tests of the history planner (no `hypothesis` dependency).

A scripted family drives `plan_step` and `settle_step` tick by tick with random search
outcomes and checks the invariants of HISTORY-BACKFILL §B.6: the written spans tile
`[F, end)` exactly, `through` never advances without a written span, nothing is written
before `F`, and no span passes 168 h or the row target.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.history.planner import (
    CEILING_RELEASE_STEPS,
    ChunkResult,
    PlannerSettings,
    ScanOutcome,
    ScanSpanResult,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    fit_window,
    interpret_scan,
    new_progress,
    plan_scan,
    plan_step,
    settle_step,
    split_on_truncation,
    window_buckets,
)
from app.history.state import HistoryState, HistoryTrigger, next_state

END0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SEEDS = range(60)


def _settings(rng: random.Random) -> PlannerSettings:
    minimum = rng.choice([1, 1, 12])
    return PlannerSettings(
        row_cap=rng.choice([500, 2000, 5000]),
        initial_chunk_buckets=rng.choice([minimum, 12, 288]),
        min_chunk_buckets=minimum,
        max_failures=rng.choice([1, 3]),
        max_hours=10_000,
        max_lower_bound_share=1.0,  # these runs test tiling, not the gate
        max_gaps=rng.choice([20, 1000]),
    )


def _events(rng, span, b0, st):
    """Totals of a scope that sends from `b0` on: sparse, but never silent for long."""
    out = []
    for i in range(span.buckets(st)):
        b = span.start + i * st.bucket
        if b >= b0 and (b == b0 or rng.random() < 0.8 or i == 0):
            out.append((b, rng.randint(1, 50)))
    return out


def _run_chunk(rng, span, p_fail, p_trunc, st, split: bool) -> list[ChunkResult]:
    n = span.buckets(st)
    roll = rng.random()
    if roll < p_fail:
        return [ChunkResult(span, SearchStatus.FAILED)]
    if roll < p_fail + p_trunc:
        halves = split_on_truncation(span, st) if split else None
        if halves is None:
            return [ChunkResult(span, SearchStatus.TRUNCATED)]
        return _run_chunk(rng, halves[0], p_fail, p_trunc, st, split) + _run_chunk(
            rng, halves[1], p_fail, p_trunc, st, split
        )
    rows = n * rng.randint(0, 6)
    return [ChunkResult(span, SearchStatus.COMPLETE, rows=rows, non_silent_buckets=n)]


def _drive(seed: int, *, moving_end: bool, split: bool):
    rng = random.Random(seed)
    st = _settings(rng)
    bucket = st.bucket
    days = rng.choice([1, 3, 9])
    end = END0
    t0 = end - timedelta(days=days)
    now = END0
    progress = new_progress(t0, now, st)
    # the scope's first event lies somewhere around the window start
    b0 = t0 - timedelta(hours=24) + bucket * rng.randint(0, days * 120)
    if rng.random() < 0.5:
        b0 = t0 - timedelta(days=2)  # QRadar holds the whole asked window
    p_fail, p_trunc = rng.choice([(0.0, 0.0), (0.3, 0.2), (0.5, 0.0), (0.1, 0.5)])
    written: list[Span] = []
    gaps: list[Span] = []
    final = None
    for _ in range(40_000):
        if moving_end and rng.random() < 0.5:
            end += bucket * rng.randint(1, 3)
        now += timedelta(minutes=5)
        budget = StepBudget(rng.randint(0, 12), 120)
        plan = plan_step(progress, now=now, end=end, budget=budget, settings=st)
        if plan.action is StepAction.FINISH:
            final = plan
            break
        if plan.action is StepAction.WAIT:
            continue
        if plan.action is StepAction.SCAN:
            assert plan.scan_span is not None
            span = plan.scan_span
            assert span.buckets(st) <= st.max_span_buckets
            ev = _events(rng, span, b0, st)
            d = interpret_scan(
                progress, [ScanSpanResult(span, SearchStatus.COMPLETE, tuple(ev))], end, st
            )
            progress = d.progress
            continue
        # READ
        before = progress
        assert plan.span is not None and plan.chunk_buckets is not None
        floor = progress.floor
        assert floor is not None
        assert plan.span.start >= floor  # no span starts before F
        assert plan.span.buckets(st) <= st.max_span_buckets  # never more than 168 h
        assert len(plan.chunks) <= budget.searches
        rho = progress.rows_per_bucket
        for c in plan.chunks:
            n = c.buckets(st)
            assert n <= plan.chunk_buckets
            if rho is not None and n > st.min_chunk_buckets:
                assert n * rho <= st.row_target_rows  # values x buckets <= the row target
        results: list[ChunkResult] = []
        for c in plan.chunks:
            results += _run_chunk(rng, c, p_fail, p_trunc, st, split)
            if results[-1].status is not SearchStatus.COMPLETE and rng.random() < 0.7:
                break  # the driver stops at the first search that did not complete
        s = settle_step(progress, results, end=end, settings=st)
        progress = s.progress
        # §B.6.3: the release counter is never negative, and is 0 when there is no ceiling.
        assert progress.ceiling_successes >= 0
        if progress.chunk_ceiling is None:
            assert progress.ceiling_successes == 0
        # through moves only when a span was written, or a gap was recorded
        if s.write_spans or s.new_gap:
            assert progress.through is not None
        else:
            assert progress.through == before.through
        for w in s.write_spans:
            assert w.start >= floor  # nothing before F
            written.append(w)
        if s.new_gap:
            gaps.append(Span(s.new_gap.start, s.new_gap.end))
        if s.trigger is not None:
            final = s
            break
    assert final is not None, f"seed {seed} did not finish"
    return st, progress, written, gaps, end, final


def _bucket_set(spans: list[Span], st: PlannerSettings) -> list[datetime]:
    out: list[datetime] = []
    for sp in spans:
        for i in range(sp.buckets(st)):
            out.append(sp.start + i * st.bucket)
    return out


@pytest.mark.parametrize("split", [True, False])
@pytest.mark.parametrize("moving_end", [False, True])
def test_spans_tile_floor_to_end_with_no_gap_and_no_overlap(moving_end: bool, split: bool) -> None:
    finished_ok = 0
    for seed in SEEDS:
        st, progress, written, gaps, end, final = _drive(seed, moving_end=moving_end, split=split)
        assert progress.floor is not None or final.trigger is HistoryTrigger.NOTHING_TO_READ
        if progress.floor is None:
            assert not written
            continue
        covered = sorted(_bucket_set(written, st) + _bucket_set(gaps, st))
        assert len(covered) == len(set(covered)), f"seed {seed}: overlap"
        if final.trigger in (HistoryTrigger.FINISHED_COMPLETE, HistoryTrigger.FINISHED_PARTIAL):
            finished_ok += 1
            count = (end - progress.floor) // st.bucket
            expected = [progress.floor + i * st.bucket for i in range(count)]
            assert covered == expected, f"seed {seed}: not a tiling of [F, end)"
        else:
            # stopped early (too many gaps): what was read is still a gap-free prefix from F
            expected = [progress.floor + i * st.bucket for i in range(len(covered))]
            assert covered == expected
        # the stored boundary is the last bucket covered
        if covered:
            assert progress.through == covered[-1]
    assert finished_ok >= len(SEEDS) // 2


def test_a_failed_or_truncated_search_never_advances_through() -> None:
    for seed in SEEDS:
        rng = random.Random(1000 + seed)
        st = _settings(rng)
        t0 = END0 - timedelta(days=2)
        start_at = t0 + rng.randint(0, 100) * st.bucket
        through = start_at - st.bucket if rng.random() < 0.7 else None
        base = new_progress(t0, END0, st)
        from dataclasses import replace

        p = replace(base, floor=t0, through=through if through is not None else None)
        nxt = start_at if through is not None else t0
        n = rng.randint(2, 200)
        status = rng.choice([SearchStatus.FAILED, SearchStatus.TRUNCATED, SearchStatus.NOT_RUN])
        chunk = ChunkResult(Span(nxt, nxt + n * st.bucket), status)
        s = settle_step(p, [chunk], end=END0, settings=st)
        assert s.progress.through == p.through, status
        assert s.write_spans == ()
        assert not s.advanced
        assert s.trigger is None or s.trigger is HistoryTrigger.TOO_MANY_GAPS


def test_plan_step_invariants_over_random_progress() -> None:
    from dataclasses import replace

    for seed in SEEDS:
        rng = random.Random(5000 + seed)
        st = _settings(rng)
        t0 = END0 - timedelta(days=rng.choice([2, 10, 28]))
        floor = t0 + st.bucket * rng.randint(0, 300)
        through = floor + st.bucket * rng.randint(0, 1000) if rng.random() < 0.6 else None
        ceiling = rng.choice([None, 3, 50])
        p = replace(
            new_progress(t0, END0, st),
            floor=floor,
            through=through,
            chunk_buckets=rng.choice([None, 1, 7, 288, 5000]),
            chunk_ceiling=ceiling,
            ceiling_successes=(
                rng.randint(0, CEILING_RELEASE_STEPS - 1) if ceiling is not None else 0
            ),
            rows_seen=rng.randint(0, 10_000),
            non_silent_buckets=rng.randint(0, 50),
        )
        assert p.ceiling_successes >= 0
        if p.chunk_ceiling is None:
            assert p.ceiling_successes == 0
        budget = StepBudget(rng.randint(1, 64), 60)
        plan = plan_step(p, now=END0, end=END0, budget=budget, settings=st)
        if plan.action is not StepAction.READ:
            assert plan.action is StepAction.FINISH
            continue
        start = (floor if through is None else through + st.bucket)
        assert plan.chunks[0].start == start
        assert plan.chunks[-1].end <= END0
        # chunks tile the planned span
        for a, b in zip(plan.chunks, plan.chunks[1:], strict=False):
            assert a.end == b.start
        assert plan.span is not None and plan.span.buckets(st) <= 2016
        assert len(plan.chunks) <= budget.searches
        rho = p.rows_per_bucket
        for c in plan.chunks:
            n = c.buckets(st)
            assert n <= max(p.chunk_ceiling or n, st.min_chunk_buckets)
            if rho is not None and n > st.min_chunk_buckets:
                assert n * rho <= st.row_target_rows


def test_the_window_fits_the_budget_or_is_refused_with_its_reason() -> None:
    for seed in SEEDS:
        rng = random.Random(9000 + seed)
        st = PlannerSettings(
            max_hours=rng.choice([6, 24, 72, 168]),
            searches_per_tick=rng.choice([1, 10, 64]),
            cadence_seconds=rng.choice([300, 900, 3600]),
        )
        per_day = rng.randint(1, 3000)
        days = rng.randint(7, 30)
        min_days = rng.choice([7, 7, 10])
        days = max(days, min_days)
        est = lambda d, per_day=per_day: per_day * d  # noqa: E731
        fit = fit_window(days, est, end=END0, settings=st, min_days=min_days)
        assert fit.reason
        if fit.refused:
            assert fit.days_fitted is None
            assert fit.hours > st.max_hours  # even the smallest window does not fit
            assert "unavailable" in fit.reason
            assert est(min_days) == fit.searches
        else:
            assert fit.days_fitted is not None
            assert min_days <= fit.days_fitted <= days
            assert fit.hours <= st.max_hours
            if fit.days_fitted < days:
                assert fit.reduced and "reduced" in fit.reason
                larger = est(fit.days_fitted + 1)
                tick_hours = -(-larger // st.searches_per_tick) * st.cadence_seconds / 3600
                assert tick_hours > st.max_hours  # the largest that fits
            assert fit.target_start == END0 - timedelta(days=fit.days_fitted)


def test_scan_finds_the_same_floor_however_the_spans_are_cut() -> None:
    for seed in SEEDS:
        rng = random.Random(7000 + seed)
        st = PlannerSettings()
        t0 = END0 - timedelta(days=rng.choice([14, 28]))
        b0 = t0 - timedelta(hours=24) + st.bucket * rng.randint(0, 28 * 288 + 288)
        p = new_progress(t0, END0, st)
        for _ in range(100):
            plan = plan_scan(p, END0, st)
            if plan.span is None:
                break
            ev = [(b0, 1), (b0 + 5 * st.bucket, 4)] if plan.span.start <= b0 < plan.span.end else []
            ev = [(b, n) for b, n in ev if plan.span.start <= b < plan.span.end]
            scanned = ScanSpanResult(plan.span, SearchStatus.COMPLETE, tuple(ev))
            d = interpret_scan(p, [scanned], END0, st)
            p = d.progress
            if d.outcome is not ScanOutcome.CONTINUE:
                break
        want = max(t0, b0 + st.margin)
        if p.floor is not None:
            assert p.floor == want
            assert p.floor >= t0
        else:
            assert b0 + st.margin >= END0 - timedelta(seconds=1) or b0 >= END0


def test_state_machine_accepts_every_trigger_the_planner_can_emit() -> None:
    """Whatever trigger the planner returns is legal from READING."""
    seen = set()
    for seed in SEEDS:
        _, _, _, _, _, final = _drive(seed, moving_end=bool(seed % 2), split=bool(seed % 3))
        trigger = final.trigger
        assert trigger is not None
        seen.add(trigger)
        change = next_state(HistoryState.READING, trigger)
        assert change.reason
    assert HistoryTrigger.FINISHED_COMPLETE in seen


def test_window_buckets_matches_the_margin_arithmetic() -> None:
    st = PlannerSettings()
    for days in (7, 14, 28):
        assert window_buckets(days, st) == days * 288 + 288
