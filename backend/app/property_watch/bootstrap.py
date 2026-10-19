"""The property-watch adapter on the shared history planner (PHASE-G §H.2 to §H.5, §H.10).

The planner, the floor scan, width adaptation, halving, gaps, the density gate and the
completion rule live in `app.history` and are called here, not repeated. This module adds
only what the shared planner does not know about a watch:

* the reserved TOTALS and PINNED searches of a step and the pinned-span cap (`plan_pw_step`);
* the downgrade of a chunk when its span's TOTALS or PINNED search was not whole
  (`settle_pw_step`);
* the watch's own columns and states (`history_columns`, `read_progress`, `apply_trigger`,
  `begin_reading`, `handoff_columns`, `restart_columns`);
* the history sub-budget of a tick (`pw_history_budget`).

Pure: no database, no provider, no clock. Callers pass `now` and the boundary. Only words
reach the stored reason, never a value text, a property value or AQL (repository rule 3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.core.config import Settings
from app.history.planner import (
    ChunkResult,
    PlannerInputError,
    PlannerSettings,
    ScanSpanResult,
    ScopeProgress,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    StepPlan,
    StepSettlement,
    new_progress,
    next_start,
    plan_step,
    settle_step,
)
from app.history.progress_codec import ProgressCodecError, decode_progress, encode_progress
from app.history.state import HistoryState, HistoryTrigger, next_state
from app.models.enums import PwHistoryState

# §H.10: constants of the planner, not settings.
INITIAL_CHUNK_BUCKETS = 288  # one day of 5-minute buckets, until a density is observed
MIN_CHUNK_BUCKETS = 12  # one hour: the width below which a failure becomes a gap
ROW_TARGET_SHARE = 0.8  # a search aims at 80 % of pw_max_rows_per_search
MAX_GAPS = 20
MAX_SPAN_HOURS = 168

WAITING_FOR_BUDGET = "waiting for search budget"
UNREADABLE_PROGRESS = "the stored history progress could not be read back"

_TO_HISTORY: dict[PwHistoryState, HistoryState] = {
    PwHistoryState.NOT_REQUESTED: HistoryState.NOT_REQUESTED,
    PwHistoryState.READING: HistoryState.READING,
    PwHistoryState.COMPLETE: HistoryState.COMPLETE,
    PwHistoryState.PARTIAL: HistoryState.PARTIAL,
    PwHistoryState.UNAVAILABLE: HistoryState.UNAVAILABLE,
}
_FROM_HISTORY: dict[HistoryState, PwHistoryState] = {v: k for k, v in _TO_HISTORY.items()}
_RESTARTABLE = (
    PwHistoryState.READING,
    PwHistoryState.COMPLETE,
    PwHistoryState.PARTIAL,
    PwHistoryState.UNAVAILABLE,
)
_HANDOFF_STATES = (PwHistoryState.COMPLETE, PwHistoryState.PARTIAL, PwHistoryState.UNAVAILABLE)


class BootstrapStateError(ValueError):
    """A state or a stored progress that the adapter cannot map or read back exactly."""


# --------------------------------------------------------------------------- settings


def pw_planner_settings(settings: Settings, *, cadence_seconds: int) -> PlannerSettings:
    """The shared planner's numbers for a property watch (§H.10)."""
    return PlannerSettings(
        bucket_seconds=settings.collection_interval_seconds,
        floor_margin_hours=settings.pw_bootstrap_floor_margin_hours,
        max_span_hours=MAX_SPAN_HOURS,
        row_cap=settings.pw_max_rows_per_search,
        row_target_share=ROW_TARGET_SHARE,
        initial_chunk_buckets=INITIAL_CHUNK_BUCKETS,
        min_chunk_buckets=MIN_CHUNK_BUCKETS,
        max_failures=settings.pw_bootstrap_max_failures,
        max_lower_bound_share=settings.pw_bootstrap_max_lower_bound_share,
        max_hours=settings.pw_bootstrap_max_hours,
        max_gaps=MAX_GAPS,
        searches_per_tick=settings.pw_bootstrap_searches_per_tick,
        cadence_seconds=cadence_seconds,
    )


# --------------------------------------------------------------------------- states


def to_history_state(state: PwHistoryState) -> HistoryState:
    return _TO_HISTORY[state]


def from_history_state(state: HistoryState) -> PwHistoryState:
    """`WAITING` and `NOT_ELIGIBLE` never apply to a watch: a watch reads with a moving end
    and needs no anchor, and a watch with stored buckets is refused at the API (§H.5.5)."""
    mapped = _FROM_HISTORY.get(state)
    if mapped is None:
        raise BootstrapStateError(f"history state {state.value} does not apply to a property watch")
    return mapped


def apply_trigger(
    state: PwHistoryState, trigger: HistoryTrigger
) -> tuple[PwHistoryState, str]:
    """The next state and its reason through the core's table; an illegal pair raises
    `IllegalTransitionError`, a target a watch cannot hold raises `BootstrapStateError`."""
    change = next_state(to_history_state(state), trigger)
    return from_history_state(change.state), change.reason


# --------------------------------------------------------------------------- columns


class _HasHistoryColumns(Protocol):
    history_progress: object
    history_target_start: datetime | None
    history_floor: datetime | None
    history_through: datetime | None
    history_started_at: datetime | None


def history_columns(
    progress: ScopeProgress | None, *, state: PwHistoryState, reason: str
) -> dict[str, Any]:
    """Every `history_*` value the collector writes for a step (§H.5.1, §H.5.6).

    `history_progress` holds the codec's JSON with the reason in the adapter object; the
    four scalar columns repeat `target_start`, `floor`, `through`, `started_at` for SQL
    readers. With no progress (nothing was started) the columns are NULL and the JSON `{}`.
    `history_end` and `history_finished_at` are set only by `handoff_columns`.
    """
    if progress is None:
        return {
            "history_state": state,
            "history_target_start": None,
            "history_floor": None,
            "history_through": None,
            "history_started_at": None,
            "history_progress": {},
        }
    return {
        "history_state": state,
        "history_target_start": progress.target_start,
        "history_floor": progress.floor,
        "history_through": progress.through,
        "history_started_at": progress.started_at,
        "history_progress": encode_progress(progress, adapter={"reason": reason}),
    }


def read_progress(watch: _HasHistoryColumns) -> tuple[ScopeProgress, str] | None:
    """The stored progress and reason, or None when the reading has not started.

    None only when `history_progress` is `{}` and every progress column is NULL. Anything
    else must decode and agree with the four columns, or `BootstrapStateError` names the
    column(s): a defaulted `through` would read a span twice or skip one. PW-13c1 answers
    that error by handing the watch off `PARTIAL` with the reason "the stored history
    progress could not be read back", using the `history_through` column (it is written in
    the same transaction as the buckets, §H.5.2).
    """
    columns = {
        "history_target_start": watch.history_target_start,
        "history_floor": watch.history_floor,
        "history_through": watch.history_through,
        "history_started_at": watch.history_started_at,
    }
    stored = watch.history_progress
    if isinstance(stored, Mapping) and not stored:
        set_columns = [name for name, value in columns.items() if value is not None]
        if not set_columns:
            return None
        raise BootstrapStateError(
            "history_progress is empty but " + ", ".join(set_columns) + " is set"
        )
    try:
        decoded = decode_progress(stored)
    except ProgressCodecError as exc:
        raise BootstrapStateError(f"history_progress cannot be read: {exc}") from exc
    progress = decoded.progress
    expected = {
        "history_target_start": progress.target_start,
        "history_floor": progress.floor,
        "history_through": progress.through,
        "history_started_at": progress.started_at,
    }
    differing = [name for name, value in expected.items() if columns[name] != value]
    if differing:
        raise BootstrapStateError(
            "history column(s) differ from history_progress: " + ", ".join(differing)
        )
    reason = decoded.adapter.get("reason")
    if not isinstance(reason, str):
        raise BootstrapStateError("history_progress has no reason in its adapter object")
    return progress, reason


# --------------------------------------------------------------------------- beginning


@dataclass(frozen=True)
class PwBegin:
    state: PwHistoryState
    reason: str
    trigger: HistoryTrigger
    progress: ScopeProgress | None
    columns: dict[str, Any]


def start_progress(
    *, history_days: int, boundary: datetime, now: datetime, settings: PlannerSettings
) -> ScopeProgress:
    """The progress of a reading that has not searched: `T0 = boundary - history_days`.

    Also what PW-13c1 uses for a `READING` watch whose columns were cleared by a restart
    (`read_progress` returned None): the state is already `READING`, so no trigger applies.
    """
    if history_days < 1:
        raise BootstrapStateError("history_days must ask for at least one day")
    target = boundary - timedelta(days=history_days)
    # new_progress refuses an unaligned T0 (a bucket that divides the day keeps it aligned).
    return new_progress(target, now, settings)


def begin_reading(
    *,
    history_days: int,
    boundary: datetime,
    now: datetime,
    enabled: bool,
    settings: PlannerSettings,
) -> PwBegin:
    """Start the reading of a `NOT_REQUESTED` watch that asks for history (§H.2.3, §H.6 row 11)."""
    if not enabled:
        state, reason = apply_trigger(PwHistoryState.NOT_REQUESTED, HistoryTrigger.SWITCHED_OFF)
        columns = handoff_columns(
            None, state=state, reason=reason, boundary=boundary, now=now, settings=settings
        )
        return PwBegin(state, reason, HistoryTrigger.SWITCHED_OFF, None, columns)
    state, reason = apply_trigger(PwHistoryState.NOT_REQUESTED, HistoryTrigger.REQUEST_READY)
    progress = start_progress(
        history_days=history_days, boundary=boundary, now=now, settings=settings
    )
    return PwBegin(
        state,
        reason,
        HistoryTrigger.REQUEST_READY,
        progress,
        history_columns(progress, state=state, reason=reason),
    )


# --------------------------------------------------------------------------- planning


@dataclass(frozen=True)
class PwStepPlan:
    step: StepPlan
    totals_span: Span | None
    pinned_span: Span | None
    grouped_chunks: tuple[Span, ...]
    reuses_scan_totals: bool
    searches: int  # what the step will cost: TOTALS + PINNED + GROUPED chunks


def _plain(step: StepPlan) -> PwStepPlan:
    return PwStepPlan(step, None, None, (), False, 0)


def _reusable(scan: ScanSpanResult | None, start: datetime) -> bool:
    return (
        scan is not None
        and scan.status is SearchStatus.COMPLETE
        and scan.span.start <= start < scan.span.end
    )


def _trim(chunks: Sequence[Span], limit: int, settings: PlannerSettings) -> tuple[Span, ...]:
    """The first `limit` buckets of a chunk list; the last chunk may be shorter."""
    out: list[Span] = []
    left = limit
    for chunk in chunks:
        if left <= 0:
            break
        n = chunk.buckets(settings)
        if n <= left:
            out.append(chunk)
            left -= n
        else:
            out.append(Span(chunk.start, chunk.start + left * settings.bucket))
            left = 0
    return tuple(out)


def scan_totals_for(scan: ScanSpanResult, span: Span) -> tuple[tuple[datetime, int], ...]:
    """The scan's `(bucket_start, total)` pairs inside `span`, for a step that reuses it."""
    if scan.status is not SearchStatus.COMPLETE:
        raise BootstrapStateError("a scan that was not whole is never reused")
    if span.start < scan.span.start or span.end > scan.span.end:
        raise BootstrapStateError("the span is not covered by the scan")
    return tuple(sorted((b, t) for b, t in scan.totals if span.start <= b < span.end))


def plan_pw_step(
    progress: ScopeProgress,
    *,
    now: datetime,
    boundary: datetime,
    budget: StepBudget,
    pinned_rows_per_bucket: int,
    enabled: bool,
    settings: PlannerSettings,
    scan: ScanSpanResult | None = None,
) -> PwStepPlan:
    """The next step of a watch: the core's plan plus the watch's reserved searches.

    * `SCAN`: one TOTALS search over the core's scan span (budget >= 1). The collector then
      calls the core's `interpret_scan`; on `CONTINUE` (or `RETRY`) it may plan again while
      budget remains (§H.4.2 step 1), and on `FLOOR_FOUND` it passes the scan result as
      `scan` to plan the first READ of the same tick.
    * `READ`: one TOTALS and, with pins, one PINNED search over the span, then GROUPED
      chunks. The core plans with `budget - reserved`, so the span is
      `min(remaining, 2016, (B - reserved) * W)`; with pins it is also at most
      `floor(0.8 * cap / pinned_rows_per_bucket)` buckets (§H.4.2). When `scan` is the
      COMPLETE scan that found the floor in this tick and covers the span's start, the
      scan is the span's TOTALS: no TOTALS search, the span ends at most where the scan
      does, and `scan_totals_for` yields its pairs. Never pass a scan of an earlier tick.
    * `FINISH` and `WAIT` carry the core's trigger and reason; `B <= reserved` waits.
    """
    if pinned_rows_per_bucket < 0:
        raise PlannerInputError("pinned_rows_per_bucket cannot be negative")
    pins = 1 if pinned_rows_per_bucket > 0 else 0
    start = next_start(progress, settings)
    reuse = start is not None and _reusable(scan, start)
    reserved = pins + (0 if reuse else 1)
    if start is None:
        reduced = budget  # the scan is one search; nothing is reserved before the floor
    else:
        reduced = StepBudget(max(0, budget.searches - reserved), budget.seconds_left)
    step = plan_step(
        progress, now=now, end=boundary, budget=reduced, settings=settings, enabled=enabled
    )
    if step.action is StepAction.SCAN:
        out = PwStepPlan(step, step.scan_span, None, (), False, 1)
    elif step.action is StepAction.READ:
        assert start is not None  # READ needs a known floor
        limit = sum(c.buckets(settings) for c in step.chunks)
        notes: list[str] = []
        if pins:
            cap = max(1, settings.row_target_rows // pinned_rows_per_bucket)
            if cap < limit:
                limit = cap
                notes.append(f"limited to {cap} bucket(s) by the pinned values")
        if reuse:
            assert scan is not None
            scan_room = (scan.span.end - start) // settings.bucket
            if scan_room < limit:
                limit = scan_room
                notes.append("ending where the floor scan ends")
        chunks = _trim(step.chunks, limit, settings)
        span = Span(chunks[0].start, chunks[-1].end)
        reason = (
            f"read {span.buckets(settings)} bucket(s) in {len(chunks)} search(es) of up to "
            f"{step.chunk_buckets}" + ("; " + "; ".join(notes) if notes else "")
        )
        step = replace(step, chunks=chunks, reason=reason)
        out = PwStepPlan(
            step,
            None if reuse else span,
            span if pins else None,
            chunks,
            reuse,
            (0 if reuse else 1) + pins + len(chunks),
        )
    else:
        out = _plain(step)
    assert out.searches <= budget.searches, "a step never costs more than its budget"
    return out


# --------------------------------------------------------------------------- settling


def settle_pw_step(
    progress: ScopeProgress,
    *,
    totals: SearchStatus,
    pinned: SearchStatus | None,
    grouped: Sequence[ChunkResult],
    totals_reused: bool = False,
    end: datetime,
    settings: PlannerSettings,
) -> StepSettlement:
    """Apply a step's results through the core, after the span-wide searches are weighed.

    The span's TOTALS and PINNED cover every chunk, so a chunk is whole only if they are
    (§H.6 row 3): FAILED or TRUNCATED makes every chunk FAILED, NOT_RUN makes every chunk
    NOT_RUN. The core then writes nothing, holds `through`, halves the width and counts
    the failure; there is no second counter here. A reused scan TOTALS is COMPLETE
    (`totals_reused=True`). `progress.searches` is the real count: the core adds each
    chunk's `searches`, and this adds the span's TOTALS (not a reused one, not NOT_RUN)
    and PINNED (when pins exist and it ran). A downgraded chunk keeps its own count.
    """
    wide = [totals] if pinned is None else [totals, pinned]
    downgrade: SearchStatus | None = None
    if any(s in (SearchStatus.FAILED, SearchStatus.TRUNCATED) for s in wide):
        downgrade = SearchStatus.FAILED
    elif any(s is SearchStatus.NOT_RUN for s in wide):
        downgrade = SearchStatus.NOT_RUN
    results = list(grouped)
    if downgrade is not None:
        # A GROUPED search that ran was spent even if the span's TOTALS failed.
        results = [
            ChunkResult(
                r.span, downgrade, searches=0 if r.status is SearchStatus.NOT_RUN else r.searches
            )
            for r in results
        ]
    settlement = settle_step(progress, results, end=end, settings=settings)
    ran = (0 if totals_reused or totals is SearchStatus.NOT_RUN else 1) + (
        1 if pinned is not None and pinned is not SearchStatus.NOT_RUN else 0
    )
    if not ran:
        return settlement
    counted = replace(settlement.progress, searches=settlement.progress.searches + ran)
    return replace(settlement, progress=counted)


# --------------------------------------------------------------------------- hand-off


def handoff_columns(
    progress: ScopeProgress | None,
    *,
    state: PwHistoryState,
    reason: str,
    boundary: datetime,
    now: datetime,
    settings: PlannerSettings,
) -> dict[str, Any]:
    """Columns for the step whose trigger finished the reading (§H.2.3, §H.2.4, §H.6 row 9).

    When a span was settled, both watermarks are the last bucket before the boundary and
    `history_end` is the boundary: the unread span up to it (time limit, switch, density,
    gaps) is an implicit gap. When nothing was settled, all three are NULL so the existing
    first-run seed starts the live path.
    """
    if state not in _HANDOFF_STATES:
        raise BootstrapStateError(f"{state.value} is not a finished history state")
    columns = history_columns(progress, state=state, reason=reason)
    settled = (
        progress is not None
        and progress.through is not None
        and state is not PwHistoryState.UNAVAILABLE
    )
    last = boundary - settings.bucket if settled else None
    columns.update(
        collected_through=last,
        evaluated_through=last,
        history_end=boundary if settled else None,
        history_finished_at=now,
    )
    return columns


def restart_columns(state: PwHistoryState, *, history_days: int | None) -> dict[str, Any]:
    """Columns for a version bump (§H.5.4 row 1); empty when nothing changes.

    A watch with `history_days >= 1` in a started state restarts `READING` with every
    `history_*` column cleared (`history_progress = {}`) and both watermarks NULL. The
    next tick finds no progress (`read_progress` is None) and starts it with
    `start_progress`. `NOT_REQUESTED` never started, and a watch without `history_days`
    is not reset by the patch route.
    """
    if history_days is None or history_days < 1 or state not in _RESTARTABLE:
        return {}
    new_state, _ = apply_trigger(state, HistoryTrigger.RESTART)
    columns = history_columns(None, state=new_state, reason="")
    columns.update(
        history_end=None,
        history_finished_at=None,
        collected_through=None,
        evaluated_through=None,
    )
    return columns


# --------------------------------------------------------------------------- budget


def pw_history_budget(
    *, settings: Settings, live_remaining: int, seconds_since_tick_start: float
) -> StepBudget:
    """The history sub-budget of a tick (§H.4.4): what live collection left over, capped."""
    return StepBudget(
        searches=max(0, min(settings.pw_bootstrap_searches_per_tick, live_remaining)),
        seconds_left=settings.pw_bootstrap_seconds_per_tick - seconds_since_tick_start,
    )


__all__ = [
    "INITIAL_CHUNK_BUCKETS",
    "MAX_GAPS",
    "MIN_CHUNK_BUCKETS",
    "ROW_TARGET_SHARE",
    "UNREADABLE_PROGRESS",
    "WAITING_FOR_BUDGET",
    "BootstrapStateError",
    "PwBegin",
    "PwStepPlan",
    "apply_trigger",
    "begin_reading",
    "from_history_state",
    "handoff_columns",
    "history_columns",
    "plan_pw_step",
    "pw_history_budget",
    "pw_planner_settings",
    "read_progress",
    "restart_columns",
    "scan_totals_for",
    "settle_pw_step",
    "start_progress",
    "to_history_state",
]
