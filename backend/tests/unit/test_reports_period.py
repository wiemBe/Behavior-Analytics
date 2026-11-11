"""SOC-8b: the report period, the retention coverage and the n floor, with no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.models.enums import AnomalyFamily
from app.schemas.reports import FigureState, ReportPeriod
from app.services.reports import (
    MIN_FIGURE_N,
    PeriodError,
    _clock,
    check_range,
    coverage_for,
    resolve_period,
)

NOW = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)  # a Wednesday


def test_last_7d_and_30d_end_now() -> None:
    kind, start, end = resolve_period("last_7d", None, NOW)
    assert (kind, start, end) == (ReportPeriod.LAST_7D, NOW - timedelta(days=7), NOW)
    _, start, end = resolve_period("last_30d", None, NOW)
    assert (start, end) == (NOW - timedelta(days=30), NOW)


def test_last_periods_ignore_start() -> None:
    junk = datetime(2020, 1, 3, 5, tzinfo=UTC)
    assert resolve_period("last_7d", junk, NOW)[1] == NOW - timedelta(days=7)


def test_calendar_week_starts_monday_utc() -> None:
    monday = datetime(2026, 11, 2, tzinfo=UTC)
    kind, start, end = resolve_period("calendar_week", monday, NOW)
    assert kind is ReportPeriod.CALENDAR_WEEK
    assert (start, end) == (monday, datetime(2026, 11, 9, tzinfo=UTC))


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 11, 1, tzinfo=UTC),  # a Sunday
        datetime(2026, 11, 3, tzinfo=UTC),  # a Tuesday
        datetime(2026, 11, 2, 0, 0, 1, tzinfo=UTC),  # a Monday, not midnight
    ],
)
def test_calendar_week_refuses_a_non_boundary(start: datetime) -> None:
    with pytest.raises(PeriodError) as caught:
        resolve_period("calendar_week", start, NOW)
    assert caught.value.ref.code == "reports.start_not_boundary"


def test_a_start_in_another_offset_is_judged_in_utc() -> None:
    # Monday 00:00 UTC is Sunday 22:00 at -02:00, and a Monday 01:00 at +01:00 is Monday 00:00.
    ok = datetime(2026, 11, 2, 1, tzinfo=timezone(timedelta(hours=1)))
    assert resolve_period("calendar_week", ok, NOW)[1] == datetime(2026, 11, 2, tzinfo=UTC)
    bad = datetime(2026, 11, 2, tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(PeriodError):
        resolve_period("calendar_week", bad, NOW)


def test_calendar_month_and_the_december_rollover() -> None:
    later = datetime(2027, 2, 1, tzinfo=UTC)
    _, start, end = resolve_period("calendar_month", datetime(2026, 11, 1, tzinfo=UTC), later)
    assert end == datetime(2026, 12, 1, tzinfo=UTC)
    _, start, end = resolve_period("calendar_month", datetime(2026, 12, 1, tzinfo=UTC), later)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)
    assert end - start == timedelta(days=31)


def test_calendar_month_refuses_a_non_first() -> None:
    with pytest.raises(PeriodError) as caught:
        resolve_period("calendar_month", datetime(2026, 11, 2, tzinfo=UTC), NOW)
    assert caught.value.ref.code == "reports.start_not_boundary"


@pytest.mark.parametrize("period", ["calendar_week", "calendar_month"])
def test_calendar_periods_need_a_start(period: str) -> None:
    with pytest.raises(PeriodError) as caught:
        resolve_period(period, None, NOW)
    assert caught.value.ref.code == "reports.start_required"


def test_an_unknown_period_is_refused() -> None:
    with pytest.raises(PeriodError) as caught:
        resolve_period("last_90d", None, NOW)
    assert caught.value.ref.code == "reports.period_unknown"


def test_a_range_over_31_days_is_refused() -> None:
    check_range(NOW - timedelta(days=31), NOW)
    with pytest.raises(PeriodError) as caught:
        check_range(NOW - timedelta(days=32), NOW)
    assert caught.value.ref.code == "reports.range_too_long"


def test_retention_makes_only_the_older_families_partial() -> None:
    settings = Settings()
    start = NOW - timedelta(days=200)  # past waf/pw (180 d), inside the 365 d families
    by_family = {c.family: c for c in coverage_for(settings, start, NOW)}
    for family in (AnomalyFamily.WAF, AnomalyFamily.PROPERTY_WATCH):
        assert by_family[family].state is FigureState.PARTIAL
        assert by_family[family].covered_from == NOW - timedelta(days=180)
    for family in (AnomalyFamily.LOG_SOURCE, AnomalyFamily.IPS, AnomalyFamily.RULE):
        assert by_family[family].state is FigureState.OK
        assert by_family[family].covered_from is None


def test_the_n_floor_is_five() -> None:
    assert MIN_FIGURE_N == 5
    four = _clock(100.0, 4, partial=False)
    assert (four.median_seconds, four.n, four.state) == (None, 4, FigureState.INSUFFICIENT_DATA)
    five = _clock(100.0, 5, partial=False)
    assert (five.median_seconds, five.state) == (100.0, FigureState.OK)
    assert _clock(100.0, 5, partial=True).state is FigureState.PARTIAL
    assert _clock(None, 0, partial=False).state is FigureState.INSUFFICIENT_DATA


def test_the_current_calendar_period_ends_now() -> None:
    _, _, end = resolve_period("calendar_week", datetime(2026, 11, 9, tzinfo=UTC), NOW)
    assert end == NOW  # the Monday after NOW would be the 16th
    _, _, end = resolve_period("calendar_month", datetime(2026, 11, 1, tzinfo=UTC), NOW)
    assert end == NOW


def test_a_start_after_now_is_refused() -> None:
    with pytest.raises(PeriodError) as caught:
        resolve_period("calendar_week", datetime(2026, 11, 16, tzinfo=UTC), NOW)
    assert caught.value.ref.code == "reports.start_in_future"
    with pytest.raises(PeriodError):
        resolve_period("calendar_month", datetime(2026, 12, 1, tzinfo=UTC), NOW)
