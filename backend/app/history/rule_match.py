"""The rule-match family's pure history half (HISTORY-BACKFILL §B.3.2, §B.4.5, §B.6.2).

The adapter reads whole hourly windows from Ariel and writes them beside the live
windows. This module decides what those reads mean, with no database, no provider and
no clock:

* the planner settings of the family (one-hour buckets, a week per search, the
  rule-match row cap);
* a scanned console total as the core's `ScanSpanResult` (the floor proof);
* one keyed search's batches as a `ChunkResult` plus the folds to persist, using the
  **live** fold so a COMMON rule keeps its lower-bound meaning;
* the `rule_match_window` and `rule_match_metric` mappings of a span, never a row
  before the floor `F` (§B.3.2 rule 1).

Only aggregates leave this module: the same per-rule counts the live collector stores.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.history.origin import HistoryFamily, HistoryOrigin, marker_values
from app.history.planner import (
    ChunkResult,
    PlannerSettings,
    ScanSpanResult,
    SearchStatus,
    Span,
    WindowFit,
    fit_window,
    searches_for,
    window_buckets,
)
from app.models.rule import RuleMatchWindowStatus
from app.providers.base import ConsoleEventTotals, ProviderError
from app.providers.dto import WindowedRuleMatches
from app.rules.matches import MatchFold, fold_rule_matches, window_starts

#: The console-wide families keep one scope row and its key is empty (§B.7.1).
RULE_MATCH_SCOPE_KEY = ""
#: One search never covers more than a week (`validate_bucketed_metric_window`).
MAX_SPAN_HOURS = 168


def planner_settings(
    settings: Any, *, window_seconds: int, cadence_seconds: int
) -> PlannerSettings:
    """The core's numbers for this family, read from the application settings.

    The density gate is disabled (`max_lower_bound_share = 1.0`): §B.12 row 12 says
    it does not apply to rule matches. A window that is TRUNCATED at one bucket is
    still stored, as TRUNCATED, and a COMMON rule's count stays a lower bound through
    the reader, never by ending the whole reading.
    """
    if window_seconds < 1:
        raise ValueError("the rule-match window width must be positive")
    max_span_buckets = MAX_SPAN_HOURS * 3600 // window_seconds
    return PlannerSettings(
        bucket_seconds=window_seconds,
        floor_margin_hours=int(settings.history_backfill_floor_margin_hours),
        max_span_hours=MAX_SPAN_HOURS,
        row_cap=int(settings.rule_match_max_rows),
        initial_chunk_buckets=max_span_buckets,
        min_chunk_buckets=1,
        max_failures=int(settings.history_backfill_max_failures),
        max_lower_bound_share=1.0,
        max_hours=int(settings.history_backfill_max_hours),
        searches_per_tick=int(settings.history_backfill_searches_per_tick),
        cadence_seconds=int(cadence_seconds),
    )


def searches_for_days(days: int, settings: PlannerSettings) -> int:
    """Searches a `days` window needs, before any density is observed (§B.6.5).

    One search per week of windows, plus the one console-total scan that proves the
    floor. A rule-match window is 744 hourly buckets for 30 days, so this is small.
    """
    return searches_for(window_buckets(days, settings), 0.0, settings) + 1


def fitted_window(
    days: int, *, anchor: datetime, settings: PlannerSettings
) -> WindowFit:
    """The largest window not exceeding `days` whose estimate fits the time budget."""
    return fit_window(
        days,
        lambda candidate: searches_for_days(candidate, settings),
        end=anchor,
        settings=settings,
        searches_per_tick=settings.searches_per_tick,
    )


# --------------------------------------------------------------------------- windows


def windows_of(span: Span, settings: PlannerSettings) -> tuple[datetime, ...]:
    """Every whole window of `span`, oldest first (aligned exactly as the search)."""
    return tuple(window_starts(span.start, span.end, settings.bucket_seconds))


def failed_scan(span: Span) -> ScanSpanResult:
    """A scan span that proved nothing; the planner retries or gives up."""
    return ScanSpanResult(span=span, status=SearchStatus.FAILED)


def scan_result(
    span: Span, totals: ConsoleEventTotals, settings: PlannerSettings
) -> ScanSpanResult:
    """One COMPLETE console-total scan, or a ProviderError for a malformed answer.

    Every bucket the provider did return must lie inside the span, be aligned, and be
    non-negative. A bucket it omitted held no event and is not an error: that is the
    silence the scan reads. A duplicate key or a row outside the span means the
    appliance did not answer the question asked, so the whole span fails closed.
    """
    if totals.bucket_seconds != settings.bucket_seconds:
        raise ProviderError("the console total bucket width is not the family's window")
    if totals.window_start != span.start or totals.window_end != span.end:
        raise ProviderError("the console total span is not the one asked for")
    points: list[tuple[datetime, int]] = []
    for bucket, total in totals.totals.items():
        if bucket.tzinfo is None or bucket.utcoffset() is None:
            raise ProviderError("a console total bucket is not timezone-aware")
        if int(bucket.timestamp()) % settings.bucket_seconds:
            raise ProviderError("a console total bucket is not aligned")
        if not span.start <= bucket < span.end:
            raise ProviderError("a console total bucket is outside the scanned span")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise ProviderError("a console total is not a non-negative integer")
        points.append((bucket, total))
    points.sort()
    return ScanSpanResult(span=span, status=SearchStatus.COMPLETE, totals=tuple(points))


# --------------------------------------------------------------------------- chunks


@dataclass(frozen=True)
class ChunkFold:
    """One keyed search's planner result and the folds worth persisting."""

    result: ChunkResult
    folds: dict[datetime, MatchFold]
    duration_ms: int | None
    collected_at: datetime

    def window_rows(
        self, instance_id: uuid.UUID, *, floor: datetime
    ) -> list[dict[str, Any]]:
        """The `rule_match_window` mappings, oldest first, never before `floor`."""
        return [
            _window_row(
                instance_id,
                fold,
                duration_ms=self.duration_ms,
                collected_at=self.collected_at,
            )
            for start, fold in sorted(self.folds.items())
            if start >= floor
        ]

    def metric_rows(self, *, floor: datetime) -> list[dict[str, Any]]:
        """The sparse `rule_match_metric` mappings, never before `floor`."""
        rows: list[dict[str, Any]] = []
        for start, fold in sorted(self.folds.items()):
            if start < floor:
                continue
            rows.extend(_metric_rows(fold))
        return rows


def fold_chunk(
    batch: WindowedRuleMatches,
    rule_ids: Mapping[int, uuid.UUID],
    *,
    span: Span,
    settings: PlannerSettings,
    duration_ms: int | None,
    collected_at: datetime,
) -> ChunkFold:
    """Fold one keyed search with the live fold; decide whether it proved the span.

    A search is COMPLETE only when it answered every window, nothing was truncated
    and no row was malformed. A window whose own batch reached the cap is TRUNCATED
    even when the search as a whole was not cut: the caller must not keep a partial
    multi-window prefix. At one window the planner keeps the TRUNCATED rows as a
    lower bound (`settle_step`), exactly as the live collector does.
    """
    expected = windows_of(span, settings)
    if set(batch.batches) != set(expected):
        raise ProviderError("windowed rule match search did not cover every window")
    folds = {
        start: fold_rule_matches(
            batch.batches[start],
            rule_ids,
            window_start=start,
            window_seconds=settings.bucket_seconds,
        )
        for start in expected
    }
    truncated = (
        batch.truncated
        or any(batch.batches[start].malformed_rows for start in expected)
        or any(fold.window.status is RuleMatchWindowStatus.TRUNCATED for fold in folds.values())
    )
    rows = sum(len(fold.rows) for fold in folds.values())
    non_silent = sum(1 for fold in folds.values() if fold.rows)
    return ChunkFold(
        result=ChunkResult(
            span=span,
            status=SearchStatus.TRUNCATED if truncated else SearchStatus.COMPLETE,
            rows=rows,
            non_silent_buckets=non_silent,
            lower_bound_buckets=0,
            searches=1,
        ),
        folds=folds,
        duration_ms=duration_ms,
        collected_at=collected_at,
    )


def failed_chunk(span: Span) -> ChunkResult:
    """A keyed search that failed: nothing written, one search spent."""
    return ChunkResult(span=span, status=SearchStatus.FAILED, searches=1)


def _window_row(
    instance_id: uuid.UUID,
    fold: MatchFold,
    *,
    duration_ms: int | None,
    collected_at: datetime,
) -> dict[str, Any]:
    window = fold.window
    return {
        "instance_id": instance_id,
        "window_start": window.window_start,
        "window_seconds": window.window_seconds,
        **marker_values(HistoryFamily.RULE_MATCH, HistoryOrigin.HISTORY),
        "status": window.status,
        "rows_returned": window.rows_returned,
        "row_cap": window.row_cap,
        "unknown_ids": window.unknown_ids,
        "duration_ms": duration_ms,
        "query_version": window.query_version,
        "collected_at": collected_at,
        # A history window is one search, never a retry of the live path.
        "attempts": 1,
        "last_error": None,
    }


def _metric_rows(fold: MatchFold) -> list[dict[str, Any]]:
    window_start = fold.window.window_start
    return [
        {
            "rule_id": row.rule_id,
            "bucket_start": window_start,
            "bucket_seconds": fold.window.window_seconds,
            "match_records": row.match_records,
            "matched_events": row.matched_events,
        }
        for row in fold.rows
    ]
