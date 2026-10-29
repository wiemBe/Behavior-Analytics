"""Measured rule matches: evidence per rule type, the fold, window coverage.

Everything here is pure. The central assertions are the fail-closed ones: an
unknown type is NOT_MEASURED, a full or partly-dropped page is TRUNCATED, and a
span is COMPLETE only when every window in it is.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.rule import RuleMatchWindowStatus
from app.providers.dto import RuleMatchBatch, RuleMatchRow
from app.rules.matches import (
    SKIPPED_REASON,
    CoverageState,
    MatchEvidence,
    assess_coverage,
    failed_window,
    failed_window_facts,
    floor_to_window,
    fold_rule_matches,
    match_evidence,
)

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
HOUR = 3600


@pytest.mark.parametrize(
    ("rule_type", "expected"),
    [
        ("EVENT", MatchEvidence.EXACT),
        ("event", MatchEvidence.EXACT),
        (" Event ", MatchEvidence.EXACT),
        ("COMMON", MatchEvidence.LOWER_BOUND),
        ("FLOW", MatchEvidence.NOT_MEASURED),
        ("OFFENSE", MatchEvidence.NOT_MEASURED),
        ("USER", MatchEvidence.NOT_MEASURED),
        ("BUILDINGBLOCK", MatchEvidence.NOT_MEASURED),
        ("SOMETHING_NEW", MatchEvidence.NOT_MEASURED),
        ("", MatchEvidence.NOT_MEASURED),
        (None, MatchEvidence.NOT_MEASURED),
    ],
)
def test_match_evidence_per_rule_type(rule_type, expected) -> None:
    assert match_evidence(rule_type) == expected


def batch(*rows: tuple[int, int, int], truncated: bool = False, malformed: int = 0,
          cap: int = 100) -> RuleMatchBatch:
    return RuleMatchBatch(
        window_start=START,
        window_end=START + timedelta(seconds=HOUR),
        rows=[RuleMatchRow(qradar_rule_id=i, records=r, events=e) for i, r, e in rows],
        truncated=truncated,
        row_cap=cap,
        malformed_rows=malformed,
        query_version="rule_match/v1",
    )


def fold(b: RuleMatchBatch, ids: dict[int, uuid.UUID]):
    return fold_rule_matches(b, ids, window_start=START, window_seconds=HOUR)


class TestFold:
    def test_rules_and_building_blocks_are_joined_by_id(self) -> None:
        rule, bb = uuid.uuid4(), uuid.uuid4()
        result = fold(batch((100, 3, 9), (200, 1, 1)), {100: rule, 200: bb})

        assert {r.rule_id: (r.match_records, r.matched_events) for r in result.rows} == {
            rule: (3, 9),
            bb: (1, 1),
        }
        assert result.window.status == RuleMatchWindowStatus.COMPLETE
        assert result.window.unknown_ids == 0
        assert result.window.rows_returned == 2

    def test_unknown_ids_are_counted_and_not_stored(self) -> None:
        rule = uuid.uuid4()
        result = fold(batch((100, 3, 9), (999, 5, 5), (998, 1, 1)), {100: rule})

        assert [r.rule_id for r in result.rows] == [rule]
        assert result.window.unknown_ids == 2
        assert result.window.rows_returned == 3

    def test_a_repeated_id_is_summed_not_duplicated(self) -> None:
        rule = uuid.uuid4()
        result = fold(batch((100, 3, 9), (100, 2, 4)), {100: rule})

        (row,) = result.rows
        assert (row.match_records, row.matched_events) == (5, 13)

    def test_the_cap_makes_the_window_truncated(self) -> None:
        rule = uuid.uuid4()
        result = fold(batch((100, 3, 9), truncated=True, cap=1), {100: rule})

        assert result.window.status == RuleMatchWindowStatus.TRUNCATED
        # The rows that did arrive are still positive evidence.
        assert len(result.rows) == 1
        assert result.window.row_cap == 1

    def test_a_dropped_malformed_row_also_makes_it_truncated(self) -> None:
        """A dropped row might have been any rule, so absence proves nothing."""
        result = fold(batch((100, 3, 9), malformed=1), {100: uuid.uuid4()})

        assert result.window.status == RuleMatchWindowStatus.TRUNCATED

    def test_an_empty_complete_batch_writes_no_rows_but_is_complete(self) -> None:
        result = fold(batch(), {})

        assert result.rows == []
        assert result.window.status == RuleMatchWindowStatus.COMPLETE

    def test_failed_window_has_no_rows_and_names_no_counts(self) -> None:
        window = failed_window(
            window_start=START, window_seconds=HOUR, row_cap=50, query_version="v"
        )

        assert window.status == RuleMatchWindowStatus.FAILED
        assert (window.rows_returned, window.unknown_ids) == (0, 0)


def windows(count: int, *, first: datetime = START, status: str = "COMPLETE",
            seconds: int = HOUR) -> list[tuple[datetime, int, str]]:
    return [(first + timedelta(seconds=seconds * i), seconds, status) for i in range(count)]


def coverage(rows, *, ever: bool = True, settle: int = 0):
    # Span: 10:00 .. 14:00, so four windows.
    return assess_coverage(
        rows,
        evaluation_start=START,
        now=START + timedelta(hours=4),
        window_seconds=HOUR,
        settle_seconds=settle,
        ever_collected=ever,
    )


class TestCoverage:
    def test_never_collected_is_none(self) -> None:
        assert coverage([], ever=False).state == CoverageState.NONE

    def test_every_window_complete_is_complete(self) -> None:
        result = coverage(windows(4))

        assert result.state == CoverageState.COMPLETE
        assert (result.expected, result.complete) == (4, 4)

    def test_one_truncated_window_makes_the_span_incomplete(self) -> None:
        rows = windows(4)
        rows[2] = (rows[2][0], HOUR, "TRUNCATED")

        result = coverage(rows)

        assert result.state == CoverageState.INCOMPLETE
        assert result.truncated == 1

    def test_one_failed_window_makes_the_span_incomplete(self) -> None:
        rows = windows(4)
        rows[1] = (rows[1][0], HOUR, "FAILED")

        result = coverage(rows)

        assert result.state == CoverageState.INCOMPLETE
        assert result.failed == 1

    def test_a_missing_window_makes_the_span_incomplete(self) -> None:
        result = coverage(windows(3))

        assert result.state == CoverageState.INCOMPLETE
        assert result.missing == 1

    def test_an_unknown_status_fails_closed(self) -> None:
        rows = windows(4)
        rows[0] = (rows[0][0], HOUR, "SOMETHING")

        assert coverage(rows).state == CoverageState.INCOMPLETE

    def test_a_window_on_another_width_does_not_count(self) -> None:
        rows = windows(4)
        rows[0] = (rows[0][0], 1800, "COMPLETE")

        result = coverage(rows)

        assert result.state == CoverageState.INCOMPLETE
        assert result.missing == 1

    def test_the_settle_lag_excludes_the_newest_window(self) -> None:
        """The newest window ended inside the lag, so it is not yet expected."""
        result = coverage(windows(3), settle=1800)

        assert result.expected == 3
        assert result.state == CoverageState.COMPLETE

    def test_an_empty_span_is_not_complete(self) -> None:
        result = assess_coverage(
            [],
            evaluation_start=START,
            now=START + timedelta(minutes=10),
            window_seconds=HOUR,
            settle_seconds=0,
            ever_collected=True,
        )

        assert result.expected == 0
        assert result.state == CoverageState.INCOMPLETE

    def test_a_partial_first_window_is_not_expected(self) -> None:
        """A window starting before the span begins is outside the span."""
        result = assess_coverage(
            windows(4),
            evaluation_start=START - timedelta(minutes=30),
            now=START + timedelta(hours=4),
            window_seconds=HOUR,
            settle_seconds=0,
            ever_collected=True,
        )

        assert result.expected == 4
        assert result.state == CoverageState.COMPLETE


def test_floor_to_window_aligns_to_the_utc_grid() -> None:
    assert floor_to_window(datetime(2026, 10, 12, 10, 59, 59, tzinfo=UTC), HOUR) == START


def failed_rows(
    count: int, *, first: datetime = START, error: str | None = "ProviderError: boom"
) -> list[tuple[datetime, int, str, str | None]]:
    return [
        (first + timedelta(seconds=HOUR * i), HOUR, "FAILED", error)
        for i in range(count)
    ]


class TestFailedWindowFacts:
    SPAN = timedelta(days=30)

    def test_no_failed_window_is_none(self) -> None:
        rows = [
            (START, HOUR, "COMPLETE", None),
            (START + timedelta(hours=1), HOUR, "TRUNCATED", None),
        ]
        assert failed_window_facts(rows, window_seconds=HOUR, span=self.SPAN) is None

    def test_failed_windows_are_counted_with_their_span(self) -> None:
        facts = failed_window_facts(
            failed_rows(2), window_seconds=HOUR, span=self.SPAN
        )

        assert facts is not None
        assert (facts.failed, facts.skipped) == (2, 0)
        assert facts.first_start == START
        assert facts.last_start == START + timedelta(hours=1)
        assert facts.last_error_class == "ProviderError"

    def test_the_until_date_is_the_span_past_the_last_window(self) -> None:
        facts = failed_window_facts(
            failed_rows(2), window_seconds=HOUR, span=self.SPAN
        )

        assert facts is not None
        assert facts.until == START + timedelta(hours=1) + self.SPAN

    def test_a_single_window_is_not_its_own_range(self) -> None:
        facts = failed_window_facts(
            failed_rows(1), window_seconds=HOUR, span=self.SPAN
        )

        assert facts is not None
        assert facts.first_start == facts.last_start

    def test_horizon_skipped_windows_are_counted_apart(self) -> None:
        rows = [
            (START, HOUR, "FAILED", SKIPPED_REASON),
            (START + timedelta(hours=1), HOUR, "FAILED", SKIPPED_REASON),
            (START + timedelta(hours=2), HOUR, "FAILED", "ProviderError: boom"),
        ]

        facts = failed_window_facts(rows, window_seconds=HOUR, span=self.SPAN)

        assert facts is not None
        assert (facts.failed, facts.skipped) == (1, 2)
        assert facts.last_error_class == "ProviderError"
        assert facts.first_start == START
        assert facts.last_start == START + timedelta(hours=2)

    def test_the_error_class_is_the_newest_failed_window_and_never_the_message(
        self,
    ) -> None:
        rows = [
            (START, HOUR, "FAILED", "ProviderError: older secret-ish text"),
            (START + timedelta(hours=1), HOUR, "FAILED", "ArielPermitUnavailable: newer"),
        ]

        facts = failed_window_facts(rows, window_seconds=HOUR, span=self.SPAN)

        assert facts is not None
        assert facts.last_error_class == "ArielPermitUnavailable"
        assert "newer" not in str(facts)

    def test_a_failed_window_without_an_error_has_no_class(self) -> None:
        facts = failed_window_facts(
            [(START, HOUR, "FAILED", None)], window_seconds=HOUR, span=self.SPAN
        )

        assert facts is not None
        assert facts.last_error_class is None

    def test_the_input_order_does_not_decide_the_span(self) -> None:
        rows = [
            (START + timedelta(hours=2), HOUR, "FAILED", "ProviderError: boom"),
            (START, HOUR, "FAILED", "ProviderError: boom"),
        ]

        facts = failed_window_facts(rows, window_seconds=HOUR, span=self.SPAN)

        assert facts is not None
        assert facts.first_start == START
        assert facts.last_start == START + timedelta(hours=2)

    def test_an_obsolete_window_width_is_not_named_as_a_failed_current_window(self) -> None:
        facts = failed_window_facts(
            [(START, HOUR // 2, "FAILED", "ProviderError: boom")],
            window_seconds=HOUR,
            span=self.SPAN,
        )

        assert facts is None

    def test_a_malformed_error_never_leaks_as_an_error_class(self) -> None:
        facts = failed_window_facts(
            [(START, HOUR, "FAILED", "secret-ish appliance message")],
            window_seconds=HOUR,
            span=self.SPAN,
        )

        assert facts is not None
        assert facts.last_error_class is None
