"""Measured rule matches: what a window proves, and the fold into stored rows.

Pure: no I/O, no clock, no session. The collector fetches and persists; this
module decides what a fetched batch means.

An event search can only see event matches. So what a COMPLETE window proves
about a rule's *absence* depends on the rule's type (`match_evidence`), and a
detector never grants itself more reasoning than that table allows:

    EVENT (incl. EVENT building blocks)  every match is searched   -> EXACT
    COMMON                               flow matches are not      -> LOWER_BOUND
    FLOW, OFFENSE, anything else         nothing is searched       -> NOT_MEASURED
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.models.rule import RuleMatchWindowStatus
from app.providers.base import ProviderError
from app.providers.dto import RuleMatchBatch, RuleMatchRow

#: The `last_error` of a FAILED window the catch-up horizon skipped without
#: searching. Its events may already be gone, so it is never repaired
#: automatically (RM-2). Defined here, the pure module, because both the writer
#: (the collector's horizon skip) and the reader (the health reason) need it.
SKIPPED_REASON = "skipped: beyond the catch-up horizon"


class MatchEvidence(StrEnum):
    """What measured matches can say about a rule of one type."""

    #: Matches are measured in full: absence from a COMPLETE window is a
    #: measured zero.
    EXACT = "EXACT"
    #: Matches are measured only in part: a count proves firing, absence proves
    #: nothing.
    LOWER_BOUND = "LOWER_BOUND"
    #: Nothing is measured for this type.
    NOT_MEASURED = "NOT_MEASURED"


def match_evidence(rule_type: str | None) -> MatchEvidence:
    """The evidence an event search gives for a rule of `rule_type`.

    An unknown or missing type is NOT_MEASURED: guessing it EXACT would turn a
    gap in the inventory into a claim of silence.
    """
    if rule_type is None:
        return MatchEvidence.NOT_MEASURED
    kind = rule_type.strip().upper()
    if kind == "EVENT":
        return MatchEvidence.EXACT
    if kind == "COMMON":
        return MatchEvidence.LOWER_BOUND
    return MatchEvidence.NOT_MEASURED


@dataclass(frozen=True)
class MatchRowToWrite:
    rule_id: uuid.UUID
    match_records: int
    matched_events: int


@dataclass(frozen=True)
class WindowRecord:
    """The `rule_match_window` row for one attempted window, minus its keys."""

    window_start: datetime
    window_seconds: int
    status: RuleMatchWindowStatus
    rows_returned: int
    row_cap: int
    unknown_ids: int
    query_version: str


@dataclass(frozen=True)
class MatchFold:
    rows: list[MatchRowToWrite]
    window: WindowRecord


def fold_rule_matches(
    batch: RuleMatchBatch,
    rule_ids: Mapping[int, uuid.UUID],
    *,
    window_start: datetime,
    window_seconds: int,
) -> MatchFold:
    """Join a batch to the inventory by id; decide the window's status.

    Rules and building blocks share one id space, so one map covers both. An id
    in neither is counted, not stored: it has no row to hang a metric on, and a
    count of them is how an inventory that fell behind QRadar shows up.

    The window is COMPLETE only if nothing may be missing from it. A full page
    means rows beyond the cap may exist, and a malformed row that was dropped
    may have been any rule: either makes the window TRUNCATED, so the rows
    kept are still positive evidence but absence proves nothing.
    """
    totals: dict[uuid.UUID, tuple[int, int]] = {}
    unknown: set[int] = set()
    for row in batch.rows:
        rule_id = rule_ids.get(row.qradar_rule_id)
        if rule_id is None:
            unknown.add(row.qradar_rule_id)
            continue
        records, events = totals.get(rule_id, (0, 0))
        totals[rule_id] = (records + row.records, events + row.events)

    status = (
        RuleMatchWindowStatus.TRUNCATED
        if batch.truncated or batch.malformed_rows > 0
        else RuleMatchWindowStatus.COMPLETE
    )
    return MatchFold(
        rows=[MatchRowToWrite(rid, rec, ev) for rid, (rec, ev) in totals.items()],
        window=WindowRecord(
            window_start=window_start,
            window_seconds=window_seconds,
            status=status,
            rows_returned=len(batch.rows),
            row_cap=batch.row_cap,
            unknown_ids=len(unknown),
            query_version=batch.query_version,
        ),
    )


def failed_window(
    *, window_start: datetime, window_seconds: int, row_cap: int, query_version: str
) -> WindowRecord:
    """A FAILED window: attempted, nothing measured, no rows."""
    return WindowRecord(
        window_start=window_start,
        window_seconds=window_seconds,
        status=RuleMatchWindowStatus.FAILED,
        rows_returned=0,
        row_cap=row_cap,
        unknown_ids=0,
        query_version=query_version,
    )


@dataclass(frozen=True)
class FailedWindowFacts:
    """What an analyst needs to know about a span's FAILED windows, bounded.

    `failed` were searched and failed; `skipped` were never searched (the
    catch-up horizon stepped over them, and their events may be gone).
    `last_error_class` is the class name of the newest failed window's error —
    never the message, which can hold appliance text. `until` is when a span of
    `span` width stops holding the last affected window: past that moment the
    verdict heals on its own.
    """

    failed: int
    skipped: int
    first_start: datetime
    last_start: datetime
    last_error_class: str | None
    until: datetime


def failed_window_facts(
    windows: Iterable[tuple[datetime, int, str, str | None]],
    *,
    window_seconds: int,
    span: timedelta,
) -> FailedWindowFacts | None:
    """Summarize the FAILED windows among `windows` for one reason sentence.

    `windows` are (start, width, status, last_error) rows the caller already
    holds — the evaluator reads them for coverage, so this adds no query. Only
    FAILED rows of the current width count: a TRUNCATED, missing or obsolete-
    width window has no failed window to name, and its story is the coverage
    counts. Returns None when no FAILED window is in the rows, so the caller
    keeps its usual sentence for every other shape of gap.
    """
    failed = skipped = 0
    first: datetime | None = None
    last: datetime | None = None
    latest_failed: datetime | None = None
    error_class: str | None = None
    for start, seconds, status, last_error in windows:
        if seconds != window_seconds or status != RuleMatchWindowStatus.FAILED:
            continue
        start = start.astimezone(UTC)
        if last_error == SKIPPED_REASON:
            skipped += 1
        else:
            failed += 1
            if latest_failed is None or start > latest_failed:
                latest_failed = start
                # The writer stores ``"{ClassName}: {message}"``. Refuse a
                # legacy or malformed value rather than risk exposing its
                # message as though it were a class name.
                candidate, separator, _ = (last_error or "").partition(":")
                error_class = candidate if separator and candidate.isidentifier() else None
        first = start if first is None or start < first else first
        last = start if last is None or start > last else last
    if first is None or last is None:
        return None
    return FailedWindowFacts(
        failed=failed,
        skipped=skipped,
        first_start=first,
        last_start=last,
        last_error_class=error_class,
        until=last + span,
    )


def floor_to_window(value: datetime, window_seconds: int) -> datetime:
    """Floor a datetime to the UTC window grid, so every worker agrees on it."""
    epoch = int(value.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)


def window_starts(start: datetime, end: datetime, window_seconds: int) -> list[datetime]:
    """The window starts of a span that is a whole number of aligned windows.

    Aligned exactly as `floor_to_window` aligns them (epoch-UTC multiples of
    `window_seconds`), so a keyed search and the per-window path agree on every
    boundary. Anything else raises ValueError, before any request is made.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("the window span must be timezone-aware")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        raise ValueError("the window span must end after its start")
    if floor_to_window(start, window_seconds) != start or start.microsecond:
        raise ValueError("the window span does not start on the window grid")
    span = (end - start).total_seconds()
    if span % window_seconds:
        raise ValueError("the window span is not a whole number of windows")
    step = timedelta(seconds=window_seconds)
    return [start + step * i for i in range(int(span // window_seconds))]


def _window_key(value: object) -> int | None:
    """A bucket key exactly as QRadar sends it: an integer, possibly as 5.0."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def fold_windowed_rows(
    raw_rows: Sequence[dict],
    *,
    window_start: datetime,
    window_end: datetime,
    window_seconds: int,
    row_cap: int,
    parse_rows: Callable[[Sequence[dict]], tuple[list[RuleMatchRow], int]],
    query_version: str,
    query: str,
) -> dict[datetime, RuleMatchBatch]:
    """Split the rows of one keyed search into one batch per window.

    Pure. Each row carries ``k = starttime / window_ms`` (integer division), so
    ``k * window_ms`` is its window's start. Every window of the span gets a
    batch, empty if no row fell in it. Within a window, rows are read by
    `parse_rows` (the same parser the per-window query uses), so a malformed
    row is dropped and counted exactly as there.

    A row whose ``k`` is missing, not an integer, or outside the span means the
    appliance did not answer the question asked, so the whole search fails
    rather than losing the row to a window it does not belong to. This applies
    to a null rule id too: that row is excluded from the counts, not from the
    check.

    A window is marked truncated when it holds `row_cap` or more raw rows, as
    the per-window query would, so the fold is column-for-column what the
    per-window path stores.
    """
    starts = window_starts(window_start, window_end, window_seconds)
    window_ms = window_seconds * 1000
    first_key = int(window_start.timestamp() * 1000) // window_ms
    grouped: dict[int, list[dict]] = {i: [] for i in range(len(starts))}
    for row in raw_rows:
        key = _window_key(row.get("k"))
        if key is None:
            raise ProviderError("rule match row has a missing or non-integer window key")
        index = key - first_key
        if not 0 <= index < len(starts):
            raise ProviderError("rule match row has a window key outside the searched span")
        grouped[index].append(row)

    out: dict[datetime, RuleMatchBatch] = {}
    for index, start in enumerate(starts):
        rows, malformed = parse_rows(grouped[index])
        out[start] = RuleMatchBatch(
            window_start=start,
            window_end=start + timedelta(seconds=window_seconds),
            rows=rows,
            truncated=len(grouped[index]) >= row_cap,
            row_cap=row_cap,
            malformed_rows=malformed,
            query_version=query_version,
            query=query,
            ariel_searches=0,
        )
    return out


class CoverageState(StrEnum):
    #: No window was ever recorded for the instance: the collector never ran, or
    #: is switched off. Rule health then behaves exactly as it did before.
    NONE = "none"
    #: Every window the evaluation span should hold is COMPLETE.
    COMPLETE = "complete"
    #: Some window is missing, TRUNCATED or FAILED. Absence proves nothing.
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class MatchCoverage:
    state: CoverageState
    expected: int = 0
    complete: int = 0
    truncated: int = 0
    failed: int = 0
    #: Expected windows with no usable record at all (never attempted, or
    #: recorded on a different window width).
    missing: int = 0


def assess_coverage(
    windows: Iterable[tuple[datetime, int, str]],
    *,
    evaluation_start: datetime,
    now: datetime,
    window_seconds: int,
    settle_seconds: int,
    ever_collected: bool,
) -> MatchCoverage:
    """How much of the evaluation span the recorded windows cover.

    `windows` are (start, width, status) rows. The span holds every grid window
    that starts at or after `evaluation_start` and has settled by `now`. The
    span is COMPLETE only if *every* one of them is COMPLETE: a single missing,
    TRUNCATED or FAILED window leaves some rule's silence unmeasured, and the
    evaluator cannot tell which.
    """
    if not ever_collected:
        return MatchCoverage(CoverageState.NONE)
    step = timedelta(seconds=window_seconds)
    settled_until = floor_to_window(now - timedelta(seconds=settle_seconds), window_seconds)
    first = floor_to_window(evaluation_start, window_seconds)
    if first < evaluation_start.astimezone(UTC):
        first += step
    by_start = {w[0].astimezone(UTC): (w[1], w[2]) for w in windows}

    expected = complete = truncated = failed = missing = 0
    cursor = first
    while cursor + step <= settled_until:
        expected += 1
        seconds, status = by_start.get(cursor, (None, None))
        if seconds != window_seconds:
            missing += 1
        elif status == RuleMatchWindowStatus.COMPLETE:
            complete += 1
        elif status == RuleMatchWindowStatus.TRUNCATED:
            truncated += 1
        else:
            # FAILED, and any status this code does not know: fail closed.
            failed += 1
        cursor += step
    state = (
        CoverageState.COMPLETE
        if expected > 0 and complete == expected
        else CoverageState.INCOMPLETE
    )
    return MatchCoverage(state, expected, complete, truncated, failed, missing)
