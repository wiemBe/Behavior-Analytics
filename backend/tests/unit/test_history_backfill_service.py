"""The pure helpers of the history-backfill service (the words and the arithmetic).

These are the parts of the admin surface that decide what the page says without
touching the database: the window's wording, the days a scope read, the families'
buckets and the plans the IPS estimate makes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.history.state import HistoryFamily
from app.services.history_backfill import (
    bucket_seconds_of,
    days_read,
    duration_words,
    floor_to_bucket,
    group_sources,
)

START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("seconds", "words"),
    [
        (300, "5 minutes"),
        (900, "15 minutes"),
        (1800, "30 minutes"),
        (3600, "1 hour"),
        (7200, "2 hours"),
        (28800, "8 hours"),
    ],
)
def test_a_cadence_is_written_in_the_unit_that_reads_as_a_duration(
    seconds: int, words: str
) -> None:
    assert duration_words(seconds) == words


def test_the_days_read_are_whole_buckets_of_the_families_bucket() -> None:
    # Ten hours of five-minute buckets: 121 buckets, 0.4 of a day.
    through = START + timedelta(hours=10)
    assert days_read(START, through, 300) == 0.4
    # The same span in hourly buckets is 11 buckets, not 121, so a different day count.
    assert days_read(START, through, 3600) == 0.5


def test_the_days_read_are_null_when_an_end_is_missing() -> None:
    assert days_read(None, START, 300) is None
    assert days_read(START, None, 300) is None
    assert days_read(None, None, 300) is None


def test_the_days_read_are_null_when_the_read_went_backwards() -> None:
    assert days_read(START, START - timedelta(hours=1), 300) is None


def test_the_days_read_have_one_decimal() -> None:
    # One bucket of five minutes is a twentieth of a day, which rounds to 0.0.
    assert days_read(START, START + timedelta(minutes=5), 300) == 0.0
    # A whole day of buckets is 1.0, never 1.0001.
    assert days_read(START, START + timedelta(days=1), 300) == 1.0


def test_the_rule_matches_read_hourly_windows_and_the_others_five_minute_buckets() -> None:
    settings = Settings()
    assert bucket_seconds_of(HistoryFamily.RULE_MATCH, settings) == 3600
    assert bucket_seconds_of(HistoryFamily.PHASE_A, settings) == 300
    assert bucket_seconds_of(HistoryFamily.WAF, settings) == 300


def test_the_estimate_end_is_a_whole_bucket() -> None:
    now = datetime(2026, 10, 19, 12, 7, 43, tzinfo=UTC)
    assert floor_to_bucket(now, 300) == datetime(2026, 10, 19, 12, 5, tzinfo=UTC)
    assert floor_to_bucket(now, 3600) == datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def test_the_sources_are_grouped_in_a_fixed_order_at_most_size_per_plan() -> None:
    assert group_sources([3, 1, 2], 2) == [[1, 2], [3]]
    assert group_sources([], 2) == []
    assert group_sources([5], 2) == [[5]]
