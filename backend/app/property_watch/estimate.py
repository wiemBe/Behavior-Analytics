"""What reading a watch's past would cost, from the planner's own arithmetic (PHASE-G §H.4.5).

The design's table (§H.4.5) is optimistic: it does not charge the 1.25 density safety factor,
TOTALS and PINNED per step span, or the first 288-bucket chunk that truncates at high density
(PW-13b's simulation, `docs/tasks/PW-13b.report.md` §3). So the estimate is not a formula. It
drives `plan_pw_step` and `settle_pw_step` over a synthetic reading where every bucket holds
the same number of rows and QRadar holds the whole window, and counts what the planner spends.

Pure: no I/O, no clock, no database. A zero is never drawn for a reading that did not finish:
`simulate_reading` returns `None` when the planner does not hand off within its tick bound.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.core.config import Settings
from app.history.planner import (
    ChunkResult,
    PlannerSettings,
    ScanOutcome,
    ScanSpanResult,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    interpret_scan,
    split_on_truncation,
)
from app.history.state import HistoryTrigger
from app.property_watch.bootstrap import (
    plan_pw_step,
    pw_planner_settings,
    settle_pw_step,
    start_progress,
)

#: A fixed, bucket-aligned boundary: the arithmetic does not depend on the day it runs.
_BOUNDARY = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
#: Events of the synthetic scope start this many days before the window, so Ariel holds it all.
_LEAD_DAYS = 3
#: A reading that has not handed off after this many ticks is not estimated.
MAX_TICKS = 5000

ASSUMPTIONS = (
    "Every bucket holds events; QRadar holds the whole window; "
    "the search time per search is not modelled."
)


@dataclass(frozen=True)
class Reading:
    searches: int
    ticks: int


@dataclass(frozen=True)
class HistoryEstimate:
    searches_min: int
    searches_max: int
    ticks_min: int
    ticks_max: int
    cadence_seconds: int
    assumptions: str


def simulate_reading(
    *,
    days: int,
    rows_per_bucket: int,
    pinned_rows_per_bucket: int,
    settings: Settings,
    cadence_seconds: int,
) -> Reading | None:
    """What the planner spends reading `days` at a constant `rows_per_bucket`.

    `ticks` counts every tick up to and including the hand-off tick; the boundary moves by
    the buckets one cadence covers, as it does while a reading runs. `None` when the reading
    does not complete within `MAX_TICKS` or ends in another way (for example one bucket that
    alone exceeds the row cap ends at the density gate): no figure is better than a wrong one.
    """
    if days < 1 or rows_per_bucket < 1 or pinned_rows_per_bucket < 0:
        raise ValueError("a reading needs days >= 1 and a density >= 1")
    planner = pw_planner_settings(settings, cadence_seconds=cadence_seconds)
    bucket = planner.bucket
    boundary = _BOUNDARY
    now = boundary
    progress = start_progress(history_days=days, boundary=boundary, now=now, settings=planner)
    first_event = progress.target_start - _LEAD_DAYS * 86400 // planner.bucket_seconds * bucket
    per_tick = settings.pw_bootstrap_searches_per_tick
    advance = max(1, cadence_seconds // planner.bucket_seconds) * bucket
    searches = 0
    for ticks in range(1, MAX_TICKS + 1):
        left = per_tick
        scan: ScanSpanResult | None = None
        done = False
        while not done:
            plan = plan_pw_step(
                progress,
                now=now,
                boundary=boundary,
                budget=StepBudget(left, 1e9),
                pinned_rows_per_bucket=pinned_rows_per_bucket,
                enabled=True,
                settings=planner,
                scan=scan,
            )
            action = plan.step.action
            if action is StepAction.FINISH:
                return _finished(plan.step.trigger, searches, ticks)
            if action is StepAction.WAIT:
                break
            if action is StepAction.SCAN:
                assert plan.totals_span is not None
                left -= 1
                searches += 1
                result = _scan(plan.totals_span, first_event, rows_per_bucket, planner)
                decision = interpret_scan(progress, [result], boundary, planner)
                progress = decision.progress
                if decision.outcome is ScanOutcome.FLOOR_FOUND:
                    scan = result
                elif decision.outcome is ScanOutcome.UNAVAILABLE:
                    return None
                continue
            wide = (0 if plan.reuses_scan_totals else 1) + (1 if plan.pinned_span else 0)
            left -= wide
            searches += wide
            results: list[ChunkResult] = []
            for chunk in plan.grouped_chunks:
                spent = _grouped(chunk, left, rows_per_bucket, planner, results)
                left -= spent
                searches += spent
            settled = settle_pw_step(
                progress,
                totals=SearchStatus.COMPLETE,
                pinned=SearchStatus.COMPLETE if plan.pinned_span else None,
                grouped=results,
                totals_reused=plan.reuses_scan_totals,
                end=boundary,
                settings=planner,
            )
            progress = settled.progress
            if settled.trigger is not None:
                return _finished(settled.trigger, searches, ticks)
            done = True
        boundary += advance
        now += advance
    return None


def _finished(trigger: HistoryTrigger | None, searches: int, ticks: int) -> Reading | None:
    """Only a reading that completes has a cost worth reporting.

    A reading that ends early (the density gate, too many gaps, the time limit) read less than
    the request, so its cost is not the cost of the request: no figure (fail closed).
    """
    if trigger is HistoryTrigger.FINISHED_COMPLETE:
        return Reading(searches, ticks)
    return None


def _scan(
    span: Span, first_event: datetime, rows: int, planner: PlannerSettings
) -> ScanSpanResult:
    totals = []
    cursor = span.start
    while cursor < span.end:
        if cursor >= first_event:
            totals.append((cursor, 5 * rows))
        cursor += planner.bucket
    return ScanSpanResult(span, SearchStatus.COMPLETE, tuple(totals))


def _grouped(
    chunk: Span,
    left: int,
    rows: int,
    planner: PlannerSettings,
    out: list[ChunkResult],
) -> int:
    """One chunk, split in time on truncation while the tick has budget; searches spent."""
    queue = [chunk]
    mine: list[ChunkResult] = []
    spent = 0
    while queue:
        piece = queue.pop(0)
        if left - spent <= 0:
            mine.append(ChunkResult(piece, SearchStatus.NOT_RUN))
            continue
        spent += 1
        n = piece.buckets(planner)
        if rows * n > planner.row_cap:
            halves = split_on_truncation(piece, planner)
            if halves is None:
                mine.append(
                    ChunkResult(
                        piece,
                        SearchStatus.TRUNCATED,
                        rows=planner.row_cap,
                        non_silent_buckets=1,
                        lower_bound_buckets=1,
                    )
                )
            else:
                queue[0:0] = list(halves)
        else:
            mine.append(
                ChunkResult(piece, SearchStatus.COMPLETE, rows=rows * n, non_silent_buckets=n)
            )
    ran = [i for i, r in enumerate(mine) if r.status is not SearchStatus.NOT_RUN]
    if not ran and spent:
        # the budget ended right after a truncation: the chunk stays truncated, unsplit
        out.append(ChunkResult(chunk, SearchStatus.TRUNCATED, searches=spent))
        return spent
    if ran:
        first = mine[ran[0]]
        extra = spent - sum(r.searches for r in mine if r.status is not SearchStatus.NOT_RUN)
        mine[ran[0]] = replace(first, searches=first.searches + extra)
    out.extend(mine)
    return spent


def estimate_history(
    *,
    days: int,
    pinned_rows_per_bucket: int,
    settings: Settings,
    cadence_seconds: int,
) -> HistoryEstimate | None:
    """The low and the high figure for a request of `days`, or `None` when none can be given.

    Low density: the pinned rows per bucket (at least one). High density: the active-value
    ceiling `pw_max_active_values_per_watch` (§H.4.5's 200-value row), never below the low one.
    """
    low = max(1, pinned_rows_per_bucket)
    high = max(low, settings.pw_max_active_values_per_watch)
    least = simulate_reading(
        days=days,
        rows_per_bucket=low,
        pinned_rows_per_bucket=pinned_rows_per_bucket,
        settings=settings,
        cadence_seconds=cadence_seconds,
    )
    most = simulate_reading(
        days=days,
        rows_per_bucket=high,
        pinned_rows_per_bucket=pinned_rows_per_bucket,
        settings=settings,
        cadence_seconds=cadence_seconds,
    )
    if least is None or most is None:
        return None
    return HistoryEstimate(
        searches_min=min(least.searches, most.searches),
        searches_max=max(least.searches, most.searches),
        ticks_min=min(least.ticks, most.ticks),
        ticks_max=max(least.ticks, most.ticks),
        cadence_seconds=cadence_seconds,
        assumptions=ASSUMPTIONS,
    )


__all__ = [
    "ASSUMPTIONS",
    "MAX_TICKS",
    "HistoryEstimate",
    "Reading",
    "estimate_history",
    "simulate_reading",
]
