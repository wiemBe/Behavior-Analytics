"""SOC-3a: window arithmetic, section states and ordering, without a database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.rule import RuleMatchWindowStatus
from app.schemas.related_activity import (
    RelatedMeasurement,
    RelatedPad,
    RelatedRuleOut,
    RelatedSectionState,
)
from app.services.related_activity import (
    ITEMS_LIMIT,
    MatchWindowSpan,
    anomaly_span,
    measure_rule_matches,
    offense_collection_state,
    order_anomalies,
    order_rules,
    state_with_cap,
    window_bounds,
)

T0 = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("pad", "seconds"),
    [
        (RelatedPad.THIRTY_MINUTES, 1800),
        (RelatedPad.ONE_HOUR, 3600),
        (RelatedPad.TWO_HOURS, 7200),
        (RelatedPad.SIX_HOURS, 21600),
    ],
)
def test_each_pad_adds_its_width_on_both_sides(pad: RelatedPad, seconds: int) -> None:
    start, end = window_bounds(T0, T0 + timedelta(minutes=10), pad)
    assert start == T0 - timedelta(seconds=seconds)
    assert end == T0 + timedelta(minutes=10, seconds=seconds)


def test_an_open_anomaly_ends_at_its_stored_bucket_end_not_the_clock() -> None:
    stored_end = T0 + timedelta(minutes=5)
    window_start, window_end = window_bounds(stored_end, stored_end, RelatedPad.TWO_HOURS)
    # The clock is never an input: the same stored end yields the same window.
    assert window_start == stored_end - timedelta(hours=2)
    assert window_end == stored_end + timedelta(hours=2)
    assert window_end == T0 + timedelta(hours=2, minutes=5)


def test_anomaly_span_prefers_stored_bounds_and_falls_back_to_detection() -> None:
    assert anomaly_span(
        detected_at=T0,
        anomaly_start=T0 + timedelta(minutes=1),
        anomaly_end=T0 + timedelta(minutes=9),
    ) == (T0 + timedelta(minutes=1), T0 + timedelta(minutes=9))
    assert anomaly_span(
        detected_at=T0, anomaly_start=None, anomaly_end=None
    ) == (T0, T0)
    assert anomaly_span(
        detected_at=T0, anomaly_start=T0 + timedelta(minutes=1), anomaly_end=None
    ) == (T0 + timedelta(minutes=1), T0 + timedelta(minutes=1))


def test_offense_collection_state_separates_not_collected_from_stale() -> None:
    now = T0
    assert (
        offense_collection_state(
            last_run_at=None,
            consecutive_failures=0,
            now=now,
            stale_after_seconds=900,
        )
        is RelatedSectionState.NOT_COLLECTED
    )
    assert (
        offense_collection_state(
            last_run_at=now - timedelta(seconds=60),
            consecutive_failures=1,
            now=now,
            stale_after_seconds=900,
        )
        is RelatedSectionState.STALE
    )
    assert (
        offense_collection_state(
            last_run_at=now - timedelta(seconds=901),
            consecutive_failures=0,
            now=now,
            stale_after_seconds=900,
        )
        is RelatedSectionState.STALE
    )
    assert (
        offense_collection_state(
            last_run_at=now - timedelta(seconds=900),
            consecutive_failures=0,
            now=now,
            stale_after_seconds=900,
        )
        is RelatedSectionState.OK
    )


def test_offense_collection_state_treats_a_naive_timestamp_as_utc() -> None:
    naive = T0.replace(tzinfo=None)
    assert (
        offense_collection_state(
            last_run_at=naive,
            consecutive_failures=0,
            now=T0,
            stale_after_seconds=900,
        )
        is RelatedSectionState.OK
    )


def test_state_with_cap_keeps_a_stronger_state_and_marks_truncation() -> None:
    assert (
        state_with_cap(RelatedSectionState.OK, ITEMS_LIMIT)
        is RelatedSectionState.OK
    )
    assert (
        state_with_cap(RelatedSectionState.OK, ITEMS_LIMIT + 1)
        is RelatedSectionState.TRUNCATED
    )
    assert (
        state_with_cap(RelatedSectionState.STALE, ITEMS_LIMIT + 1)
        is RelatedSectionState.STALE
    )
    assert (
        state_with_cap(RelatedSectionState.NOT_COLLECTED, 0)
        is RelatedSectionState.NOT_COLLECTED
    )


def _anomaly_row(family: str, start: datetime, anomaly_id: uuid.UUID | None = None) -> dict:
    return {
        "family": family,
        "id": anomaly_id or uuid.uuid4(),
        "start": start,
    }


def test_anomalies_order_by_start_desc_then_family_then_id() -> None:
    same = T0
    first = _anomaly_row("waf", same, uuid.UUID(int=2))
    second = _anomaly_row("ips", same, uuid.UUID(int=1))
    third = _anomaly_row("log_source", same, uuid.UUID(int=3))
    newer = _anomaly_row("property_watch", same + timedelta(minutes=1))
    rows = [third, second, newer, first]
    ordered = order_anomalies(rows)
    # ips < log_source < waf among equal starts; the newer start wins regardless.
    assert [row["id"] for row in ordered] == [
        newer["id"],
        second["id"],
        third["id"],
        first["id"],
    ]


def _rule(qradar_id: int, offenses: int, matched: int | None) -> RelatedRuleOut:
    return RelatedRuleOut(
        id=uuid.uuid4(),
        qradar_id=qradar_id,
        name=f"rule-{qradar_id}",
        type="EVENT",
        related_offenses=offenses,
        matched_events=matched,
        measurement=(
            RelatedMeasurement.NOT_COLLECTED
            if matched is None
            else RelatedMeasurement.EXACT
        ),
    )


def test_rules_order_by_offenses_then_matched_with_nulls_last_then_id() -> None:
    rows = [
        _rule(30, 2, None),
        _rule(20, 2, 10),
        _rule(10, 2, 10),
        _rule(40, 3, None),
    ]
    ordered = order_rules(rows)
    assert [rule.qradar_id for rule in ordered] == [40, 10, 20, 30]


def _window(start: datetime, seconds: int, status: RuleMatchWindowStatus) -> MatchWindowSpan:
    return MatchWindowSpan(start=start, seconds=seconds, status=status)


def test_complete_coverage_is_an_exact_count() -> None:
    windows = [
        _window(T0, 3600, RuleMatchWindowStatus.COMPLETE),
        _window(T0 + timedelta(hours=1), 3600, RuleMatchWindowStatus.COMPLETE),
    ]
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=2),
        matched_events=42,
    ) == (42, RelatedMeasurement.EXACT)


def test_an_event_rule_absent_from_a_complete_window_is_a_measured_zero() -> None:
    windows = [_window(T0, 3600, RuleMatchWindowStatus.COMPLETE)]
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
        matched_events=None,
    ) == (0, RelatedMeasurement.EXACT)


def test_a_partial_window_makes_a_count_a_lower_bound_and_absence_unmeasured() -> None:
    windows = [_window(T0, 3600, RuleMatchWindowStatus.TRUNCATED)]
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
        matched_events=7,
    ) == (7, RelatedMeasurement.LOWER_BOUND)
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
        matched_events=None,
    ) == (None, RelatedMeasurement.NOT_COLLECTED)


def test_a_failed_window_or_a_coverage_gap_is_not_a_zero() -> None:
    failed = [
        _window(T0, 3600, RuleMatchWindowStatus.FAILED),
        _window(T0 + timedelta(hours=1), 3600, RuleMatchWindowStatus.COMPLETE),
    ]
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=failed,
        window_start=T0,
        window_end=T0 + timedelta(hours=2),
        matched_events=None,
    ) == (None, RelatedMeasurement.NOT_COLLECTED)

    gap = [_window(T0, 3600, RuleMatchWindowStatus.COMPLETE)]
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=gap,
        window_start=T0,
        window_end=T0 + timedelta(hours=2),
        matched_events=5,
    ) == (5, RelatedMeasurement.LOWER_BOUND)


def test_no_measured_window_ever_yields_a_zero() -> None:
    assert measure_rule_matches(
        rule_type="EVENT",
        windows=[],
        window_start=T0,
        window_end=T0 + timedelta(hours=2),
        matched_events=None,
    ) == (None, RelatedMeasurement.NOT_COLLECTED)


def test_a_rule_type_an_event_search_cannot_measure_is_not_collected() -> None:
    windows = [_window(T0, 3600, RuleMatchWindowStatus.COMPLETE)]
    assert measure_rule_matches(
        rule_type="OFFENSE",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
        matched_events=9,
    ) == (None, RelatedMeasurement.NOT_COLLECTED)


def test_a_common_rule_with_a_count_is_only_a_lower_bound() -> None:
    windows = [_window(T0, 3600, RuleMatchWindowStatus.COMPLETE)]
    assert measure_rule_matches(
        rule_type="COMMON",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
        matched_events=9,
    ) == (9, RelatedMeasurement.LOWER_BOUND)
    assert measure_rule_matches(
        rule_type="COMMON",
        windows=windows,
        window_start=T0,
        window_end=T0 + timedelta(hours=1),
        matched_events=None,
    ) == (None, RelatedMeasurement.NOT_COLLECTED)
