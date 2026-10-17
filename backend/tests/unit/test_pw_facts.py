"""The fact contract PW-2 and PW-3 build on: measured zeros, counts, remainders, readings."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from app.models.enums import PwBucketMeasurement, PwPinnedMeasurement, WafMeasurement
from app.property_watch.facts import (
    TUPLE_SEPARATOR,
    PwBucketFact,
    PwCount,
    PwCountState,
    PwFactError,
    PwReadingKind,
    PwRemainderBound,
    PwSearchStatus,
    PwValueFact,
    absence_is_provable,
    coerce_count,
    count_from_search,
    is_truncated,
    is_unparsed,
    join_tuple_key,
    reading_for,
    split_tuple_key,
)

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
CAP = 5
DONE = PwSearchStatus.COMPLETED


def bucket(**over: object) -> PwBucketFact:
    base: dict[str, object] = {
        "bucket_start": T0,
        "bucket_seconds": 300,
        "watch_version": 1,
        "totals_complete": True,
        "values_measurement": PwBucketMeasurement.EXACT,
        "pinned_measurement": PwPinnedMeasurement.NOT_REQUESTED,
        "total_events": 100,
        "parsed_events": 90,
        "untracked_events": 0,
    }
    base.update(over)
    return PwBucketFact(**base)  # type: ignore[arg-type]


class TestMeasuredZero:
    def test_a_completed_whole_search_that_omits_the_key_is_an_exact_zero(self) -> None:
        count = count_from_search({}, "DENY", status=DONE, row_count=0, cap=CAP)
        assert count == PwCount(0, PwCountState.EXACT)
        assert count.measured_zero

    def test_exactly_cap_rows_is_whole_so_the_absent_key_is_still_zero(self) -> None:
        count = count_from_search({"A": 1}, "DENY", status=DONE, row_count=CAP, cap=CAP)
        assert count.measured_zero

    def test_cap_plus_one_rows_is_truncated_so_the_absent_key_is_unmeasured(self) -> None:
        count = count_from_search({"A": 1}, "DENY", status=DONE, row_count=CAP + 1, cap=CAP)
        assert count.state is PwCountState.UNMEASURED
        assert count.events is None
        assert not count.measured_zero

    @pytest.mark.parametrize(
        "status",
        [PwSearchStatus.FAILED, PwSearchStatus.CANCELLED, PwSearchStatus.TIMED_OUT,
         PwSearchStatus.RUNNING],
    )
    def test_any_other_end_state_is_never_zero(self, status: PwSearchStatus) -> None:
        count = count_from_search({}, "DENY", status=status, row_count=0, cap=CAP)
        assert count.state is PwCountState.UNMEASURED
        assert count.events is None
        # Even a key that "is there" is not trusted from a search that did not complete.
        count_with_row = count_from_search(
            {"DENY": 9}, "DENY", status=status, row_count=1, cap=CAP
        )
        assert count_with_row.events is None

    def test_a_present_key_is_exact_when_whole_and_a_lower_bound_when_truncated(self) -> None:
        whole = count_from_search({"DENY": 7.0}, "DENY", status=DONE, row_count=3, cap=CAP)
        assert whole == PwCount(7, PwCountState.EXACT)
        cut = count_from_search({"DENY": 7}, "DENY", status=DONE, row_count=CAP + 1, cap=CAP)
        assert cut == PwCount(7, PwCountState.LOWER_BOUND)

    @pytest.mark.parametrize("raw", [7.5, -1, math.nan, "7", None, True])
    def test_a_bad_present_count_refuses_the_whole_search_fact(self, raw: object) -> None:
        with pytest.raises(PwFactError):
            count_from_search({"DENY": raw}, "DENY", status=DONE, row_count=1, cap=CAP)

    def test_the_cap_boundary_helper(self) -> None:
        assert not is_truncated(CAP, CAP)
        assert is_truncated(CAP + 1, CAP)
        assert not is_truncated(0, CAP)
        with pytest.raises(PwFactError):
            is_truncated(1, 0)
        with pytest.raises(PwFactError):
            is_truncated(-1, CAP)

    def test_a_count_object_cannot_carry_a_number_while_unmeasured(self) -> None:
        with pytest.raises(PwFactError):
            PwCount(0, PwCountState.UNMEASURED)
        with pytest.raises(PwFactError):
            PwCount(None, PwCountState.EXACT)


class TestCounts:
    @pytest.mark.parametrize(("raw", "expected"), [(12, 12), (12.0, 12), (0, 0), (0.0, 0)])
    def test_integers_and_integer_valued_floats_are_accepted(
        self, raw: float, expected: int
    ) -> None:
        result = coerce_count(raw)
        assert result == expected
        assert isinstance(result, int)

    @pytest.mark.parametrize(
        "raw",
        [12.5, -1, -1.0, math.nan, math.inf, -math.inf, "12", None, True, False, [1], 1e400],
    )
    def test_anything_else_is_refused(self, raw: object) -> None:
        with pytest.raises(PwFactError):
            coerce_count(raw)


class TestUnparsed:
    @pytest.mark.parametrize("raw", [None, "", "   ", "\t", 7, b"x"])
    def test_null_and_empty_are_both_unparsed(self, raw: object) -> None:
        assert is_unparsed(raw)

    @pytest.mark.parametrize("raw", ["alice", "0", "DENY", " x "])
    def test_text_is_a_value(self, raw: str) -> None:
        assert not is_unparsed(raw)


class TestTupleKey:
    def test_round_trip(self) -> None:
        key = join_tuple_key(("alice", "DENY"))
        assert key == f"alice{TUPLE_SEPARATOR}DENY"
        assert split_tuple_key(key) == ("alice", "DENY")

    def test_a_component_with_the_separator_or_no_component_is_refused(self) -> None:
        with pytest.raises(PwFactError):
            join_tuple_key(("a\x1fb", "c"))
        with pytest.raises(PwFactError):
            join_tuple_key(())


class TestBucketFact:
    def test_the_remainder_is_total_minus_parsed_and_exact_for_a_whole_search(self) -> None:
        fact = bucket()
        assert fact.unparsed_events == 10
        assert fact.unparsed_bound is PwRemainderBound.EXACT

    def test_a_lower_bound_bucket_can_only_over_state_the_remainder(self) -> None:
        fact = bucket(values_measurement=PwBucketMeasurement.LOWER_BOUND, untracked_events=None)
        assert fact.unparsed_events == 10
        assert fact.unparsed_bound is PwRemainderBound.UPPER_BOUND
        assert fact.coverage.lower_bound

    def test_the_remainder_is_unknown_not_zero_without_totals(self) -> None:
        fact = bucket(totals_complete=False, total_events=None)
        assert fact.unparsed_events is None
        assert fact.unparsed_bound is None
        assert fact.coverage.ratio is None
        assert not fact.coverage.reaches(0.0)

    def test_incomplete_totals_cannot_carry_a_total(self) -> None:
        with pytest.raises(PwFactError):
            bucket(totals_complete=False)

    def test_parsed_cannot_exceed_total(self) -> None:
        with pytest.raises(PwFactError):
            bucket(parsed_events=101)

    def test_an_uncollected_bucket_measured_nothing(self) -> None:
        fact = bucket(
            values_measurement=PwBucketMeasurement.NOT_COLLECTED,
            parsed_events=None,
            untracked_events=None,
        )
        assert fact.unparsed_events is None
        with pytest.raises(PwFactError):
            bucket(values_measurement=PwBucketMeasurement.NOT_COLLECTED)
        with pytest.raises(PwFactError):
            bucket(parsed_events=None)  # a collected bucket must state parsed events

    def test_untracked_events_are_known_only_from_a_whole_search(self) -> None:
        with pytest.raises(PwFactError):
            bucket(values_measurement=PwBucketMeasurement.LOWER_BOUND, untracked_events=3)

    def test_kept_values_cannot_exceed_the_parsed_events(self) -> None:
        with pytest.raises(PwFactError):
            bucket(values=(PwValueFact("A", 95),))
        assert bucket(values=(PwValueFact("A", 60), PwValueFact("B", 30)), untracked_events=0)

    def test_not_applicable_needs_events_that_parsed_nowhere(self) -> None:
        assert bucket(parsed_events=0, untracked_events=0).property_not_parsed
        # A silent scope is not a parsing break.
        silent = bucket(total_events=0, parsed_events=0, untracked_events=0)
        assert not silent.property_not_parsed
        assert silent.coverage.ratio is None
        assert not bucket().property_not_parsed

    def test_coverage_ratio(self) -> None:
        assert bucket().coverage.ratio == pytest.approx(0.9)
        assert bucket().coverage.reaches(0.9)
        assert not bucket(parsed_events=80, untracked_events=0).coverage.reaches(0.9)


class TestReading:
    def test_a_metric_row_is_a_count_under_the_bucket_measurement(self) -> None:
        reading = reading_for(bucket(), pinned=False, tracked_at_bucket=True, metric_events=7)
        assert (reading.kind, reading.events, reading.measurement) == (
            PwReadingKind.COUNT, 7, WafMeasurement.EXACT,
        )
        lb = bucket(values_measurement=PwBucketMeasurement.LOWER_BOUND, untracked_events=None)
        assert reading_for(lb, pinned=False, tracked_at_bucket=True, metric_events=7).measurement \
            is WafMeasurement.LOWER_BOUND

    def test_a_pinned_row_is_exact_when_the_pinned_search_was_whole(self) -> None:
        lb = bucket(
            values_measurement=PwBucketMeasurement.LOWER_BOUND,
            untracked_events=None,
            pinned_measurement=PwPinnedMeasurement.EXACT,
        )
        assert reading_for(lb, pinned=True, tracked_at_bucket=True, metric_events=4).measurement \
            is WafMeasurement.EXACT
        assert reading_for(lb, pinned=False, tracked_at_bucket=True, metric_events=4).measurement \
            is WafMeasurement.LOWER_BOUND

    def test_no_row_in_an_exact_bucket_is_a_measured_zero(self) -> None:
        reading = reading_for(bucket(), pinned=False, tracked_at_bucket=True, metric_events=None)
        assert reading.kind is PwReadingKind.MEASURED_ZERO
        assert reading.events == 0
        assert reading.is_measured_zero

    def test_no_row_in_a_lower_bound_bucket_is_unknown_never_zero(self) -> None:
        lb = bucket(values_measurement=PwBucketMeasurement.LOWER_BOUND, untracked_events=None)
        reading = reading_for(lb, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert reading.kind is PwReadingKind.UNKNOWN
        assert reading.events is None
        assert not reading.is_measured_zero

    def test_a_pinned_value_has_a_proven_zero_under_a_cut_open_search(self) -> None:
        lb = bucket(
            values_measurement=PwBucketMeasurement.LOWER_BOUND,
            untracked_events=None,
            pinned_measurement=PwPinnedMeasurement.EXACT,
        )
        pinned = reading_for(lb, pinned=True, tracked_at_bucket=True, metric_events=None)
        assert pinned.is_measured_zero
        # The same bucket proves nothing for an automatic value.
        automatic = reading_for(lb, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert not automatic.is_measured_zero

    def test_a_pin_whose_search_did_not_run_does_not_get_a_zero(self) -> None:
        for state in (PwPinnedMeasurement.NOT_COLLECTED, PwPinnedMeasurement.LOWER_BOUND):
            cut = bucket(
                values_measurement=PwBucketMeasurement.LOWER_BOUND,
                untracked_events=None,
                pinned_measurement=state,
            )
            reading = reading_for(cut, pinned=True, tracked_at_bucket=True, metric_events=None)
            assert reading.kind is PwReadingKind.UNKNOWN

    def test_an_untracked_value_is_not_an_observation(self) -> None:
        reading = reading_for(bucket(), pinned=False, tracked_at_bucket=False, metric_events=None)
        assert reading.kind is PwReadingKind.NOT_AN_OBSERVATION
        assert reading.events is None

    def test_no_bucket_row_is_nothing_never_zero(self) -> None:
        for pinned in (True, False):
            reading = reading_for(None, pinned=pinned, tracked_at_bucket=True, metric_events=None)
            assert reading.kind is PwReadingKind.NO_BUCKET
            assert reading.events is None
            assert reading.measurement is None

    def test_an_uncollected_bucket_is_nothing_never_zero(self) -> None:
        dead = bucket(
            values_measurement=PwBucketMeasurement.NOT_COLLECTED,
            parsed_events=None,
            untracked_events=None,
        )
        reading = reading_for(dead, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert reading.kind is PwReadingKind.NO_BUCKET
        assert reading.events is None

    def test_a_row_in_an_uncollected_bucket_fails_closed(self) -> None:
        dead = bucket(
            values_measurement=PwBucketMeasurement.NOT_COLLECTED,
            parsed_events=None,
            untracked_events=None,
        )
        reading = reading_for(dead, pinned=False, tracked_at_bucket=True, metric_events=5)
        assert reading.kind is PwReadingKind.UNKNOWN
        assert reading.events is None

    def test_negative_metric_events_are_refused(self) -> None:
        with pytest.raises(PwFactError):
            reading_for(bucket(), pinned=False, tracked_at_bucket=True, metric_events=-1)

    def test_absence_also_needs_coverage_at_the_floor(self) -> None:
        zero = reading_for(bucket(), pinned=False, tracked_at_bucket=True, metric_events=None)
        assert absence_is_provable(zero, bucket(), 0.9)
        thin = bucket(parsed_events=50, untracked_events=0)
        assert not absence_is_provable(zero, thin, 0.9)
        counted = reading_for(bucket(), pinned=False, tracked_at_bucket=True, metric_events=3)
        assert not absence_is_provable(counted, bucket(), 0.9)
