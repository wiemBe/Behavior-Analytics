"""The `pw_*` settings: defaults, bounds and the startup cross-checks (design §G.14.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


def make(**over: object) -> Settings:
    return Settings(_env_file=None, encryption_key=KEY, **over)  # type: ignore[call-arg]


def test_both_switches_default_false() -> None:
    settings = make()
    assert settings.pw_collection_enabled is False
    assert settings.pw_alerting_enabled is False


def test_the_designed_defaults() -> None:
    s = make()
    assert (s.pw_max_enabled_watches, s.pw_max_searches_per_tick) == (10, 40)
    assert (s.pw_max_searches_per_watch_per_tick, s.pw_max_sources_per_search) == (12, 50)
    assert (s.pw_max_chunks_per_watch, s.pw_max_rows_per_search) == (4, 5000)
    assert (s.pw_max_values_per_bucket, s.pw_max_new_values_per_bucket) == (50, 20)
    assert (s.pw_max_active_values_per_watch, s.pw_max_pinned_values) == (200, 10)
    assert (s.pw_value_max_length, s.pw_min_admission_events) == (255, 3)
    assert (s.pw_value_active_days, s.pw_min_coverage) == (7, 0.90)
    assert (s.pw_scope_stale_after_hours, s.pw_scope_change_grace_buckets) == (24, 12)
    assert (s.pw_baseline_lookback_days, s.pw_baseline_values_per_run) == (28, 100)
    assert s.pw_max_evaluation_buckets_per_run == 24
    assert s.pw_baseline_min_visits == 2
    assert (s.pw_spike_ratio, s.pw_min_absolute_delta_events, s.pw_min_bucket_events) == (
        2.0, 50.0, 20.0,
    )
    assert (s.pw_drop_min_median, s.pw_drop_ratio) == (20.0, 0.5)
    assert (s.pw_disappear_min_median, s.pw_disappear_buckets) == (5.0, 6)
    assert (s.pw_new_value_history_days, s.pw_new_value_window_buckets) == (7, 12)
    assert (s.pw_new_value_min_events, s.pw_new_value_min_buckets) == (20, 2)
    assert (s.pw_metric_retention_days, s.pw_anomaly_retention_days) == (35, 180)
    assert (s.pw_retention_batch_size, s.pw_retention_max_batches) == (5000, 20)
    assert s.pw_retention_interval_seconds == 86400


@pytest.mark.parametrize(
    ("name", "bad", "good"),
    [
        ("pw_max_enabled_watches", (0, 101), (1, 100)),
        ("pw_max_searches_per_tick", (0, 257), (1, 256)),
        ("pw_max_searches_per_watch_per_tick", (0, 257), (1, 12)),
        ("pw_max_sources_per_search", (0, 501), (1, 500)),
        ("pw_max_chunks_per_watch", (0, 21), (1, 20)),
        ("pw_max_rows_per_search", (0, 100_001), (1, 5000)),
        ("pw_max_values_per_bucket", (0, 5001), (1, 5000)),
        ("pw_max_new_values_per_bucket", (-1, 1001), (0, 1000)),
        ("pw_max_active_values_per_watch", (0, 5001), (1, 5000)),
        ("pw_max_pinned_values", (0, 21), (1, 20)),
        ("pw_value_max_length", (0, 256), (1, 255)),
        ("pw_min_admission_events", (0,), (1,)),
        ("pw_value_active_days", (0, 91), (1, 90)),
        ("pw_min_coverage", (-0.01, 1.01), (0.0, 1.0)),
        ("pw_scope_stale_after_hours", (0, 721), (1, 720)),
        ("pw_scope_change_grace_buckets", (-1, 2017), (0, 2016)),
        ("pw_baseline_lookback_days", (0,), (1,)),
        ("pw_baseline_values_per_run", (0, 5001), (1, 5000)),
        ("pw_max_evaluation_buckets_per_run", (0, 2017), (1, 2016)),
        ("pw_spike_ratio", (1.0, 0.5), (1.01,)),
        ("pw_min_absolute_delta_events", (-1.0,), (0.0,)),
        ("pw_min_bucket_events", (-1.0,), (0.0,)),
        ("pw_drop_min_median", (-1.0,), (0.0,)),
        ("pw_drop_ratio", (0.0, 1.0), (0.01, 0.99)),
        ("pw_disappear_min_median", (-1.0,), (0.0,)),
        ("pw_disappear_buckets", (0, 2017), (1, 2016)),
        ("pw_new_value_history_days", (0, 61), (1, 60)),
        ("pw_new_value_window_buckets", (0, 2017), (1, 2016)),
        ("pw_new_value_min_events", (0,), (1,)),
        ("pw_new_value_min_buckets", (0,), (1,)),
        ("pw_metric_retention_days", (1,), (2,)),
        ("pw_anomaly_retention_days", (1,), (2,)),
        ("pw_retention_batch_size", (99, 100_001), (100, 100_000)),
        ("pw_retention_max_batches", (0, 1001), (1, 1000)),
        ("pw_retention_interval_seconds", (3599,), (3600,)),
    ],
)
def test_each_bound_is_enforced(
    name: str, bad: tuple[object, ...], good: tuple[object, ...]
) -> None:
    for value in bad:
        with pytest.raises(ValidationError):
            make(**{name: value})
    for value in good:
        # The cross-checks run only with collection on, so a single bound is tested alone.
        assert getattr(make(**{name: value}), name) == value


class TestCrossChecks:
    """Contradictory settings are refused where PW collection is on, and only there."""

    def test_rows_may_not_exceed_the_ariel_multi_bucket_cap(self) -> None:
        with pytest.raises(ValidationError, match="PW_MAX_ROWS_PER_SEARCH"):
            make(pw_collection_enabled=True, pw_max_rows_per_search=6000,
                 ariel_multi_bucket_max_rows=5000)
        make(pw_collection_enabled=True, pw_max_rows_per_search=5000,
             ariel_multi_bucket_max_rows=5000)

    def test_the_per_watch_budget_may_not_exceed_the_tick_budget(self) -> None:
        with pytest.raises(ValidationError, match="PW_MAX_SEARCHES_PER_WATCH_PER_TICK"):
            make(pw_collection_enabled=True, pw_max_searches_per_tick=8)
        make(pw_collection_enabled=True, pw_max_searches_per_tick=12)

    def test_pins_count_toward_the_active_ceiling(self) -> None:
        with pytest.raises(ValidationError, match="PW_MAX_PINNED_VALUES"):
            make(pw_collection_enabled=True, pw_max_pinned_values=10,
                 pw_max_active_values_per_watch=5)

    def test_the_baseline_lookback_may_not_exceed_retention(self) -> None:
        with pytest.raises(ValidationError, match="PW_BASELINE_LOOKBACK_DAYS"):
            make(pw_collection_enabled=True, pw_baseline_lookback_days=36)
        make(pw_collection_enabled=True, pw_baseline_lookback_days=35)

    def test_the_baseline_visits_must_fit_in_the_lookback(self) -> None:
        with pytest.raises(ValidationError, match="PW_BASELINE_MIN_VISITS") as caught:
            make(pw_collection_enabled=True, pw_baseline_min_visits=5,
                 pw_baseline_lookback_days=28)
        assert "PW_BASELINE_LOOKBACK_DAYS" in str(caught.value)
        make(pw_collection_enabled=True, pw_baseline_min_visits=4, pw_baseline_lookback_days=28)
        # a single visit asks for no recurrence, so any lookback holds it
        make(pw_collection_enabled=True, pw_baseline_min_visits=1, pw_baseline_lookback_days=1)

    def test_the_baseline_visits_are_bounded(self) -> None:
        for bad in (0, 53):
            with pytest.raises(ValidationError):
                make(pw_baseline_min_visits=bad)
        assert make(pw_baseline_min_visits=1).pw_baseline_min_visits == 1
        assert make(pw_baseline_min_visits=52).pw_baseline_min_visits == 52

    def test_the_novelty_history_must_stay_below_retention(self) -> None:
        with pytest.raises(ValidationError, match="PW_NEW_VALUE_HISTORY_DAYS"):
            make(pw_collection_enabled=True, pw_new_value_history_days=35,
                 pw_metric_retention_days=35)
        make(pw_collection_enabled=True, pw_new_value_history_days=34)

    def test_every_problem_is_reported_at_once(self) -> None:
        with pytest.raises(ValidationError) as caught:
            make(pw_collection_enabled=True, pw_baseline_lookback_days=40,
                 pw_new_value_history_days=40)
        message = str(caught.value)
        assert "PW_BASELINE_LOOKBACK_DAYS" in message
        assert "PW_NEW_VALUE_HISTORY_DAYS" in message

    def test_a_deployment_without_pw_is_never_refused_over_a_pw_setting(self) -> None:
        settings = make(pw_baseline_min_visits=52, pw_baseline_lookback_days=90,
                        pw_new_value_history_days=50,
                        pw_max_searches_per_tick=5, pw_max_pinned_values=20,
                        pw_max_active_values_per_watch=3)
        assert settings.pw_collection_enabled is False
