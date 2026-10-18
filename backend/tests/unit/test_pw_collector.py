"""Pure parts of property-watch collection: the completed prefix, the gap fact, the rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.collectors.pw_collector import collected_prefix, not_collected_fact
from app.models.enums import PwBucketMeasurement, PwPinnedMeasurement
from app.property_watch.keyed_search import PwBucketOutcome, PwCellState
from app.providers.dto import PwSearchKind

T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)


def cells(search: PwSearchKind, *states: PwCellState) -> list[PwBucketOutcome]:
    return [
        PwBucketOutcome(search=search, bucket_start=T0 + STEP * i, state=state)
        for i, state in enumerate(states)
    ]


C, L, F, N = (
    PwCellState.COMPLETE,
    PwCellState.LOWER_BOUND,
    PwCellState.FAILED,
    PwCellState.NOT_COLLECTED,
)
G, T, P = PwSearchKind.GROUPED, PwSearchKind.TOTALS, PwSearchKind.PINNED


class TestCollectedPrefix:
    def test_every_bucket_complete(self) -> None:
        assert collected_prefix(cells(G, C, C), cells(T, C, C), None) == 2

    def test_a_lower_bound_grouped_bucket_still_completes(self) -> None:
        assert collected_prefix(cells(G, C, L, C), cells(T, C, C, C), None) == 3

    def test_the_first_failed_grouped_bucket_ends_the_prefix(self) -> None:
        assert collected_prefix(cells(G, C, F, C), cells(T, C, C, C), None) == 1

    def test_a_totals_search_that_was_not_whole_holds_the_watermark(self) -> None:
        assert collected_prefix(cells(G, C, C), cells(T, C, L), None) == 1
        assert collected_prefix(cells(G, C, C), cells(T, F, C), None) == 0
        assert collected_prefix(cells(G, C, C), cells(T, C, N), None) == 1

    def test_a_pinned_search_that_measured_nothing_holds_it_only_when_asked(self) -> None:
        assert collected_prefix(cells(G, C, C), cells(T, C, C), cells(P, C, F)) == 1
        assert collected_prefix(cells(G, C, C), cells(T, C, C), cells(P, C, L)) == 2
        assert collected_prefix(cells(G, C, C), cells(T, C, C), None) == 2

    def test_a_spent_budget_is_not_complete(self) -> None:
        assert collected_prefix(cells(G, N, N), cells(T, N, N), None) == 0

    def test_nothing_to_collect(self) -> None:
        assert collected_prefix([], [], None) == 0


def test_a_gap_fact_carries_no_number_and_is_not_a_zero() -> None:
    fact = not_collected_fact(bucket_start=T0, bucket_seconds=300, watch_version=2, pinned=True)
    assert fact.values_measurement is PwBucketMeasurement.NOT_COLLECTED
    assert fact.pinned_measurement is PwPinnedMeasurement.NOT_COLLECTED
    assert fact.total_events is None and fact.parsed_events is None
    assert fact.untracked_events is None and fact.unparsed_events is None
    assert fact.totals_complete is False and fact.values == ()
    plain = not_collected_fact(bucket_start=T0, bucket_seconds=300, watch_version=2, pinned=False)
    assert plain.pinned_measurement is PwPinnedMeasurement.NOT_REQUESTED
