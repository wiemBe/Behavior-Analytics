"""Cron floor: successive CronTrigger fires, at least 300 seconds apart.

The seven D-7 presets (docs/SEARCH-CADENCE.md) pass. A every-minute cron fails.
An irregular cron with one short gap fails. The walk stops at its horizon and
at its fire cap. A 5-minute schedule over a year does not ask the trigger once
per fire, and a DST transition can close a gap that ordinary days do not have.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.services.search_scheduler import (
    CRON_FLOOR_MAX_FIRES,
    CRON_FLOOR_SECONDS,
    check_cron_floor,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)

# The editor's presets. The fastest is five minutes, which is the floor.
PRESETS = (
    "*/5 * * * *",
    "*/15 * * * *",
    "*/30 * * * *",
    "0 * * * *",
    "0 */2 * * *",
    "0 */4 * * *",
    "0 */8 * * *",
)


def test_five_minute_presets_pass() -> None:
    for cron in PRESETS:
        outcome = check_cron_floor(cron, "UTC", now=NOW)
        assert outcome.accepted, outcome.reason
        assert outcome.fires_checked <= CRON_FLOOR_MAX_FIRES
        assert outcome.min_gap_seconds is not None
        assert outcome.min_gap_seconds >= CRON_FLOOR_SECONDS
    # The 5-minute preset is dense enough that a tiny cap would not be the
    # default. This fails if the walk stops after a handful of fires.
    five = check_cron_floor("*/5 * * * *", "UTC", now=NOW)
    assert five.fires_checked >= 1_000
    assert five.min_gap_seconds == CRON_FLOOR_SECONDS


def test_every_minute_fails() -> None:
    outcome = check_cron_floor("* * * * *", "UTC", now=NOW)
    assert not outcome.accepted
    assert outcome.fires_checked == 1
    assert outcome.reason is not None
    assert "60" in outcome.reason
    assert str(CRON_FLOOR_SECONDS) in outcome.reason


def test_irregular_cron_with_one_short_gap_fails() -> None:
    # 00:00, 00:15 (15 min, legal), 00:17 (2 min). The short gap is not the
    # first one, so a check that only looks at the first pair would pass.
    outcome = check_cron_floor("0,15,17 0 * * *", "UTC", now=NOW)
    assert not outcome.accepted
    assert outcome.fires_checked == 2
    assert outcome.reason is not None
    assert "120" in outcome.reason
    assert str(CRON_FLOOR_SECONDS) in outcome.reason


def test_horizon_bound_holds() -> None:
    # The only short gap is 1 Jan 00:00 then 00:01. A one-day horizon starting
    # 2 Jan never reaches it, and the walk does not go looking past the horizon.
    start = datetime(2026, 1, 2, tzinfo=UTC)
    bounded = check_cron_floor(
        "0,1 0 1 1 *", "UTC", now=start, horizon=timedelta(days=1), max_fires=10
    )
    assert bounded.accepted
    assert bounded.fires_checked == 0

    wide = check_cron_floor("0,1 0 1 1 *", "UTC", now=start, horizon=timedelta(days=366))
    assert not wide.accepted
    assert wide.fires_checked == 1
    assert wide.reason is not None
    assert "60" in wide.reason

    # A legal dense cron stops at the cap even though the horizon is longer.
    capped = check_cron_floor(
        "*/5 * * * *", "UTC", now=start, horizon=timedelta(days=366), max_fires=4
    )
    assert capped.accepted
    assert capped.fires_checked == 4
    assert capped.min_gap_seconds == CRON_FLOOR_SECONDS


def test_dst_fold_does_not_reject_the_five_minute_preset() -> None:
    # US fall-back, 2026-11-01. Wall-clock subtraction sees a negative gap
    # across the repeated hour; absolute UTC does not.
    now = datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("America/New_York"))
    outcome = check_cron_floor(
        "*/5 * * * *", "America/New_York", now=now, horizon=timedelta(hours=6)
    )
    assert outcome.accepted, outcome.reason
    assert outcome.min_gap_seconds == CRON_FLOOR_SECONDS


def test_invalid_cron_is_refused() -> None:
    outcome = check_cron_floor("not a cron", "UTC", now=NOW)
    assert not outcome.accepted
    assert outcome.fires_checked == 0
    assert outcome.reason is not None
    assert "not a valid cron expression" in outcome.reason
    assert "not a cron" in outcome.reason


def test_cron_with_no_future_fire_is_refused() -> None:
    outcome = check_cron_floor("0 0 31 2 *", "UTC", now=NOW)
    assert not outcome.accepted
    assert outcome.fires_checked == 0
    assert outcome.reason is not None
    assert "no future fire time" in outcome.reason


def test_five_minute_floor_does_not_walk_every_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    """`*/5` over 366 days stays cheap in a fixed-offset zone and a DST zone.

    The old walk called `get_next_fire_time` once per gap, 105,408 times, and
    took about 1.5 seconds. The linear scan covers that same count of gaps
    and only asks the trigger for the first fire plus each offset change.
    """
    calls = {"n": 0}
    original = CronTrigger.get_next_fire_time

    def wrapped(
        self: CronTrigger, previous: datetime | None, now: datetime
    ) -> datetime | None:
        calls["n"] += 1
        return original(self, previous, now)

    monkeypatch.setattr(CronTrigger, "get_next_fire_time", wrapped)
    for zone in ("Europe/Istanbul", "America/New_York"):
        calls["n"] = 0
        started = time.perf_counter()
        outcome = check_cron_floor("*/5 * * * *", zone, now=NOW)
        elapsed = time.perf_counter() - started
        assert outcome.accepted, outcome.reason
        assert outcome.min_gap_seconds == CRON_FLOOR_SECONDS
        assert outcome.fires_checked == CRON_FLOOR_MAX_FIRES
        assert calls["n"] < 2_000, calls["n"]
        # Generous against a busy machine. The measured cost is well under
        # the 50 ms budget; 105,408 trigger calls are not.
        assert elapsed < 0.25, elapsed


def test_dst_transition_closes_a_gap_ordinary_days_do_not() -> None:
    # Fall back, America/New_York 2026-11-01 at 02:00. 01:59 EDT and the
    # repeated 01:00 EST are 60 seconds apart. On an ordinary day the closest
    # fires of this expression are 59 minutes apart, so a check that never
    # walks the folded hour accepts it.
    fall = check_cron_floor(
        "0,59 1 * * *",
        "America/New_York",
        now=datetime(2026, 11, 1, tzinfo=ZoneInfo("America/New_York")),
        horizon=timedelta(days=2),
    )
    assert not fall.accepted
    assert fall.fires_checked == 2
    assert fall.reason is not None
    assert "fires 60 seconds apart" in fall.reason
    assert "2026-11-01T05:59:00+00:00" in fall.reason
    assert "2026-11-01T06:00:00+00:00" in fall.reason

    ordinary_fall = check_cron_floor(
        "0,59 1 * * *",
        "America/New_York",
        now=datetime(2026, 6, 1, tzinfo=ZoneInfo("America/New_York")),
        horizon=timedelta(days=2),
    )
    assert ordinary_fall.accepted, ordinary_fall.reason
    assert ordinary_fall.min_gap_seconds is not None
    assert ordinary_fall.min_gap_seconds >= CRON_FLOOR_SECONDS

    # Spring forward, 2026-03-08 at 02:00. 01:56 EST and 03:00 EDT are 240
    # seconds apart. The next day those wall times are 64 minutes apart.
    spring = check_cron_floor(
        "0,56 1,3 * * *",
        "America/New_York",
        now=datetime(2026, 3, 8, tzinfo=ZoneInfo("America/New_York")),
        horizon=timedelta(days=1),
    )
    assert not spring.accepted
    assert spring.fires_checked == 2
    assert spring.reason is not None
    assert "fires 240 seconds apart" in spring.reason
    assert "2026-03-08T06:56:00+00:00" in spring.reason
    assert "2026-03-08T07:00:00+00:00" in spring.reason

    ordinary_spring = check_cron_floor(
        "0,56 1,3 * * *",
        "America/New_York",
        now=datetime(2026, 3, 9, tzinfo=ZoneInfo("America/New_York")),
        horizon=timedelta(days=1),
    )
    assert ordinary_spring.accepted, ordinary_spring.reason
    assert ordinary_spring.min_gap_seconds is not None
    assert ordinary_spring.min_gap_seconds >= CRON_FLOOR_SECONDS
