"""Startup refusal: the rule-match repair bounds must be usable and safe."""

from __future__ import annotations

import pytest

from app.core.config import Settings

#: The repair only runs with the family on, so the validator only bites then.
ON = {"rule_match_collection_enabled": True}


def test_default_repair_settings_boot_with_collection_on() -> None:
    settings = Settings(**ON)

    assert settings.rule_match_repair_enabled is True
    assert settings.rule_match_repair_max_age_seconds == 86_400
    assert settings.rule_match_repair_max_windows_per_run == 6
    assert settings.rule_match_repair_max_attempts == 3


def test_the_bounds_are_not_enforced_while_the_family_is_off() -> None:
    """Collection off: the repair never runs, so its bounds cannot bite."""
    settings = Settings(
        rule_match_window_seconds=3600, rule_match_repair_max_age_seconds=120
    )
    assert settings.rule_match_repair_max_age_seconds == 120


def test_the_bounds_are_not_enforced_while_the_repair_is_off() -> None:
    settings = Settings(
        rule_match_collection_enabled=True,
        rule_match_repair_enabled=False,
        rule_match_window_seconds=3600,
        rule_match_repair_max_age_seconds=120,
    )
    assert settings.rule_match_repair_max_age_seconds == 120


def test_startup_refuses_an_age_below_one_window() -> None:
    with pytest.raises(ValueError, match="RULE_MATCH_REPAIR_MAX_AGE_SECONDS"):
        Settings(**ON, rule_match_window_seconds=3600, rule_match_repair_max_age_seconds=3599)


def test_one_window_exactly_is_accepted() -> None:
    settings = Settings(
        **ON, rule_match_window_seconds=3600, rule_match_repair_max_age_seconds=3600
    )
    assert settings.rule_match_repair_max_age_seconds == 3600


def test_startup_refuses_an_age_reaching_retention() -> None:
    """Re-reading at or beyond retention could read rotated-out data as empty.

    The field bounds keep this unreachable today (`le=604_800` vs `ge=30` days,
    see the test below), so the validator is probed directly on a model built
    without validation — it guards a future ceiling raise the way the
    neighbouring retention validator guards a floor change.
    """
    settings = Settings.model_construct(
        rule_match_collection_enabled=True,
        rule_match_repair_enabled=True,
        rule_match_retention_days=30,
        rule_match_repair_max_age_seconds=30 * 86400,
    )
    with pytest.raises(ValueError, match="RULE_MATCH_REPAIR_MAX_AGE_SECONDS"):
        settings._rule_match_repair_bounds_are_consistent()


def test_an_age_inside_retention_is_accepted() -> None:
    settings = Settings(
        **ON, rule_match_repair_max_age_seconds=604_800
    )  # 7 days, the ceiling; retention is 90 by default
    assert settings.rule_match_repair_max_age_seconds == 604_800


def test_the_field_bounds_keep_the_age_inside_retention_whatever_is_set() -> None:
    """604,800 s is below 30 d (the retention floor) x 86,400 s: the ceiling
    cannot reach retention, so the validator above is defence for a future
    bound change, not a reachable refusal."""
    age = Settings.model_fields["rule_match_repair_max_age_seconds"]
    retention = Settings.model_fields["rule_match_retention_days"]
    age_ceiling = next(m.le for m in age.metadata if getattr(m, "le", None) is not None)
    retention_floor = next(
        m.ge for m in retention.metadata if getattr(m, "ge", None) is not None
    )
    assert age_ceiling == 604_800
    assert retention_floor == 30
    assert age_ceiling < retention_floor * 86400


def test_startup_refuses_a_per_run_cap_above_the_live_one() -> None:
    with pytest.raises(ValueError, match="RULE_MATCH_REPAIR_MAX_WINDOWS_PER_RUN"):
        Settings(**ON, rule_match_repair_max_windows_per_run=7)


def test_the_static_ceiling_pins_seven_days() -> None:
    """The field's own ceiling is the design maximum: 604,800 s (7 days)."""
    with pytest.raises(ValueError, match="less_than_equal"):
        Settings(**ON, rule_match_repair_max_age_seconds=604_801)


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("rule_match_repair_max_age_seconds", 59, "greater_than_equal"),
        ("rule_match_repair_max_windows_per_run", 0, "greater_than_equal"),
        ("rule_match_repair_max_windows_per_run", 169, "less_than_equal"),
        ("rule_match_repair_max_attempts", 0, "greater_than_equal"),
        ("rule_match_repair_max_attempts", 21, "less_than_equal"),
    ],
)
def test_each_static_repair_bound_is_enforced(name: str, value: int, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        Settings(**{name: value})
