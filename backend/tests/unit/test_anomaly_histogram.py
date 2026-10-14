"""The histogram's pure parts: the UTC hour axis, the range ceiling, the gap-fill.

The SQL behind `GET /anomalies/histogram` is one grouped `UNION ALL`; what
turns its rows into a chart is everything around it -- which hours the range
covers, what happens at the boundaries, and an empty hour staying present as
a measured zero. All of that is pure, so it is pinned here without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.repositories.anomaly_histogram import (
    MAX_RANGE,
    check_range,
    fill_hours,
    hour_span,
)

T0 = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)


def h(n: int) -> datetime:
    """`n` whole hours after T0."""
    return T0 + timedelta(hours=n)


class TestHourSpan:
    def test_a_mid_hour_since_floors_to_its_hour(self) -> None:
        span = hour_span(
            datetime(2026, 10, 13, 8, 37, tzinfo=UTC), datetime(2026, 10, 13, 11, 0, tzinfo=UTC)
        )
        assert span == [h(0), h(1), h(2)]

    def test_an_until_on_an_hour_boundary_does_not_open_that_hour(self) -> None:
        """The population is half-open: `until` itself is outside the range,
        so the hour starting at `until` can hold nothing."""
        assert hour_span(T0, h(3)) == [h(0), h(1), h(2)]

    def test_a_microsecond_into_the_hour_opens_it(self) -> None:
        """`until - 1 µs` falls in the next hour, so that hour is present --
        it can only hold the one instant just below `until`."""
        span = hour_span(T0, h(3) + timedelta(microseconds=1))
        assert span == [h(0), h(1), h(2), h(3)]

    def test_a_range_of_exactly_168_hours_spans_168_hours(self) -> None:
        span = hour_span(T0, h(168))
        assert len(span) == 168
        assert span[0] == T0 and span[-1] == h(167)

    def test_one_hour_past_it_spans_one_more(self) -> None:
        assert len(hour_span(T0, h(169))) == 169

    def test_a_mid_hour_span_of_a_week_covers_169_boundaries(self) -> None:
        """A 168-hour range starting mid-hour still runs through the hour of
        its last instant -- the ceiling is on the range, not the bucket count."""
        span = hour_span(T0 + timedelta(minutes=30), h(168) + timedelta(minutes=30))
        assert len(span) == 169

    def test_a_non_utc_instant_is_floored_in_utc(self) -> None:
        """13:30+05:30 is 08:00Z, so the span starts at 08:00Z, not 13:00Z."""
        zone = timezone(timedelta(hours=5, minutes=30))
        span = hour_span(datetime(2026, 10, 13, 13, 30, tzinfo=zone), h(2))
        assert span == [h(0), h(1)]

    def test_an_empty_range_has_no_hours(self) -> None:
        assert hour_span(T0, T0) == []


class TestRangeCeiling:
    def test_exactly_168_hours_is_allowed(self) -> None:
        check_range(T0, h(168))

    def test_one_microsecond_past_it_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="168 hours"):
            check_range(T0, h(168) + timedelta(microseconds=1))

    def test_one_hour_past_it_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="168 hours"):
            check_range(T0, h(169))


class TestGapFill:
    def test_every_hour_is_present_even_when_empty(self) -> None:
        hours = fill_hours(hour_span(T0, h(3)), {})
        assert [hour.start for hour in hours] == [h(0), h(1), h(2)]
        # A measured zero: the hour exists, and no severity is invented for it.
        assert all(hour.counts == {} and hour.total == 0 for hour in hours)

    def test_counts_land_in_their_hour_and_carry_their_total(self) -> None:
        counts = {h(0): {"CRITICAL": 1, "HIGH": 4}, h(2): {"LOW": 2}}
        hours = fill_hours(hour_span(T0, h(3)), counts)
        assert [(hour.start, hour.counts, hour.total) for hour in hours] == [
            (h(0), {"CRITICAL": 1, "HIGH": 4}, 5),
            (h(1), {}, 0),
            (h(2), {"LOW": 2}, 2),
        ]

    def test_no_zero_entries_are_synthesized(self) -> None:
        """The counts mapping holds only what was counted; a zero severity is
        the absence of a key, so the payload never ships measured nothing."""
        hours = fill_hours(hour_span(T0, h(1)), {T0: {"HIGH": 4}})
        assert hours[0].counts == {"HIGH": 4}

    def test_the_totals_sum_to_the_population(self) -> None:
        counts = {h(0): {"HIGH": 4}, h(1): {"LOW": 2, "HIGH": 1}}
        hours = fill_hours(hour_span(T0, h(3)), counts)
        assert sum(hour.total for hour in hours) == 7

    def test_the_ceiling_constant_is_a_week_of_hours(self) -> None:
        assert timedelta(hours=168) == MAX_RANGE
