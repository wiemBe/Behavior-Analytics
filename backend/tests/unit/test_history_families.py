"""The family registry, window fitting and the history search budget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.history import (
    FAMILY_SPECS,
    HistoryFamily,
    family_spec,
    fit_family_window,
    history_search_budget,
)
from app.models import Base
from app.models.enums import SearchFamily

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


def make(**over: object) -> Settings:
    return Settings(_env_file=None, encryption_key=KEY, **over)  # type: ignore[call-arg]


def ns(**over: object) -> SimpleNamespace:
    """A settings stand-in: the fitting reads attributes only."""
    base: dict[str, object] = {
        "history_backfill_days": 28,
        "history_backfill_max_hours": 72,
        "history_backfill_searches_per_tick": 10,
        "search_cadence_max_searches_per_tick": 16,
        "baseline_lookback_days": 28,
        "ips_baseline_lookback_days": 28,
        "rule_health_window_days": 30,
        "retention_enabled": False,
        "retention_log_source_metric_days": None,
        "waf_retention_enabled": True,
        "waf_metric_retention_days": 35,
        "ips_retention_enabled": True,
        "ips_metric_retention_days": 35,
        "catchall_retention_days": 35,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------------------ registry
def test_every_scope_table_family_has_an_entry_and_nothing_else() -> None:
    from app.history.state import SCOPE_TABLE_FAMILIES as scope_families

    assert set(FAMILY_SPECS) == set(scope_families)
    assert HistoryFamily.PROPERTY_WATCH not in FAMILY_SPECS
    with pytest.raises(KeyError, match="PROPERTY_WATCH"):
        family_spec(HistoryFamily.PROPERTY_WATCH)


def _setting_names(spec) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        name
        for name in (
            spec.collection_switch,
            spec.lookback_setting,
            spec.retention_setting,
            spec.retention_switch,
        )
        if name is not None
    ]


def test_every_setting_name_in_the_registry_exists_on_settings() -> None:
    fields = set(Settings.model_fields)
    for spec in FAMILY_SPECS.values():
        for name in _setting_names(spec):
            assert name in fields, (spec.family, name)
    for name in (
        "history_backfill_days",
        "history_backfill_max_hours",
        "history_backfill_searches_per_tick",
        "search_cadence_max_searches_per_tick",
    ):
        assert name in fields


def test_every_table_exists_and_the_search_family_is_a_member() -> None:
    for spec in FAMILY_SPECS.values():
        for table in spec.tables:
            assert table in Base.metadata.tables
        assert spec.search_family in {member.value for member in SearchFamily}


def test_a_family_without_a_switch_says_why() -> None:
    for spec in FAMILY_SPECS.values():
        assert (spec.collection_switch is None) == (spec.always_on_reason is not None)
    assert FAMILY_SPECS[HistoryFamily.PHASE_A].collection_switch is None
    assert FAMILY_SPECS[HistoryFamily.RULE_MATCH].retention_setting is None


def test_the_marker_tables_are_the_ones_each_family_writes() -> None:
    assert {family: spec.tables for family, spec in FAMILY_SPECS.items()} == {
        HistoryFamily.PHASE_A: ("log_source_metric",),
        HistoryFamily.WAF: ("waf_source_bucket",),
        HistoryFamily.IPS: ("ips_source_bucket",),
        HistoryFamily.CATCHALL: ("catchall_bucket",),
        HistoryFamily.RULE_MATCH: ("rule_match_window",),
    }


# ------------------------------------------------------------------------------ fitting
def test_the_defaults_read_the_asked_window_everywhere_it_fits() -> None:
    settings = make()
    for family in (HistoryFamily.PHASE_A, HistoryFamily.WAF, HistoryFamily.IPS,
                   HistoryFamily.CATCHALL):
        fitted = fit_family_window(family, settings)
        assert (fitted.days, fitted.reason, fitted.available) == (28, None, True), family
    # rule matches read their own 30-day window, capped by the 28 asked
    assert fit_family_window(HistoryFamily.RULE_MATCH, settings).days == 28


def test_the_asked_days_limit_is_named() -> None:
    fitted = fit_family_window(HistoryFamily.WAF, ns(history_backfill_days=14))
    assert fitted.days == 14
    assert fitted.limited_by == ("history_backfill_days",)


@pytest.mark.parametrize(
    ("lookback", "days"), [(29, 28), (28, 28), (27, 27), (8, 8), (7, 7)]
)
def test_the_lookback_limits_at_its_edge(lookback: int, days: int) -> None:
    fitted = fit_family_window(HistoryFamily.WAF, ns(baseline_lookback_days=lookback))
    assert fitted.days == days
    if lookback < 28:
        assert fitted.limited_by == ("baseline_lookback_days",)
        assert fitted.reason is not None
        assert "BASELINE_LOOKBACK_DAYS is" in fitted.reason and f"to {days} days" in fitted.reason
    else:
        assert fitted.reason is None


def test_below_seven_days_the_lookback_makes_the_family_unavailable() -> None:
    fitted = fit_family_window(HistoryFamily.IPS, ns(ips_baseline_lookback_days=6))
    assert fitted.days is None and not fitted.available
    assert fitted.limited_by == ("ips_baseline_lookback_days",)
    assert fitted.reason is not None
    assert "IPS_BASELINE_LOOKBACK_DAYS is 6 days" in fitted.reason
    assert "below the 7-day minimum" in fitted.reason


def test_the_rule_health_window_is_the_rule_match_lookback() -> None:
    assert fit_family_window(HistoryFamily.RULE_MATCH, ns(rule_health_window_days=10)).days == 10
    fitted = fit_family_window(HistoryFamily.RULE_MATCH, ns(rule_health_window_days=6))
    assert fitted.days is None and fitted.limited_by == ("rule_health_window_days",)


@pytest.mark.parametrize(
    ("retention", "days"),
    [(36, 28), (35, 28), (34, 28), (33, 28), (32, 28), (31, 27), (30, 26), (11, 7)],
)
def test_the_retention_limits_at_its_edge(retention: int, days: int) -> None:
    # d x 24 + 72 < retention x 24: retention 32 admits 28 (744 < 768), 31 admits 27.
    fitted = fit_family_window(HistoryFamily.WAF, ns(waf_metric_retention_days=retention))
    assert fitted.days == days
    if days < 28:
        assert fitted.limited_by == ("waf_metric_retention_days",)
        assert fitted.reason is not None and "HISTORY_BACKFILL_MAX_HOURS is 72" in fitted.reason
    else:
        assert fitted.reason is None


def test_the_retention_formula_is_strict() -> None:
    for retention in range(11, 60):
        fitted = fit_family_window(HistoryFamily.WAF, ns(
            waf_metric_retention_days=retention, history_backfill_days=90,
            baseline_lookback_days=90))
        assert fitted.days is not None
        assert fitted.days * 24 + 72 < retention * 24
        assert (fitted.days + 1) * 24 + 72 >= retention * 24


def test_retention_too_short_for_seven_days_is_unavailable() -> None:
    fitted = fit_family_window(HistoryFamily.WAF, ns(waf_metric_retention_days=10))
    assert fitted.days is None  # (240 - 73) // 24 = 6
    assert fitted.reason is not None
    assert "WAF_METRIC_RETENTION_DAYS is 10 days" in fitted.reason
    assert "HISTORY_BACKFILL_MAX_HOURS is 72 hours" in fitted.reason


def test_a_retention_switched_off_does_not_limit() -> None:
    off = ns(waf_retention_enabled=False, waf_metric_retention_days=2)
    assert fit_family_window(HistoryFamily.WAF, off).days == 28
    on = ns(waf_retention_enabled=True, waf_metric_retention_days=2)
    assert fit_family_window(HistoryFamily.WAF, on).days is None
    ips_off = ns(ips_retention_enabled=False, ips_metric_retention_days=2)
    assert fit_family_window(HistoryFamily.IPS, ips_off).days == 28


def test_catchall_retention_has_no_switch() -> None:
    assert fit_family_window(HistoryFamily.CATCHALL, ns(catchall_retention_days=34)).days == 28
    assert fit_family_window(HistoryFamily.CATCHALL, ns(catchall_retention_days=30)).days == 26


def test_phase_a_retention_applies_only_when_enabled_and_set() -> None:
    # off: nothing limits, whatever the days say
    assert fit_family_window(
        HistoryFamily.PHASE_A, ns(retention_enabled=False, retention_log_source_metric_days=10)
    ).days == 28
    # on but no days set: nothing limits
    assert fit_family_window(
        HistoryFamily.PHASE_A, ns(retention_enabled=True, retention_log_source_metric_days=None)
    ).days == 28
    # on and set: retention - 3 d (retention 32 is exact: 28 x 24 + 72 < 32 x 24)
    assert fit_family_window(
        HistoryFamily.PHASE_A, ns(retention_enabled=True, retention_log_source_metric_days=32)
    ).days == 28
    fitted = fit_family_window(
        HistoryFamily.PHASE_A, ns(retention_enabled=True, retention_log_source_metric_days=20)
    )
    assert fitted.days == 16 and fitted.limited_by == ("retention_log_source_metric_days",)
    short = fit_family_window(
        HistoryFamily.PHASE_A, ns(retention_enabled=True, retention_log_source_metric_days=10)
    )
    assert short.days is None
    assert short.reason is not None and "RETENTION_LOG_SOURCE_METRIC_DAYS is 10" in short.reason


def test_max_hours_moves_the_retention_edge() -> None:
    settings = ns(waf_metric_retention_days=35, history_backfill_max_hours=168)
    # 28 x 24 + 168 = 840, not below 35 x 24 = 840: one day less
    assert fit_family_window(HistoryFamily.WAF, settings).days == 27


def test_a_tie_names_every_limiting_setting() -> None:
    fitted = fit_family_window(
        HistoryFamily.WAF, ns(history_backfill_days=14, baseline_lookback_days=14)
    )
    assert fitted.days == 14
    assert fitted.limited_by == ("history_backfill_days", "baseline_lookback_days")


def test_the_unavailable_reason_is_words_not_a_value_text() -> None:
    fitted = fit_family_window(HistoryFamily.WAF, ns(baseline_lookback_days=3))
    assert fitted.reason is not None
    assert fitted.reason.startswith("history unavailable:")
    assert len(fitted.reason) <= 255  # the scope row's `reason` column


def test_a_released_configuration_is_fitted_at_run_time_not_refused() -> None:
    settings = make(baseline_lookback_days=14, waf_collection_enabled=True,
                    ips_collection_enabled=True, catchall_collection_enabled=True,
                    search_cadence_max_searches_per_tick=8)
    assert fit_family_window(HistoryFamily.WAF, settings).days == 14
    assert fit_family_window(HistoryFamily.PHASE_A, settings).days == 14
    assert fit_family_window(HistoryFamily.IPS, settings).days == 28
    assert history_search_budget(settings, used=0) == 8


# ------------------------------------------------------------------------------ budget
@pytest.mark.parametrize(("used", "budget"), [(0, 10), (6, 10), (7, 9), (16, 0), (20, 0)])
def test_the_search_budget_after_the_live_pass(used: int, budget: int) -> None:
    assert history_search_budget(ns(), used) == budget


def test_the_search_budget_never_exceeds_the_per_tick_setting() -> None:
    assert history_search_budget(ns(history_backfill_searches_per_tick=3), 0) == 3
    assert history_search_budget(ns(search_cadence_max_searches_per_tick=4), 0) == 4


def test_a_negative_use_is_a_bug() -> None:
    with pytest.raises(ValueError, match="negative"):
        history_search_budget(ns(), -1)
