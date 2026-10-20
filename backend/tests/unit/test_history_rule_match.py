"""The rule-match history half: the scan, the fold and the scope row (HB-5).

Pure unit tests of `app.history.rule_match`: no database, no provider. The
integration side (the collector's step) lives in
`tests/integration/test_history_rule_match.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.history.lifecycle import SCOPE_COLUMNS, ScopeRowView
from app.history.planner import ChunkResult, ScanSpanResult, SearchStatus, Span
from app.history.rule_match import (
    RULE_MATCH_SCOPE_KEY,
    failed_chunk,
    failed_scan,
    fitted_window,
    fold_chunk,
    planner_settings,
    scan_result,
    scope_base,
    searches_for_days,
    step_scope_row,
    windows_of,
)
from app.history.state import HistoryFamily, HistoryState
from app.models.rule import RuleMatchWindowStatus
from app.providers.base import ConsoleEventTotals, ProviderError
from app.providers.dto import RuleMatchBatch, RuleMatchRow, WindowedRuleMatches

T0 = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
WEEK = timedelta(hours=168)


def app_settings(**overrides):
    """Pinned settings for the pure module; nothing is read from the environment."""
    values = {
        "history_backfill_days": 28,
        "history_backfill_floor_margin_hours": 24,
        "history_backfill_max_hours": 72,
        "history_backfill_max_failures": 3,
        "history_backfill_searches_per_tick": 10,
        "rule_match_max_rows": 5000,
        "rule_health_window_days": 30,
        **overrides,
    }
    return get_settings().model_copy(update=values)


def planner(**overrides):
    return planner_settings(app_settings(**overrides), window_seconds=3600, cadence_seconds=300)


class TestPlannerSettings:
    def test_the_family_numbers(self) -> None:
        settings = planner()

        assert settings.bucket_seconds == 3600
        assert settings.max_span_buckets == 168  # a week per search
        assert settings.initial_chunk_buckets == 168
        assert settings.min_chunk_buckets == 1
        assert settings.row_cap == 5000
        assert settings.floor_margin_hours == 24
        assert settings.max_failures == 3
        assert settings.searches_per_tick == 10
        assert settings.cadence_seconds == 300

    def test_the_density_gate_never_ends_a_rule_match_reading(self) -> None:
        """§B.12 row 12: the gate does not apply; a COMMON rule stays a lower bound."""
        assert planner().max_lower_bound_share == 1.0

    def test_a_non_positive_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="window width must be positive"):
            planner_settings(app_settings(), window_seconds=0, cadence_seconds=300)

    def test_the_scan_width_tracks_the_window_width(self) -> None:
        settings = planner_settings(app_settings(), window_seconds=1800, cadence_seconds=300)

        assert settings.max_span_buckets == 336


class TestCosts:
    def test_searches_for_days_counts_the_console_scan(self) -> None:
        settings = planner()

        # 28 days + the 24 h margin = 696 hourly buckets; 5 reads of a week + 1 scan.
        assert searches_for_days(28, settings) == 6
        # 7 days + margin = 192 buckets; 2 reads + 1 scan.
        assert searches_for_days(7, settings) == 3

    def test_the_full_window_fits_the_time_budget(self) -> None:
        anchor = T0 + timedelta(days=40)

        fit = fitted_window(28, anchor=anchor, settings=planner())

        assert fit.refused is False
        assert fit.days_fitted == 28
        assert fit.target_start == anchor - timedelta(days=28)

    def test_an_estimate_over_the_time_limit_refuses_the_window(self) -> None:
        anchor = T0 + timedelta(days=40)
        settings = planner_settings(
            app_settings(history_backfill_max_hours=1, history_backfill_searches_per_tick=1),
            window_seconds=3600,
            cadence_seconds=3600,
        )

        fit = fitted_window(7, anchor=anchor, settings=settings)

        assert fit.refused is True
        assert fit.days_fitted is None
        assert fit.target_start is None


class TestWindows:
    def test_windows_of_a_span_are_oldest_first_and_whole(self) -> None:
        assert windows_of(Span(T0, T0 + 3 * HOUR), planner()) == (
            T0,
            T0 + HOUR,
            T0 + 2 * HOUR,
        )


def console_totals(
    *pairs: tuple[datetime, int],
    start: datetime = T0,
    end: datetime = T0 + WEEK,
    bucket_seconds: int = 3600,
) -> ConsoleEventTotals:
    return ConsoleEventTotals(
        window_start=start,
        window_end=end,
        bucket_seconds=bucket_seconds,
        totals=dict(pairs),
        query_version="console_event_totals/v1",
    )


class TestScanResult:
    def test_a_whole_scan_is_complete_with_its_totals_sorted(self) -> None:
        span = Span(T0, T0 + WEEK)

        result = scan_result(
            span,
            console_totals((T0 + 2 * HOUR, 7), (T0, 5)),
            planner(),
        )

        assert result.status is SearchStatus.COMPLETE
        assert result.totals == ((T0, 5), (T0 + 2 * HOUR, 7))

    def test_an_empty_answer_is_complete_silence_not_an_error(self) -> None:
        result = scan_result(Span(T0, T0 + WEEK), console_totals(), planner())

        assert result.status is SearchStatus.COMPLETE
        assert result.totals == ()

    def test_a_wrong_bucket_width_fails_closed(self) -> None:
        span = Span(T0, T0 + WEEK)

        with pytest.raises(ProviderError, match="bucket width"):
            scan_result(span, console_totals(bucket_seconds=1800), planner())

    def test_a_span_that_is_not_the_one_asked_for_fails_closed(self) -> None:
        span = Span(T0, T0 + WEEK)

        with pytest.raises(ProviderError, match="span is not the one asked for"):
            scan_result(span, console_totals(start=T0 + HOUR), planner())

    def test_a_naive_bucket_fails_closed(self) -> None:
        span = Span(T0, T0 + WEEK)
        naive = datetime(2026, 10, 12, 1, 0)  # deliberately naive: the point of the test

        with pytest.raises(ProviderError, match="timezone-aware"):
            scan_result(span, console_totals((naive, 5)), planner())

    def test_an_unaligned_bucket_fails_closed(self) -> None:
        span = Span(T0, T0 + WEEK)

        with pytest.raises(ProviderError, match="not aligned"):
            scan_result(span, console_totals((T0 + timedelta(minutes=30), 5)), planner())

    def test_a_bucket_outside_the_span_fails_closed(self) -> None:
        span = Span(T0, T0 + WEEK)

        with pytest.raises(ProviderError, match="outside the scanned span"):
            scan_result(span, console_totals((T0 - HOUR, 5)), planner())

    @pytest.mark.parametrize("total", [-1, 1.5, "5", True])
    def test_a_total_that_is_not_a_non_negative_integer_fails_closed(self, total) -> None:
        span = Span(T0, T0 + WEEK)

        with pytest.raises(ProviderError, match="non-negative integer"):
            scan_result(span, console_totals((T0, total)), planner())

    def test_a_failed_scan_proves_nothing(self) -> None:
        span = Span(T0, T0 + HOUR)

        assert failed_scan(span) == ScanSpanResult(span=span, status=SearchStatus.FAILED)


def rule_batch(
    start: datetime,
    rows: dict[int, tuple[int, int]],
    *,
    truncated: bool = False,
    malformed: int = 0,
    row_cap: int = 100,
) -> RuleMatchBatch:
    return RuleMatchBatch(
        window_start=start,
        window_end=start + HOUR,
        rows=[
            RuleMatchRow(qradar_rule_id=qid, records=rec, events=ev)
            for qid, (rec, ev) in rows.items()
        ],
        truncated=truncated,
        row_cap=row_cap,
        malformed_rows=malformed,
        query_version="rule_match/v2",
        query="windowed query",
    )


def windowed(
    span: Span,
    batches: dict[datetime, RuleMatchBatch],
    *,
    truncated: bool = False,
    row_cap: int = 100,
) -> WindowedRuleMatches:
    return WindowedRuleMatches(
        window_start=span.start,
        window_end=span.end,
        window_seconds=3600,
        batches=batches,
        truncated=truncated,
        row_cap=row_cap,
        query="windowed query",
    )


def fold(span: Span, batches: dict[datetime, RuleMatchBatch], rule_id: uuid.UUID, **kwargs):
    return fold_chunk(
        windowed(span, batches, **kwargs),
        {100: rule_id},
        span=span,
        settings=planner(),
        duration_ms=7,
        collected_at=T0,
    )


SPAN = Span(T0, T0 + 3 * HOUR)


class TestFoldChunk:
    def test_a_whole_search_folds_every_window_with_the_live_fold(self) -> None:
        rule = uuid.uuid4()
        batches = {
            T0: rule_batch(T0, {100: (3, 9)}),
            T0 + HOUR: rule_batch(T0 + HOUR, {}),
            T0 + 2 * HOUR: rule_batch(T0 + 2 * HOUR, {100: (1, 2)}),
        }

        chunk = fold(SPAN, batches, rule)

        assert chunk.result.status is SearchStatus.COMPLETE
        assert set(chunk.folds) == {T0, T0 + HOUR, T0 + 2 * HOUR}
        assert chunk.result.rows == 2
        assert chunk.result.non_silent_buckets == 2
        assert chunk.result.lower_bound_buckets == 0
        assert chunk.result.searches == 1
        assert chunk.duration_ms == 7
        assert chunk.collected_at == T0
        # A COMMON rule is not dropped: its absence stays a lower bound to the reader.
        assert chunk.folds[T0].rows[0].rule_id == rule

    def test_a_search_that_skipped_a_window_proves_nothing(self) -> None:
        rule = uuid.uuid4()
        batches = {
            T0: rule_batch(T0, {100: (1, 1)}),
            T0 + 2 * HOUR: rule_batch(T0 + 2 * HOUR, {100: (1, 1)}),
        }

        with pytest.raises(ProviderError, match="did not cover every window"):
            fold(SPAN, batches, rule)

    def test_a_truncated_search_is_truncated(self) -> None:
        rule = uuid.uuid4()

        chunk = fold(
            SPAN,
            {start: rule_batch(start, {100: (1, 1)}) for start in windows_of(SPAN, planner())},
            rule,
            truncated=True,
        )

        assert chunk.result.status is SearchStatus.TRUNCATED

    def test_a_malformed_row_makes_the_search_truncated(self) -> None:
        rule = uuid.uuid4()
        batches = {
            T0: rule_batch(T0, {100: (1, 1)}, malformed=1),
            T0 + HOUR: rule_batch(T0 + HOUR, {}),
            T0 + 2 * HOUR: rule_batch(T0 + 2 * HOUR, {}),
        }

        chunk = fold(SPAN, batches, rule)

        assert chunk.result.status is SearchStatus.TRUNCATED

    def test_one_window_hitting_its_cap_makes_the_search_truncated(self) -> None:
        """A whole search is only whole if every window in it is."""
        rule = uuid.uuid4()
        batches = {
            T0: rule_batch(T0, {100: (1, 1)}),
            T0 + HOUR: rule_batch(T0 + HOUR, {100: (1, 1)}, truncated=True),
            T0 + 2 * HOUR: rule_batch(T0 + 2 * HOUR, {}),
        }

        chunk = fold(SPAN, batches, rule)

        assert chunk.result.status is SearchStatus.TRUNCATED
        assert chunk.folds[T0 + HOUR].window.status is RuleMatchWindowStatus.TRUNCATED

    def test_a_failed_chunk_spends_one_search(self) -> None:
        span = Span(T0, T0 + 2 * HOUR)

        assert failed_chunk(span) == ChunkResult(
            span=span, status=SearchStatus.FAILED, searches=1
        )


class TestRows:
    def test_window_rows_are_history_marked_and_never_before_the_floor(self) -> None:
        rule = uuid.uuid4()
        batches = {
            T0: rule_batch(T0, {100: (3, 9)}),
            T0 + HOUR: rule_batch(T0 + HOUR, {100: (1, 2)}),
            T0 + 2 * HOUR: rule_batch(T0 + 2 * HOUR, {100: (0, 0)}),
        }
        instance_id = uuid.uuid4()

        rows = fold(SPAN, batches, rule).window_rows(instance_id, floor=T0 + HOUR)

        assert [row["window_start"] for row in rows] == [T0 + HOUR, T0 + 2 * HOUR]
        first = rows[0]
        assert first["origin"] == "HISTORY"
        assert first["instance_id"] == instance_id
        assert first["status"] is RuleMatchWindowStatus.COMPLETE
        assert first["query_version"] == "rule_match/v2"
        assert first["attempts"] == 1
        assert first["last_error"] is None

    def test_metric_rows_are_the_sparse_rows_after_the_floor(self) -> None:
        rule = uuid.uuid4()
        batches = {
            T0: rule_batch(T0, {100: (3, 9)}),
            T0 + HOUR: rule_batch(T0 + HOUR, {}),
            T0 + 2 * HOUR: rule_batch(T0 + 2 * HOUR, {100: (1, 2)}),
        }

        rows = fold(SPAN, batches, rule).metric_rows(floor=T0 + HOUR)

        assert len(rows) == 1
        assert rows[0] == {
            "rule_id": rule,
            "bucket_start": T0 + 2 * HOUR,
            "bucket_seconds": 3600,
            "match_records": 1,
            "matched_events": 2,
        }


def scope_view(**overrides) -> ScopeRowView:
    return ScopeRowView(state=HistoryState.READING, **overrides)


class TestScopeRow:
    def test_scope_base_holds_every_column(self) -> None:
        instance_id = uuid.uuid4()

        base = scope_base(
            instance_id=instance_id,
            family=HistoryFamily.RULE_MATCH,
            scope_key=RULE_MATCH_SCOPE_KEY,
            row=scope_view(reason="reading"),
        )

        assert set(base) == set(SCOPE_COLUMNS)
        assert base["instance_id"] == instance_id
        assert base["family"] == "RULE_MATCH"
        assert base["scope_key"] == ""
        assert base["state"] == "READING"
        assert base["progress"] == {}

    def test_step_scope_row_overlays_the_planner_fields(self) -> None:
        base = scope_base(
            instance_id=uuid.uuid4(),
            family=HistoryFamily.RULE_MATCH,
            scope_key=RULE_MATCH_SCOPE_KEY,
            row=scope_view(),
        )

        row = step_scope_row(
            base,
            state=HistoryState.READING,
            days_target=28,
            target_start=T0,
            floor=T0,
            through=T0 + HOUR,
            started_at=T0,
            finished_at=None,
            reason="read 1 bucket(s)",
            progress={"version": 1},
        )

        assert set(row) == set(SCOPE_COLUMNS)
        assert row["state"] == "READING"
        assert row["days_target"] == 28
        assert row["floor"] == T0
        assert row["through"] == T0 + HOUR
        assert row["progress"] == {"version": 1}
        assert row["finished_at"] is None

    def test_step_scope_row_bounds_a_long_reason(self) -> None:
        base = scope_base(
            instance_id=uuid.uuid4(),
            family=HistoryFamily.RULE_MATCH,
            scope_key=RULE_MATCH_SCOPE_KEY,
            row=scope_view(),
        )

        row = step_scope_row(
            base,
            state=HistoryState.COMPLETE,
            days_target=28,
            target_start=T0,
            floor=T0,
            through=T0 + HOUR,
            started_at=T0,
            finished_at=T0 + HOUR,
            reason="x" * 400,
            progress={},
        )

        assert len(row["reason"]) == 255
        assert row["reason"].endswith("…")

    def test_step_scope_row_refuses_an_incomplete_base(self) -> None:
        base = scope_base(
            instance_id=uuid.uuid4(),
            family=HistoryFamily.RULE_MATCH,
            scope_key=RULE_MATCH_SCOPE_KEY,
            row=scope_view(),
        )
        del base["floor"]

        with pytest.raises(ValueError, match="scope base lacks"):
            step_scope_row(
                base,
                state=HistoryState.READING,
                days_target=None,
                target_start=None,
                floor=None,
                through=None,
                started_at=None,
                finished_at=None,
                reason="",
                progress={},
            )
