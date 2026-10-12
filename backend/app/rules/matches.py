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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.models.rule import RuleMatchWindowStatus
from app.providers.dto import RuleMatchBatch


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


def floor_to_window(value: datetime, window_seconds: int) -> datetime:
    """Floor a datetime to the UTC window grid, so every worker agrees on it."""
    epoch = int(value.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)


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
