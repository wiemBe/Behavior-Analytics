"""The shared pure planner of the history backfill (HISTORY-BACKFILL §B.3, §B.6, §B.8).

Every family's history step is the same function of `(progress, boundary, budget,
settings)`. The caller runs the searches and the fold; this module decides:

* the floor scan: where the first event of a scope is, hence `F = max(T0, b0 + m)`, or
  `UNAVAILABLE` (`plan_scan`, `interpret_scan`);
* the next step: the chunks to search from `through`, bounded by 168 h and by the row
  target (`plan_step`);
* what a step's results allow: `through` moves only past the contiguous prefix of whole
  searches; a failure halves the width and, at the minimum width, becomes a gap after
  `max_failures`; the density gate; completion against the moving end (`settle_step`);
* the window fitted to the time budget (`fit_window`) and the cost model of §B.8
  (`searches_for`, `ticks_for`).

Every decision carries its reason in words. Fail closed (repository rule 8): a search that
failed, was truncated or was not run proves nothing and moves nothing; no row is planned
before the floor. No database, no provider, no clock, no settings import: callers pass
numbers and `datetime` values in.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.history.state import HistoryTrigger

# One search never covers more than a week (`validate_bucketed_metric_window`).
HARD_MAX_SPAN_HOURS = 168
# §B.6.5: below a week there is no weekday x hour cell with a visit.
DEFAULT_MIN_WINDOW_DAYS = 7
# §B.6.2 step 6: rows per bucket are inflated by a quarter before sizing a chunk.
DENSITY_SAFETY = 1.25


class PlannerInputError(ValueError):
    """The caller passed something that is not a valid input (a bug, not a data fault)."""


@dataclass(frozen=True)
class PlannerSettings:
    """The numbers a family passes in (names follow the `history_backfill_*` settings)."""

    bucket_seconds: int = 300
    floor_margin_hours: int = 24
    max_span_hours: int = HARD_MAX_SPAN_HOURS
    row_cap: int = 5000
    row_target_share: float = 0.8
    initial_chunk_buckets: int = 288
    min_chunk_buckets: int = 1
    max_failures: int = 3
    max_lower_bound_share: float = 0.05
    max_hours: int = 72
    max_gaps: int = 20
    searches_per_tick: int = 10
    cadence_seconds: int = 300

    def __post_init__(self) -> None:
        if self.bucket_seconds < 1 or 86400 % self.bucket_seconds:
            raise PlannerInputError("bucket_seconds must be a positive divisor of one day")
        if not 1 <= self.floor_margin_hours <= 168:
            raise PlannerInputError("floor_margin_hours must be 1..168")
        if not 1 <= self.max_span_hours <= HARD_MAX_SPAN_HOURS:
            raise PlannerInputError("max_span_hours must be 1..168")
        if self.row_cap < 1 or not 0 < self.row_target_share <= 1:
            raise PlannerInputError("row_cap must be positive and row_target_share in (0, 1]")
        if self.min_chunk_buckets < 1 or self.initial_chunk_buckets < self.min_chunk_buckets:
            raise PlannerInputError("chunk widths must satisfy 1 <= minimum <= initial")
        if self.max_failures < 1 or self.max_gaps < 1 or self.searches_per_tick < 1:
            raise PlannerInputError("max_failures, max_gaps and searches_per_tick must be >= 1")
        if not 0 <= self.max_lower_bound_share <= 1:
            raise PlannerInputError("max_lower_bound_share must be in [0, 1]")
        if self.max_hours < 1 or self.cadence_seconds < 1:
            raise PlannerInputError("max_hours and cadence_seconds must be >= 1")

    @property
    def bucket(self) -> timedelta:
        return timedelta(seconds=self.bucket_seconds)

    @property
    def margin(self) -> timedelta:
        """The margin, rounded up to whole buckets."""
        buckets = math.ceil(self.floor_margin_hours * 3600 / self.bucket_seconds)
        return buckets * self.bucket

    @property
    def max_span_buckets(self) -> int:
        return self.max_span_hours * 3600 // self.bucket_seconds

    @property
    def row_target_rows(self) -> int:
        """Rows one search may return: 80 % of the family's cap."""
        return max(1, math.floor(self.row_cap * self.row_target_share + 1e-9))


# --------------------------------------------------------------------------- time helpers


def _check_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PlannerInputError(f"{name} must be timezone-aware")


def _epoch(value: datetime) -> int:
    _check_aware(value, "time")
    if value.microsecond:
        raise PlannerInputError("times must be whole seconds")
    return int(value.timestamp())


def _is_aligned(value: datetime, settings: PlannerSettings) -> bool:
    return _epoch(value) % settings.bucket_seconds == 0


def _require_aligned(value: datetime, name: str, settings: PlannerSettings) -> None:
    if not _is_aligned(value, settings):
        raise PlannerInputError(f"{name} must be aligned to the {settings.bucket_seconds} s bucket")


def _align_up(value: datetime, settings: PlannerSettings) -> datetime:
    epoch = _epoch(value)
    step = settings.bucket_seconds
    return datetime.fromtimestamp(-(-epoch // step) * step, tz=UTC)


def buckets_between(start: datetime, end: datetime, settings: PlannerSettings) -> int:
    """Whole buckets in `[start, end)`; both must be aligned."""
    _require_aligned(start, "start", settings)
    _require_aligned(end, "end", settings)
    return max(0, (_epoch(end) - _epoch(start)) // settings.bucket_seconds)


# --------------------------------------------------------------------------- shared types


@dataclass(frozen=True)
class Span:
    """A half-open interval `[start, end)` of whole buckets."""

    start: datetime
    end: datetime

    def buckets(self, settings: PlannerSettings) -> int:
        return buckets_between(self.start, self.end, settings)


@dataclass(frozen=True)
class Gap:
    """A recorded span that could not be read (`history_progress.gaps`; words, no values)."""

    start: datetime
    end: datetime
    reason: str


@dataclass(frozen=True)
class ScopeProgress:
    """What the planner carries between ticks (the scope row's `progress`, §B.7.1)."""

    target_start: datetime  # T0, fixed at the first step
    started_at: datetime
    floor: datetime | None = None  # F, once proved
    first_event_bucket: datetime | None = None  # b0
    through: datetime | None = None  # start of the last history bucket written
    scan_cursor: datetime | None = None  # start of the next floor-scan span
    scan_failures: int = 0
    chunk_buckets: int | None = None  # W, the width of one search
    chunk_ceiling: int | None = None  # W may not grow past a width that failed
    failures: int = 0  # consecutive failures at the minimum width
    searches: int = 0
    buckets_read: int = 0
    lower_bound_buckets: int = 0
    rows_seen: int = 0
    non_silent_buckets: int = 0
    density_checked: bool = False
    gaps: tuple[Gap, ...] = field(default_factory=tuple)

    @property
    def rows_per_bucket(self) -> float | None:
        """Mean rows of a non-silent bucket seen so far, or None before any."""
        if self.non_silent_buckets <= 0:
            return None
        return self.rows_seen / self.non_silent_buckets


def new_progress(
    target_start: datetime, started_at: datetime, settings: PlannerSettings
) -> ScopeProgress:
    """The progress of a scope that has not searched yet."""
    _require_aligned(target_start, "target_start", settings)
    _check_aware(started_at, "started_at")
    return ScopeProgress(
        target_start=target_start,
        started_at=started_at,
        scan_cursor=target_start - settings.margin,
    )


def next_start(progress: ScopeProgress, settings: PlannerSettings) -> datetime | None:
    """First bucket still to read, or None while the floor is unknown."""
    if progress.floor is None:
        return None
    if progress.through is None:
        return progress.floor
    return progress.through + settings.bucket


# --------------------------------------------------------------------------- floor scan


class ScanOutcome(StrEnum):
    FLOOR_FOUND = "FLOOR_FOUND"
    CONTINUE = "CONTINUE"  # no event yet; scan the next span
    RETRY = "RETRY"  # a span proved nothing; scan it again
    UNAVAILABLE = "UNAVAILABLE"


class SearchStatus(StrEnum):
    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class ScanSpanResult:
    """The TOTALS result of one scan span: `(bucket_start, total_events)` per bucket."""

    span: Span
    status: SearchStatus
    totals: tuple[tuple[datetime, int], ...] = ()


@dataclass(frozen=True)
class ScanPlan:
    span: Span | None
    reason: str


@dataclass(frozen=True)
class ScanDecision:
    outcome: ScanOutcome
    progress: ScopeProgress
    reason: str
    trigger: HistoryTrigger | None = None


def plan_scan(progress: ScopeProgress, end: datetime, settings: PlannerSettings) -> ScanPlan:
    """The next floor-scan span: from the cursor, at most 168 h, never past `end`."""
    _require_aligned(end, "end", settings)
    cursor = progress.scan_cursor
    if cursor is None:
        raise PlannerInputError("progress has no scan cursor")
    if cursor >= end:
        return ScanPlan(None, "the scan reached the end of the window")
    stop = min(end, cursor + settings.max_span_buckets * settings.bucket)
    return ScanPlan(Span(cursor, stop), "scan for the first event of the scope")


def interpret_scan(
    progress: ScopeProgress,
    results: Sequence[ScanSpanResult],
    end: datetime,
    settings: PlannerSettings,
) -> ScanDecision:
    """Find `b0` and the floor `F = max(T0, b0 + margin)`, or say why none yet.

    Results come in time order from the cursor. Only a COMPLETE span is evidence: a
    failed or truncated one stops the scan there, even if a later span has events,
    because the earlier bucket may hold the true first event.
    """
    _require_aligned(end, "end", settings)
    cursor = progress.scan_cursor
    if progress.floor is not None or cursor is None:
        raise PlannerInputError("the floor is already known; there is nothing to scan")
    if not results:
        return ScanDecision(ScanOutcome.RETRY, progress, "no scan result was provided")
    # Every scan search that ran is a search spent, whatever it proved.
    progress = replace(
        progress,
        searches=progress.searches + sum(r.status is not SearchStatus.NOT_RUN for r in results),
    )
    for result in results:
        if result.span.start != cursor:
            raise PlannerInputError("scan results must be contiguous from the scan cursor")
        if result.status is not SearchStatus.COMPLETE:
            failures = progress.scan_failures + 1
            if failures >= settings.max_failures:
                return ScanDecision(
                    ScanOutcome.UNAVAILABLE,
                    replace(progress, scan_failures=failures),
                    f"the floor scan failed {failures} times; history is unavailable",
                    HistoryTrigger.NOTHING_TO_READ,
                )
            return ScanDecision(
                ScanOutcome.RETRY,
                replace(progress, scan_failures=failures),
                f"a scan search was {result.status.lower()}; it proves nothing, so it is retried "
                f"({failures} of {settings.max_failures})",
            )
        for bucket, total in result.totals:
            _require_aligned(bucket, "bucket", settings)
            if not result.span.start <= bucket < result.span.end:
                raise PlannerInputError("a scan bucket lies outside its span")
            if total < 0:
                raise PlannerInputError("a bucket total cannot be negative")
        events = sorted(b for b, total in result.totals if total > 0)
        if events:
            b0 = events[0]
            floor = max(progress.target_start, _align_up(b0 + settings.margin, settings))
            if floor >= end:
                return ScanDecision(
                    ScanOutcome.UNAVAILABLE,
                    replace(progress, first_event_bucket=b0, scan_failures=0),
                    "the first event is too recent to prove anything older: the margin of "
                    f"{settings.floor_margin_hours} h reaches the end of the window",
                    HistoryTrigger.NOTHING_TO_READ,
                )
            cut = floor > progress.target_start + settings.bucket
            return ScanDecision(
                ScanOutcome.FLOOR_FOUND,
                replace(
                    progress,
                    first_event_bucket=b0,
                    floor=floor,
                    scan_cursor=result.span.end,
                    scan_failures=0,
                ),
                (
                    f"QRadar holds events of the scope from {b0.isoformat()}; history starts at "
                    f"{floor.isoformat()}"
                    + (" (later than the asked start)" if cut else "")
                ),
            )
        cursor = result.span.end
    progress = replace(progress, scan_cursor=cursor, scan_failures=0)
    if cursor >= end:
        return ScanDecision(
            ScanOutcome.UNAVAILABLE,
            progress,
            "no event of the scope in the whole window; nothing is written",
            HistoryTrigger.NOTHING_TO_READ,
        )
    return ScanDecision(
        ScanOutcome.CONTINUE, progress, "no event in the scanned span; scan the next one"
    )


def floor_is_cut(progress: ScopeProgress, settings: PlannerSettings) -> bool:
    """True when Ariel holds less than asked (`F > T0 + one bucket`, §B.3.4)."""
    return progress.floor is not None and progress.floor > progress.target_start + settings.bucket


# --------------------------------------------------------------------------- step planning


def row_fit_buckets(rows_per_bucket: float, settings: PlannerSettings) -> int:
    """Widest chunk whose rows stay inside the row target (§B.6.2 step 6)."""
    if rows_per_bucket <= 0:
        return settings.max_span_buckets
    fit = math.floor(settings.row_target_rows / (DENSITY_SAFETY * rows_per_bucket))
    return max(settings.min_chunk_buckets, min(settings.max_span_buckets, fit))


@dataclass(frozen=True)
class StepBudget:
    """What the tick has left for history: searches and seconds (live goes first)."""

    searches: int
    seconds_left: float

    def __post_init__(self) -> None:
        if self.searches < 0:
            raise PlannerInputError("the search budget cannot be negative")


class StepAction(StrEnum):
    SCAN = "SCAN"
    READ = "READ"
    WAIT = "WAIT"
    FINISH = "FINISH"


@dataclass(frozen=True)
class StepPlan:
    action: StepAction
    reason: str
    chunks: tuple[Span, ...] = ()
    scan_span: Span | None = None
    chunk_buckets: int | None = None
    trigger: HistoryTrigger | None = None  # set when the step ends the reading

    @property
    def span(self) -> Span | None:
        if not self.chunks:
            return None
        return Span(self.chunks[0].start, self.chunks[-1].end)


def chunk_width(progress: ScopeProgress, settings: PlannerSettings) -> int:
    """W for the next step: the stored width, the density fit and the failure ceiling."""
    width = progress.chunk_buckets or settings.initial_chunk_buckets
    rho = progress.rows_per_bucket
    if rho is not None:
        width = min(width, row_fit_buckets(rho, settings))
    if progress.chunk_ceiling is not None:
        width = min(width, progress.chunk_ceiling)
    return max(settings.min_chunk_buckets, min(width, settings.max_span_buckets))


def finish_verdict(
    progress: ScopeProgress, settings: PlannerSettings
) -> tuple[HistoryTrigger, str]:
    """COMPLETE or PARTIAL at the end of a reading that reached its boundary (§B.3.4)."""
    problems: list[str] = []
    if floor_is_cut(progress, settings):
        problems.append("QRadar holds less than asked (the floor is later than the asked start)")
    if progress.gaps:
        problems.append(f"{len(progress.gaps)} span(s) could not be read")
    if problems:
        return HistoryTrigger.FINISHED_PARTIAL, "reached the end; " + "; ".join(problems)
    return HistoryTrigger.FINISHED_COMPLETE, "reached the end with the whole asked window read"


def plan_step(
    progress: ScopeProgress,
    *,
    now: datetime,
    end: datetime,
    budget: StepBudget,
    settings: PlannerSettings,
    enabled: bool = True,
    stop_requested: bool = False,
) -> StepPlan:
    """The next step. `end` is the anchor, or the moving closed-bucket boundary."""
    _require_aligned(end, "end", settings)
    _check_aware(now, "now")
    if not enabled:
        return StepPlan(
            StepAction.FINISH,
            "history reading was switched off",
            trigger=HistoryTrigger.SWITCHED_OFF,
        )
    if stop_requested:
        return StepPlan(
            StepAction.FINISH,
            "history reading was stopped by an operator",
            trigger=HistoryTrigger.STOPPED_BY_OPERATOR,
        )
    start = next_start(progress, settings)
    if start is not None and start >= end:
        trigger, reason = finish_verdict(progress, settings)
        return StepPlan(StepAction.FINISH, reason, trigger=trigger)
    if now - progress.started_at > timedelta(hours=settings.max_hours):
        return StepPlan(
            StepAction.FINISH,
            f"the reading did not finish in {settings.max_hours} h",
            trigger=HistoryTrigger.TIME_LIMIT,
        )
    if budget.searches <= 0 or budget.seconds_left <= 0:
        return StepPlan(StepAction.WAIT, "waiting for search budget")
    if start is None:
        scan = plan_scan(progress, end, settings)
        if scan.span is None:
            return StepPlan(
                StepAction.FINISH,
                "no event of the scope in the whole window; nothing is written",
                trigger=HistoryTrigger.NOTHING_TO_READ,
            )
        return StepPlan(StepAction.SCAN, scan.reason, scan_span=scan.span)
    _require_aligned(start, "start", settings)
    width = chunk_width(progress, settings)
    remaining = buckets_between(start, end, settings)
    span_buckets = min(remaining, settings.max_span_buckets, width * budget.searches)
    chunks: list[Span] = []
    cursor = start
    left = span_buckets
    while left > 0:
        n = min(width, left)
        chunks.append(Span(cursor, cursor + n * settings.bucket))
        cursor += n * settings.bucket
        left -= n
    return StepPlan(
        StepAction.READ,
        f"read {span_buckets} bucket(s) in {len(chunks)} search(es) of up to {width}",
        chunks=tuple(chunks),
        chunk_buckets=width,
    )


def split_on_truncation(
    chunk: Span, settings: PlannerSettings
) -> tuple[Span, Span] | None:
    """Halve a truncated multi-bucket search in time, left first.

    None at one bucket: the result there is a lower bound, never a split (§B.6.3).
    """
    n = chunk.buckets(settings)
    if n <= 1:
        return None
    mid = chunk.start + (n // 2) * settings.bucket
    return Span(chunk.start, mid), Span(mid, chunk.end)


# --------------------------------------------------------------------------- settling a step


@dataclass(frozen=True)
class ChunkResult:
    """What the family got for one chunk (after any splitting it did itself)."""

    span: Span
    status: SearchStatus
    rows: int = 0
    non_silent_buckets: int = 0
    lower_bound_buckets: int = 0
    # Searches the family really spent on this chunk, its own truncation splits included.
    searches: int = 1

    def __post_init__(self) -> None:
        if self.searches < 0:
            raise PlannerInputError("a chunk cannot have spent a negative number of searches")


@dataclass(frozen=True)
class StepSettlement:
    progress: ScopeProgress
    write_spans: tuple[Span, ...]  # the contiguous completed prefix: the only spans to persist
    advanced: bool
    reason: str
    trigger: HistoryTrigger | None = None  # None: the state stays as it is
    new_gap: Gap | None = None


def settle_step(
    progress: ScopeProgress,
    results: Sequence[ChunkResult],
    *,
    end: datetime,
    settings: PlannerSettings,
) -> StepSettlement:
    """Apply a step's results. `through` moves only past whole, completed chunks."""
    start = next_start(progress, settings)
    if start is None:
        raise PlannerInputError("the floor is unknown; settle a scan with interpret_scan")
    _require_aligned(end, "end", settings)
    cursor = start
    for result in results:
        if result.span.start != cursor or result.span.end <= result.span.start:
            raise PlannerInputError("chunk results must tile contiguously from the next bucket")
        cursor = result.span.end

    bucket = settings.bucket
    write: list[Span] = []
    through = progress.through
    read = lb = rows = non_silent = 0
    # Every search that ran was spent, also those of chunks after the one that blocked.
    searches = sum(r.searches for r in results if r.status is not SearchStatus.NOT_RUN)
    blocker: ChunkResult | None = None
    for result in results:
        n = result.span.buckets(settings)
        done = result.status is SearchStatus.COMPLETE or (
            result.status is SearchStatus.TRUNCATED and n == 1
        )
        if not done:
            blocker = result
            break
        write.append(result.span)
        through = result.span.end - bucket
        read += n
        rows += result.rows
        non_silent += result.non_silent_buckets
        lb += result.lower_bound_buckets if result.status is SearchStatus.COMPLETE else 1
    advanced = bool(write)

    updated = replace(
        progress,
        through=through,
        searches=progress.searches + searches,
        buckets_read=progress.buckets_read + read,
        lower_bound_buckets=progress.lower_bound_buckets + lb,
        rows_seen=progress.rows_seen + rows,
        non_silent_buckets=progress.non_silent_buckets + non_silent,
    )
    reasons: list[str] = []
    new_gap: Gap | None = None
    trigger: HistoryTrigger | None = None

    if blocker is None:
        updated = replace(updated, failures=0)
        reasons.append(f"read {read} bucket(s)")
        rho = updated.rows_per_bucket
        if rho is not None:
            fit = row_fit_buckets(rho, settings)
            if updated.chunk_ceiling is not None:
                fit = min(fit, updated.chunk_ceiling)
            updated = replace(updated, chunk_buckets=fit)
    else:
        n = blocker.span.buckets(settings)
        if blocker.status is SearchStatus.FAILED:
            narrower = max(settings.min_chunk_buckets, n // 2)
            updated = replace(updated, chunk_buckets=narrower, chunk_ceiling=narrower)
            if n <= settings.min_chunk_buckets:
                failures = updated.failures + 1
                if failures >= settings.max_failures:
                    if len(updated.gaps) >= settings.max_gaps:
                        trigger = HistoryTrigger.TOO_MANY_GAPS
                        reasons.append(f"more than {settings.max_gaps} unreadable spans")
                        updated = replace(updated, failures=failures)
                    else:
                        new_gap = Gap(
                            blocker.span.start,
                            blocker.span.end,
                            f"search failed {failures} times at the minimum width",
                        )
                        updated = replace(
                            updated,
                            failures=0,
                            gaps=(*updated.gaps, new_gap),
                            through=blocker.span.end - bucket,
                        )
                        reasons.append(f"recorded a gap: {new_gap.reason}")
                else:
                    updated = replace(updated, failures=failures)
                    reasons.append(
                        f"a search failed at the minimum width ({failures} of "
                        f"{settings.max_failures}); the same span is tried again"
                    )
            else:
                reasons.append(f"a search of {n} buckets failed; the width halves to {narrower}")
        elif blocker.status is SearchStatus.TRUNCATED:
            narrower = max(settings.min_chunk_buckets, n // 2)
            updated = replace(updated, chunk_buckets=narrower)
            reasons.append(
                f"a search of {n} buckets was truncated and keeps nothing; the width halves "
                f"to {narrower}"
            )
        else:
            reasons.append("the budget ended before this span was searched")

    if trigger is None and not updated.density_checked and updated.buckets_read > 0:
        updated = replace(updated, density_checked=True)
        share = updated.lower_bound_buckets / updated.buckets_read
        if share > settings.max_lower_bound_share:
            trigger = HistoryTrigger.DENSITY_GATE
            reasons.append(
                f"{share:.0%} of the buckets read are lower bounds (limit "
                f"{settings.max_lower_bound_share:.0%}): too many distinct values per bucket, "
                "so history would be lower bounds"
            )

    if trigger is None:
        nxt = next_start(updated, settings)
        if nxt is not None and nxt >= end:
            trigger, verdict = finish_verdict(updated, settings)
            reasons.append(verdict)
    return StepSettlement(
        progress=updated,
        write_spans=tuple(write),
        advanced=advanced,
        reason="; ".join(reasons),
        trigger=trigger,
        new_gap=new_gap,
    )


# --------------------------------------------------------------------------- cost and window


def searches_for(
    buckets: int,
    rows_per_bucket: float,
    settings: PlannerSettings,
    *,
    target_rows: int | None = None,
) -> int:
    """§B.8.2: `ceil(buckets / min(2016, floor(target_rows / rho)))`.

    `target_rows` defaults to the settings' row target (4 000 of a 5 000 cap).
    """
    if buckets <= 0:
        return 0
    rows = settings.row_target_rows if target_rows is None else target_rows
    if rows_per_bucket <= 0:
        width = settings.max_span_buckets
    else:
        width = max(1, min(settings.max_span_buckets, math.floor(rows / rows_per_bucket)))
    return math.ceil(buckets / width)


def ticks_for(searches: int, searches_per_tick: int) -> int:
    """§B.8.3: ticks at `searches_per_tick` history searches each."""
    if searches_per_tick < 1:
        raise PlannerInputError("searches_per_tick must be >= 1")
    return math.ceil(max(0, searches) / searches_per_tick)


def window_buckets(days: int, settings: PlannerSettings) -> int:
    """Buckets of a window of `days` plus the floor margin (28 d + 24 h = 8 352)."""
    return days * 86400 // settings.bucket_seconds + settings.margin // settings.bucket


@dataclass(frozen=True)
class WindowFit:
    days_requested: int
    days_fitted: int | None  # None when refused
    target_start: datetime | None
    searches: int
    ticks: int
    hours: float
    reduced: bool
    refused: bool
    reason: str


def fit_window(
    days_requested: int,
    searches_for_days: Callable[[int], int],
    *,
    end: datetime,
    settings: PlannerSettings,
    searches_per_tick: int | None = None,
    min_days: int = DEFAULT_MIN_WINDOW_DAYS,
) -> WindowFit:
    """The largest window `D' <= days_requested` whose estimate fits `max_hours` (§B.6.5).

    `searches_for_days(D)` is the family's estimate of the searches for `D` days. The fitted
    window always ends at `end`, so what is read is the newest `D'` days. Below `min_days`
    the scope is refused with the estimate, never read with a window too short to use.
    """
    _require_aligned(end, "end", settings)
    per_tick = searches_per_tick or settings.searches_per_tick
    if days_requested < min_days:
        raise PlannerInputError("the requested window is below the family's minimum")
    cadence_hours = settings.cadence_seconds / 3600

    def hours_for(searches: int) -> tuple[int, float]:
        ticks = ticks_for(searches, per_tick)
        return ticks, ticks * cadence_hours

    for days in range(days_requested, min_days - 1, -1):
        searches = searches_for_days(days)
        ticks, hours = hours_for(searches)
        if hours <= settings.max_hours:
            reduced = days < days_requested
            reason = (
                f"window reduced from {days_requested} to {days} days: the estimate exceeded "
                f"{settings.max_hours} hours"
                if reduced
                else f"the {days}-day window fits in an estimated {hours:.1f} hours"
            )
            return WindowFit(
                days_requested,
                days,
                end - timedelta(days=days),
                searches,
                ticks,
                hours,
                reduced,
                False,
                reason,
            )
    searches = searches_for_days(min_days)
    ticks, hours = hours_for(searches)
    return WindowFit(
        days_requested,
        None,
        None,
        searches,
        ticks,
        hours,
        False,
        True,
        f"even {min_days} days need about {hours:.1f} hours ({searches} searches, {ticks} ticks), "
        f"over the {settings.max_hours}-hour limit; history is unavailable",
    )
