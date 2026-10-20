"""The pure helpers of the history-backfill service (the words and the arithmetic).

These are the parts of the admin surface that decide what the page says without
touching the database: the window's wording, the days a scope read, the families'
buckets and the plans the IPS estimate makes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.history.state import HistoryFamily
from app.models.history_backfill import HistoryBackfillScope
from app.services.history_backfill import (
    bucket_seconds_of,
    days_read,
    duration_words,
    floor_to_bucket,
    group_sources,
    scope_out,
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
    assert days_read(HistoryFamily.WAF, START, through, 300) == 0.4
    # The same span in hourly buckets is 11 buckets, not 121, so a different day count.
    assert days_read(HistoryFamily.RULE_MATCH, START, through, 3600) == 0.5


def test_the_days_read_are_null_when_an_end_is_missing() -> None:
    assert days_read(HistoryFamily.WAF, None, START, 300) is None
    assert days_read(HistoryFamily.WAF, START, None, 300) is None
    assert days_read(HistoryFamily.WAF, None, None, 300) is None


def test_the_days_read_are_null_when_the_read_went_backwards() -> None:
    through = START - timedelta(hours=1)
    assert days_read(HistoryFamily.WAF, START, through, 300) is None


def test_the_days_read_have_one_decimal() -> None:
    # One bucket of five minutes is a twentieth of a day, which rounds to 0.0.
    assert days_read(HistoryFamily.WAF, START, START + timedelta(minutes=5), 300) == 0.0
    # A whole day of buckets is 1.0, never 1.0001.
    assert days_read(HistoryFamily.WAF, START, START + timedelta(days=1), 300) == 1.0


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


# --------------------------------------------------------------- the floor and the name


def stored_scope(
    family: HistoryFamily,
    *,
    key: str = "",
    floor: datetime | None = None,
    target_start: datetime | None = None,
    through: datetime | None = None,
) -> HistoryBackfillScope:
    """A scope row as a family adapter would have stored it (no session needed)."""
    return HistoryBackfillScope(
        instance_id=uuid.uuid4(),
        family=family.value,
        scope_key=key,
        state="READING",
        days_requested=28,
        days_target=28,
        target_start=target_start,
        floor=floor,
        through=through,
        anchor=None,
        started_at=START,
        finished_at=None,
        reason=None,
        progress={},
    )


@pytest.mark.parametrize(
    ("family", "proved"),
    [
        (HistoryFamily.WAF, True),
        (HistoryFamily.IPS, True),
        (HistoryFamily.RULE_MATCH, True),
        (HistoryFamily.PHASE_A, False),
        (HistoryFamily.CATCHALL, False),
    ],
)
def test_a_scope_says_whether_its_floor_is_proved_or_only_where_the_read_started(
    family: HistoryFamily, proved: bool
) -> None:
    # §B.3.2: only WAF, IPS and rule matches put the floor QRadar proved in the column.
    # Phase A and catch-all prove one floor per source, which the column cannot hold.
    assert scope_out(stored_scope(family, floor=START), 300).floor_proved is proved


@pytest.mark.parametrize("family", [HistoryFamily.PHASE_A, HistoryFamily.CATCHALL])
def test_a_phase_a_or_catch_all_read_counts_from_target_start_not_from_the_margin(
    family: HistoryFamily,
) -> None:
    # The column holds the read start `T0 - m`; the margin buckets are not read proof.
    target_start = START + timedelta(hours=24)
    floor = target_start - timedelta(hours=24)
    through = target_start + timedelta(hours=10)
    assert days_read(family, floor, through, 300, target_start=target_start) == 0.4
    # Without a `T0` the only start there is, is the column: 34 hours, rounded to one
    # decimal.
    assert days_read(family, floor, through, 300) == 1.4


def test_a_waf_read_counts_from_its_floor_whatever_its_target_start_says() -> None:
    through = START + timedelta(hours=10)
    assert (
        days_read(
            HistoryFamily.WAF, START, through, 300, target_start=START + timedelta(hours=5)
        )
        == 0.4
    )


def test_a_scope_row_has_a_name_only_for_a_per_source_family() -> None:
    assert scope_out(stored_scope(HistoryFamily.WAF, key="7"), 300, "web-7").name == "web-7"
    assert scope_out(stored_scope(HistoryFamily.IPS, key="7"), 300, "ips-7").name == "ips-7"
    # A console-wide row never borrows a log source's name.
    assert scope_out(stored_scope(HistoryFamily.PHASE_A), 300, "web-7").name is None
    # A source that is no longer known has none either: the page shows the key.
    assert scope_out(stored_scope(HistoryFamily.WAF, key="7"), 300).name is None

