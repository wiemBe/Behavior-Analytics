"""Local business-hours gate shared by detection and the baseline.

The bucket start is UTC. The window is the source's timezone. A configuration
that cannot be trusted counts as expected-active: suppressing a real silence
is the dangerous direction.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.anomaly.business_hours import is_expected_active
from app.models.log_source import LogSource

LOGGER = "app.anomaly.business_hours"


def source(**kwargs: object) -> LogSource:
    """A detached source. `id` is set here because the warning key is the id."""
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        qradar_id=1,
        name="src",
        business_hours_only=True,
        business_hours_start=9,
        business_hours_end=18,
        business_days=[1, 2, 3, 4, 5],
        timezone_name="UTC",
    )
    defaults.update(kwargs)
    return LogSource(**defaults)


def _at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _warnings(caplog: pytest.LogCaptureFixture, source_id: uuid.UUID) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and str(source_id) in record.getMessage()
    ]


def test_utc_zone_matches_the_old_hour_and_weekday_window() -> None:
    """UTC keeps start <= hour < end on the listed ISO weekdays.

    2026-07-13 is a Monday. A week of hours pins the end hour as exclusive and
    the weekend as inactive, which is what the UTC clock used to do.
    """
    src = source()
    days = {1, 2, 3, 4, 5}
    start = datetime(2026, 7, 13, 0, tzinfo=UTC)
    for offset in range(7 * 24):
        when = start + timedelta(hours=offset)
        legacy = when.isoweekday() in days and 9 <= when.hour < 18
        assert is_expected_active(src, when) is legacy


def test_istanbul_window_follows_local_time_not_the_utc_hour() -> None:
    """Europe/Istanbul is UTC+3. 06:00Z is 09:00 local; 15:30Z is 18:30."""
    src = source(timezone_name="Europe/Istanbul")
    inside = _at(2026, 7, 15, 6, 0)
    outside = _at(2026, 7, 15, 15, 30)
    zone = ZoneInfo("Europe/Istanbul")
    assert inside.astimezone(zone).hour == 9
    assert outside.astimezone(zone).hour == 18
    # 09:00 local is inside 09-18. The same instant is 06:00 UTC, outside it.
    assert is_expected_active(src, inside) is True
    # 18:30 local is outside: the end hour is exclusive.
    assert is_expected_active(src, outside) is False


def test_berlin_dst_uses_the_local_offset_on_each_side_of_the_change() -> None:
    """Last Sunday of March and of October 2026. Days 1-7 so the weekend
    does not hide the clock change. 07:00Z is 08:00 before the spring
    forward and 09:00 after it; the October pair is the reverse.
    """
    src = source(
        timezone_name="Europe/Berlin",
        business_days=[1, 2, 3, 4, 5, 6, 7],
    )
    zone = ZoneInfo("Europe/Berlin")
    spring_before = _at(2026, 3, 28, 7)
    spring_after = _at(2026, 3, 29, 7)
    fall_before = _at(2026, 10, 24, 7)
    fall_after = _at(2026, 10, 25, 7)
    fall_later = _at(2026, 10, 25, 8)
    assert spring_before.astimezone(zone).hour == 8
    assert spring_after.astimezone(zone).hour == 9
    assert fall_before.astimezone(zone).hour == 9
    assert fall_after.astimezone(zone).hour == 8
    assert fall_later.astimezone(zone).hour == 9
    assert is_expected_active(src, spring_before) is False
    assert is_expected_active(src, spring_after) is True
    assert is_expected_active(src, fall_before) is True
    assert is_expected_active(src, fall_after) is False
    assert is_expected_active(src, fall_later) is True


def test_wrapped_window_covers_the_evening_and_the_early_morning() -> None:
    """22-6 is 22:00-05:59. Both of these days are business days, so this
    pins the hour wrap and the exclusive end, not which day owns it.
    """
    src = source(business_hours_start=22, business_hours_end=6)
    assert is_expected_active(src, _at(2026, 7, 15, 22)) is True
    assert is_expected_active(src, _at(2026, 7, 15, 23)) is True
    assert is_expected_active(src, _at(2026, 7, 16, 5)) is True
    assert is_expected_active(src, _at(2026, 7, 16, 6)) is False
    assert is_expected_active(src, _at(2026, 7, 15, 12)) is False


def test_wrapped_window_belongs_to_the_local_day_it_starts_on() -> None:
    """Monday-only 22-6: Tuesday 05:00 is Monday's window; Monday 05:00 is Sunday's."""
    src = source(
        business_hours_start=22, business_hours_end=6, business_days=[1]
    )
    assert is_expected_active(src, _at(2026, 7, 13, 23)) is True
    assert is_expected_active(src, _at(2026, 7, 14, 5)) is True
    assert is_expected_active(src, _at(2026, 7, 13, 5)) is False
    assert is_expected_active(src, _at(2026, 7, 14, 23)) is False


def test_equal_start_and_end_covers_every_hour_of_a_business_day() -> None:
    src = source(business_hours_start=9, business_hours_end=9)
    wednesday = datetime(2026, 7, 15, tzinfo=UTC)
    for hour in range(24):
        assert is_expected_active(src, wednesday.replace(hour=hour)) is True
    assert is_expected_active(src, _at(2026, 7, 19, 12)) is False


def test_local_weekday_is_used_when_it_differs_from_the_utc_weekday() -> None:
    """Monday 17:00 in Los Angeles is Tuesday 00:00 UTC in July 2026."""
    zone = ZoneInfo("America/Los_Angeles")
    local = datetime(2026, 7, 13, 17, 0, tzinfo=zone)
    when = local.astimezone(UTC)
    assert when.isoweekday() != local.isoweekday()
    assert local.isoweekday() == 1
    src = source(
        timezone_name="America/Los_Angeles",
        business_days=[1],
        business_hours_start=9,
        business_hours_end=18,
    )
    assert is_expected_active(src, when) is True


def test_naive_bucket_start_is_treated_as_utc() -> None:
    """A naive 10:00 is 10:00 UTC, not the process zone.

    Read as Pacific/Auckland (UTC+12 in July) that same wall time is 22:00
    the previous day, which is outside 09-18. The process zone is restored.
    """
    src = source()
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Auckland"
    time.tzset()
    try:
        naive = datetime(2026, 7, 15, 10, 0)
        assert naive.astimezone(UTC).hour != 10
        assert is_expected_active(src, naive) is True
        assert is_expected_active(src, datetime(2026, 7, 15, 3, 0)) is False
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_untrusted_configuration_is_expected_active_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown zone, [0], and [] used to suppress. They must not, and once only."""
    caplog.set_level(logging.WARNING, logger=LOGGER)
    # Wednesday 03:00 is outside Mon-Fri 09-18, so a trusted window is inactive.
    when = _at(2026, 7, 15, 3)
    bad_zone = source(timezone_name="Not/AZone")
    empty = source(business_days=[])
    zero = source(business_days=[0])
    hours = source(business_hours_start=25)
    mixed = source(business_days=[1, 0])
    for src in (bad_zone, empty, zero, hours, mixed):
        for _ in range(4):
            assert is_expected_active(src, when) is True
        messages = _warnings(caplog, src.id)
        assert len(messages) == 1
    assert "ZoneInfoNotFoundError" in _warnings(caplog, bad_zone.id)[0]
    assert "InvalidBusinessDays" in _warnings(caplog, empty.id)[0]
    assert "InvalidBusinessDays" in _warnings(caplog, zero.id)[0]
    assert "InvalidBusinessHours" in _warnings(caplog, hours.id)[0]
    assert "InvalidBusinessDays" in _warnings(caplog, mixed.id)[0]

    quiet = source(business_hours_only=False, timezone_name="Not/AZone", business_days=[])
    assert is_expected_active(quiet, when) is True
    assert _warnings(caplog, quiet.id) == []
