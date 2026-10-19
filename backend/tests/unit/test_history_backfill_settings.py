"""The `history_backfill_*` settings: defaults, bounds and the startup cross-checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


def make(**over: object) -> Settings:
    return Settings(_env_file=None, encryption_key=KEY, **over)  # type: ignore[call-arg]


def test_the_designed_defaults() -> None:
    s = make()
    assert s.history_backfill_enabled is True
    assert s.history_backfill_days == 28
    assert s.history_backfill_searches_per_tick == 10
    assert s.history_backfill_seconds_per_tick == 120
    assert s.history_backfill_max_hours == 72
    assert s.history_backfill_floor_margin_hours == 24
    assert s.history_backfill_max_failures == 3
    assert s.history_backfill_max_lower_bound_share == 0.05
    assert s.history_backfill_waf_breakdown_days == 7
    assert s.history_backfill_baseline_sources_per_tick == 50


# (setting, lowest valid, one below, highest valid, one above); None = no bound on that side
BOUNDS = [
    ("history_backfill_days", 7, 6, 90, 91),
    ("history_backfill_searches_per_tick", 1, 0, 64, 65),
    ("history_backfill_seconds_per_tick", 10, 9, 600, 601),
    ("history_backfill_max_hours", 1, 0, 168, 169),
    ("history_backfill_floor_margin_hours", 1, 0, 168, 169),
    ("history_backfill_max_failures", 1, 0, 20, 21),
    ("history_backfill_max_lower_bound_share", 0.0, -0.01, 1.0, 1.01),
    ("history_backfill_waf_breakdown_days", 1, 0, 7, None),
    ("history_backfill_baseline_sources_per_tick", 1, 0, 5000, 5001),
]


@pytest.mark.parametrize(("name", "low", "below", "high", "above"), BOUNDS)
def test_each_bound_at_its_edge_and_one_past(
    name: str, low: float, below: float, high: float, above: float | None
) -> None:
    assert getattr(make(**{name: low}), name) == low
    with pytest.raises(ValidationError, match=name):
        make(**{name: below})
    assert getattr(make(**{name: high}), name) == high
    if above is not None:
        with pytest.raises(ValidationError, match=name):
            make(**{name: above})


class TestCrossChecks:
    def test_the_breakdown_days_may_not_exceed_the_window(self) -> None:
        make(history_backfill_days=7, history_backfill_waf_breakdown_days=7)
        with pytest.raises(ValidationError) as caught:
            make(history_backfill_days=7, history_backfill_waf_breakdown_days=8)
        message = str(caught.value)
        assert "HISTORY_BACKFILL_WAF_BREAKDOWN_DAYS" in message
        assert "HISTORY_BACKFILL_DAYS" in message
        assert "(8 > 7)" in message

    def test_a_longer_window_admits_a_longer_breakdown(self) -> None:
        make(history_backfill_days=30, history_backfill_waf_breakdown_days=30)
        with pytest.raises(ValidationError, match=r"\(31 > 30\)"):
            make(history_backfill_days=30, history_backfill_waf_breakdown_days=31)


def test_a_released_configuration_still_starts() -> None:
    """No startup refusal depends on a released family's own settings (HB-2 item 4).

    A 14-day baseline lookback is below the 28-day default window, and a cadence cap of 8
    searches is below the default 10 history searches; both are fitted at run time.
    """
    settings = make(
        baseline_lookback_days=14,
        search_cadence_max_searches_per_tick=8,
        waf_collection_enabled=True,
        ips_collection_enabled=True,
        catchall_collection_enabled=True,
    )
    assert settings.baseline_lookback_days == 14
    assert settings.history_backfill_days == 28
    # the same with every family's retention at its smallest accepted value
    make(
        waf_collection_enabled=True,
        waf_metric_retention_days=29,
        baseline_lookback_days=14,
        ips_metric_retention_days=2,
        catchall_retention_days=2,
        retention_enabled=True,
        retention_log_source_metric_days=5,
    )
