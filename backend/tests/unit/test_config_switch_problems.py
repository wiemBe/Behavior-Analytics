"""Pure ``problems()`` extractors for the switch-dependent config checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import (
    Settings,
    _ips_retention_covers_the_baseline_problems,
    _pw_bounds_are_consistent_problems,
    _rule_match_repair_bounds_are_consistent_problems,
    _waf_novelty_history_is_retained_problems,
    _waf_retention_covers_the_baseline_problems,
    switch_dependent_problems,
)

SECRET_MARKER = "SECRET-MARKER-VALUE-12345"


# ---------------------------------------------------------------------------
# WAF retention
# ---------------------------------------------------------------------------


def test_waf_retention_problems_violation() -> None:
    base = Settings(
        waf_collection_enabled=False,
        waf_retention_enabled=True,
        waf_metric_retention_days=20,
    )
    violating = base.model_copy(update={"waf_collection_enabled": True})
    expected = (
        "WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS "
        f"({violating.waf_metric_retention_days} <= {violating.baseline_lookback_days})"
    )
    assert _waf_retention_covers_the_baseline_problems(violating) == [expected]
    with pytest.raises(ValidationError) as exc:
        Settings(
            waf_collection_enabled=True,
            waf_retention_enabled=True,
            waf_metric_retention_days=20,
        )
    assert expected in str(exc.value)


def test_waf_retention_problems_valid() -> None:
    settings = Settings(
        waf_collection_enabled=True,
        waf_retention_enabled=True,
        waf_metric_retention_days=35,
    )
    assert _waf_retention_covers_the_baseline_problems(settings) == []


def test_waf_retention_problems_switch_off() -> None:
    settings = Settings(
        waf_collection_enabled=False,
        waf_retention_enabled=True,
        waf_metric_retention_days=20,
    )
    assert _waf_retention_covers_the_baseline_problems(settings) == []


def test_waf_retention_problems_boundary_is_inclusive() -> None:
    base = Settings(
        waf_collection_enabled=False,
        waf_retention_enabled=True,
        waf_metric_retention_days=28,
    )
    at_limit = base.model_copy(update={"waf_collection_enabled": True})
    expected = (
        "WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS "
        f"({at_limit.waf_metric_retention_days} <= {at_limit.baseline_lookback_days})"
    )
    assert _waf_retention_covers_the_baseline_problems(at_limit) == [expected]
    above = base.model_copy(update={
        "waf_collection_enabled": True,
        "waf_metric_retention_days": base.baseline_lookback_days + 1,
    })
    assert _waf_retention_covers_the_baseline_problems(above) == []


# ---------------------------------------------------------------------------
# IPS retention
# ---------------------------------------------------------------------------


def test_ips_retention_problems_violation() -> None:
    base = Settings(
        ips_collection_enabled=False,
        ips_retention_enabled=True,
        ips_metric_retention_days=20,
    )
    violating = base.model_copy(update={"ips_collection_enabled": True})
    expected = (
        "IPS_METRIC_RETENTION_DAYS must exceed IPS_BASELINE_LOOKBACK_DAYS "
        f"({violating.ips_metric_retention_days} <= {violating.ips_baseline_lookback_days})"
    )
    assert _ips_retention_covers_the_baseline_problems(violating) == [expected]
    with pytest.raises(ValidationError) as exc:
        Settings(
            ips_collection_enabled=True,
            ips_retention_enabled=True,
            ips_metric_retention_days=20,
        )
    assert expected in str(exc.value)


def test_ips_retention_problems_valid() -> None:
    settings = Settings(
        ips_collection_enabled=True,
        ips_retention_enabled=True,
        ips_metric_retention_days=35,
    )
    assert _ips_retention_covers_the_baseline_problems(settings) == []


def test_ips_retention_problems_switch_off() -> None:
    settings = Settings(
        ips_collection_enabled=False,
        ips_retention_enabled=True,
        ips_metric_retention_days=20,
    )
    assert _ips_retention_covers_the_baseline_problems(settings) == []


def test_ips_retention_problems_boundary_is_inclusive() -> None:
    base = Settings(
        ips_collection_enabled=False,
        ips_retention_enabled=True,
        ips_metric_retention_days=28,
    )
    at_limit = base.model_copy(update={"ips_collection_enabled": True})
    expected = (
        "IPS_METRIC_RETENTION_DAYS must exceed IPS_BASELINE_LOOKBACK_DAYS "
        f"({at_limit.ips_metric_retention_days} <= {at_limit.ips_baseline_lookback_days})"
    )
    assert _ips_retention_covers_the_baseline_problems(at_limit) == [expected]
    above = base.model_copy(update={
        "ips_collection_enabled": True,
        "ips_metric_retention_days": base.ips_baseline_lookback_days + 1,
    })
    assert _ips_retention_covers_the_baseline_problems(above) == []


# ---------------------------------------------------------------------------
# Rule-match repair
# ---------------------------------------------------------------------------


def test_rule_match_repair_problems_violation() -> None:
    base = Settings(
        rule_match_collection_enabled=False,
        rule_match_repair_enabled=True,
        rule_match_repair_max_age_seconds=100,
    )
    violating = base.model_copy(update={"rule_match_collection_enabled": True})
    expected = (
        "RULE_MATCH_REPAIR_MAX_AGE_SECONDS must be at least one window "
        f"({violating.rule_match_repair_max_age_seconds} < "
        f"{violating.rule_match_window_seconds})"
    )
    assert _rule_match_repair_bounds_are_consistent_problems(violating) == [expected]
    with pytest.raises(ValidationError) as exc:
        Settings(
            rule_match_collection_enabled=True,
            rule_match_repair_enabled=True,
            rule_match_repair_max_age_seconds=100,
        )
    assert expected in str(exc.value)


def test_rule_match_repair_problems_valid() -> None:
    settings = Settings(rule_match_collection_enabled=True, rule_match_repair_enabled=True)
    assert _rule_match_repair_bounds_are_consistent_problems(settings) == []


def test_rule_match_repair_problems_switch_off() -> None:
    settings = Settings(
        rule_match_collection_enabled=False,
        rule_match_repair_enabled=True,
        rule_match_repair_max_age_seconds=100,
    )
    assert _rule_match_repair_bounds_are_consistent_problems(settings) == []


def test_rule_match_repair_problems_all_three_in_order() -> None:
    base = Settings(rule_match_collection_enabled=False)
    # No valid field values break both the age floor and the retention ceiling,
    # so model_copy is used to exercise the pure function's full message list.
    violating = base.model_copy(update={
        "rule_match_collection_enabled": True,
        "rule_match_repair_max_age_seconds": 100,
        "rule_match_retention_days": 0,
        "rule_match_repair_max_windows_per_run": 10,
    })
    first = (
        "RULE_MATCH_REPAIR_MAX_AGE_SECONDS must be at least one window "
        f"({violating.rule_match_repair_max_age_seconds} < "
        f"{violating.rule_match_window_seconds})"
    )
    second = (
        "RULE_MATCH_REPAIR_MAX_AGE_SECONDS must stay inside "
        "RULE_MATCH_RETENTION_DAYS "
        f"({violating.rule_match_repair_max_age_seconds} >= "
        f"{violating.rule_match_retention_days * 86400})"
    )
    third = (
        "RULE_MATCH_REPAIR_MAX_WINDOWS_PER_RUN must not exceed "
        "RULE_MATCH_MAX_WINDOWS_PER_RUN "
        f"({violating.rule_match_repair_max_windows_per_run} > "
        f"{violating.rule_match_max_windows_per_run})"
    )
    assert _rule_match_repair_bounds_are_consistent_problems(violating) == [
        first,
        second,
        third,
    ]
    # The validator still raises the first of a constructible pair of violations.
    with pytest.raises(ValidationError) as exc:
        Settings(
            rule_match_collection_enabled=True,
            rule_match_repair_enabled=True,
            rule_match_repair_max_age_seconds=100,
            rule_match_repair_max_windows_per_run=10,
        )
    assert first in str(exc.value)
    assert third not in str(exc.value)


def test_second_switch_off_suppresses_checks_with_collection_on() -> None:
    base = Settings(
        waf_collection_enabled=False,
        waf_retention_enabled=False,
        waf_metric_retention_days=20,
        ips_collection_enabled=False,
        ips_retention_enabled=False,
        ips_metric_retention_days=20,
        rule_match_collection_enabled=False,
        rule_match_repair_enabled=False,
        rule_match_repair_max_age_seconds=100,
    )
    settings = base.model_copy(update={
        "waf_collection_enabled": True,
        "ips_collection_enabled": True,
        "rule_match_collection_enabled": True,
    })
    assert _waf_retention_covers_the_baseline_problems(settings) == []
    assert _ips_retention_covers_the_baseline_problems(settings) == []
    assert _rule_match_repair_bounds_are_consistent_problems(settings) == []


# ---------------------------------------------------------------------------
# WAF novelty
# ---------------------------------------------------------------------------


def test_waf_novelty_problems_violation() -> None:
    base = Settings(waf_collection_enabled=False, waf_new_endpoint_history_days=40)
    violating = base.model_copy(update={"waf_collection_enabled": True})
    expected = (
        "WAF_NEW_ENDPOINT_HISTORY_DAYS must be below WAF_METRIC_RETENTION_DAYS "
        f"({violating.waf_new_endpoint_history_days} >= {violating.waf_metric_retention_days})"
    )
    assert _waf_novelty_history_is_retained_problems(violating) == [expected]
    with pytest.raises(ValidationError) as exc:
        Settings(waf_collection_enabled=True, waf_new_endpoint_history_days=40)
    assert expected in str(exc.value)


def test_waf_novelty_problems_valid() -> None:
    settings = Settings(waf_collection_enabled=True, waf_new_endpoint_history_days=7)
    assert _waf_novelty_history_is_retained_problems(settings) == []


def test_waf_novelty_problems_switch_off() -> None:
    settings = Settings(waf_collection_enabled=False, waf_new_endpoint_history_days=40)
    assert _waf_novelty_history_is_retained_problems(settings) == []


def test_waf_novelty_problems_boundary_is_inclusive() -> None:
    base = Settings(
        waf_collection_enabled=False,
        waf_metric_retention_days=35,
        waf_new_endpoint_history_days=35,
    )
    at_limit = base.model_copy(update={"waf_collection_enabled": True})
    expected = (
        "WAF_NEW_ENDPOINT_HISTORY_DAYS must be below WAF_METRIC_RETENTION_DAYS "
        f"({at_limit.waf_new_endpoint_history_days} >= {at_limit.waf_metric_retention_days})"
    )
    assert _waf_novelty_history_is_retained_problems(at_limit) == [expected]
    below = base.model_copy(update={
        "waf_collection_enabled": True,
        "waf_new_endpoint_history_days": base.waf_metric_retention_days - 1,
    })
    assert _waf_novelty_history_is_retained_problems(below) == []


# ---------------------------------------------------------------------------
# PW bounds
# ---------------------------------------------------------------------------


def test_pw_bounds_problems_violation() -> None:
    base = Settings(pw_collection_enabled=False, pw_max_rows_per_search=6000)
    violating = base.model_copy(update={"pw_collection_enabled": True})
    expected = (
        "PW_MAX_ROWS_PER_SEARCH must not exceed ARIEL_MULTI_BUCKET_MAX_ROWS "
        f"({violating.pw_max_rows_per_search} > {violating.ariel_multi_bucket_max_rows})"
    )
    assert _pw_bounds_are_consistent_problems(violating) == [expected]
    with pytest.raises(ValidationError) as exc:
        Settings(pw_collection_enabled=True, pw_max_rows_per_search=6000)
    assert expected in str(exc.value)


def test_pw_bounds_problems_valid() -> None:
    settings = Settings(pw_collection_enabled=True)
    assert _pw_bounds_are_consistent_problems(settings) == []


def test_pw_bounds_problems_switch_off() -> None:
    settings = Settings(pw_collection_enabled=False, pw_max_rows_per_search=6000)
    assert _pw_bounds_are_consistent_problems(settings) == []


def test_pw_bootstrap_hours_boundary_is_inclusive() -> None:
    base = Settings(pw_collection_enabled=False, pw_metric_retention_days=31)
    at_limit = base.model_copy(update={"pw_collection_enabled": True})
    expected = (
        "PW_BOOTSTRAP_DAYS and PW_BOOTSTRAP_MAX_HOURS must fit below "
        "PW_METRIC_RETENTION_DAYS "
        f"({at_limit.pw_bootstrap_days} * 24 + {at_limit.pw_bootstrap_max_hours} "
        f">= {at_limit.pw_metric_retention_days} * 24)"
    )
    assert _pw_bounds_are_consistent_problems(at_limit) == [expected]
    above = base.model_copy(update={
        "pw_collection_enabled": True,
        "pw_metric_retention_days": base.pw_metric_retention_days + 1,
    })
    assert _pw_bounds_are_consistent_problems(above) == []


def test_pw_bounds_problems_multiple_violations_joined() -> None:
    overrides = {
        "pw_collection_enabled": True,
        "pw_max_rows_per_search": 6000,
        "pw_max_searches_per_watch_per_tick": 50,
        "pw_max_active_values_per_watch": 5,
        "pw_baseline_lookback_days": 40,
        "pw_new_value_history_days": 40,
        "pw_bootstrap_days": 1,
    }
    settings = Settings(pw_collection_enabled=False).model_copy(update=overrides)
    expected = [
        "PW_MAX_ROWS_PER_SEARCH must not exceed ARIEL_MULTI_BUCKET_MAX_ROWS "
        f"({settings.pw_max_rows_per_search} > {settings.ariel_multi_bucket_max_rows})",
        "PW_MAX_SEARCHES_PER_WATCH_PER_TICK must not exceed PW_MAX_SEARCHES_PER_TICK "
        f"({settings.pw_max_searches_per_watch_per_tick} > "
        f"{settings.pw_max_searches_per_tick})",
        "PW_MAX_PINNED_VALUES must not exceed PW_MAX_ACTIVE_VALUES_PER_WATCH "
        f"({settings.pw_max_pinned_values} > {settings.pw_max_active_values_per_watch})",
        "PW_BASELINE_LOOKBACK_DAYS must not exceed PW_METRIC_RETENTION_DAYS "
        f"({settings.pw_baseline_lookback_days} > {settings.pw_metric_retention_days})",
        "PW_NEW_VALUE_HISTORY_DAYS must be below PW_METRIC_RETENTION_DAYS "
        f"({settings.pw_new_value_history_days} >= {settings.pw_metric_retention_days})",
        "PW_BOOTSTRAP_DAYS must not be below PW_NEW_VALUE_HISTORY_DAYS "
        f"({settings.pw_bootstrap_days} < {settings.pw_new_value_history_days})",
    ]
    assert _pw_bounds_are_consistent_problems(settings) == expected
    with pytest.raises(ValidationError) as exc:
        Settings(**overrides)
    assert "; ".join(expected) in str(exc.value)


def test_pw_min_visits_one_needs_no_week_of_lookback() -> None:
    settings = Settings(
        pw_collection_enabled=True,
        pw_baseline_min_visits=1,
        pw_baseline_lookback_days=5,
        pw_bootstrap_days=5,
        pw_new_value_history_days=1,
    )
    assert _pw_bounds_are_consistent_problems(settings) == []


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def test_switch_dependent_problems_aggregates_in_order() -> None:
    base = Settings(
        waf_collection_enabled=False,
        waf_retention_enabled=True,
        waf_metric_retention_days=20,
        ips_collection_enabled=False,
        ips_retention_enabled=True,
        ips_metric_retention_days=20,
        rule_match_collection_enabled=False,
        rule_match_repair_enabled=True,
        rule_match_repair_max_age_seconds=100,
        waf_new_endpoint_history_days=40,
        pw_collection_enabled=False,
        pw_max_rows_per_search=6000,
    )
    violating = base.model_copy(update={
        "waf_collection_enabled": True,
        "ips_collection_enabled": True,
        "rule_match_collection_enabled": True,
        "pw_collection_enabled": True,
    })
    # waf novelty switch needs waf collection on; already.
    result = switch_dependent_problems(violating)
    assert len(result) >= 5
    # order: waf retention, ips retention, repair, novelty, pw
    assert "WAF_METRIC_RETENTION_DAYS" in result[0]
    assert "IPS_METRIC_RETENTION_DAYS" in result[1]
    assert "RULE_MATCH_REPAIR_MAX_AGE_SECONDS" in result[2]
    assert "WAF_NEW_ENDPOINT_HISTORY_DAYS" in result[3]
    assert "PW_MAX_ROWS_PER_SEARCH" in result[4]


def test_no_secret_in_messages() -> None:
    settings = Settings(
        secret_key=SECRET_MARKER,
        encryption_key=SECRET_MARKER,
        smtp_password=SECRET_MARKER,
        waf_collection_enabled=False,
        waf_metric_retention_days=20,
    )
    violating = settings.model_copy(update={"waf_collection_enabled": True})
    msgs = switch_dependent_problems(violating)
    joined = " ".join(msgs)
    assert SECRET_MARKER not in joined
    # also check individual
    for fn in (
        _waf_retention_covers_the_baseline_problems,
        _ips_retention_covers_the_baseline_problems,
        _rule_match_repair_bounds_are_consistent_problems,
        _waf_novelty_history_is_retained_problems,
        _pw_bounds_are_consistent_problems,
    ):
        for m in fn(violating):
            assert SECRET_MARKER not in m