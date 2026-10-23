"""Startup refusal: RULE_MATCH_RETENTION_DAYS must exceed RULE_HEALTH_WINDOW_DAYS."""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_startup_refuses_equality() -> None:
    # Default switches: no WAF, IPS or rule-match collection. The refusal is
    # not gated by any collection switch, because the rows exist regardless.
    with pytest.raises(ValueError, match="RULE_MATCH_RETENTION_DAYS must exceed"):
        Settings(rule_match_retention_days=30, rule_health_window_days=30)


def test_startup_refuses_below() -> None:
    # In bounds but at or under the reader window: the validator refuses.
    with pytest.raises(ValueError, match="RULE_MATCH_RETENTION_DAYS must exceed"):
        Settings(rule_match_retention_days=30, rule_health_window_days=60)


def test_static_floor_refuses_below_thirty() -> None:
    # Below the longest reader's default the field's `ge=30` refuses first,
    # so the validator only ever sees values the floor let through.
    with pytest.raises(ValueError, match="greater_than_equal"):
        Settings(rule_match_retention_days=29, rule_health_window_days=30)


def test_startup_names_the_window_it_compares_with() -> None:
    with pytest.raises(ValueError, match="RULE_HEALTH_WINDOW_DAYS"):
        Settings(rule_match_retention_days=45, rule_health_window_days=90)


def test_startup_accepts_strictly_above() -> None:
    settings = Settings(rule_match_retention_days=31, rule_health_window_days=30)
    assert settings.rule_match_retention_days == 31


def test_field_bounds_pin_the_longest_reader_default() -> None:
    """`ge` is the longest reader's default: `rule_health_window_days` = 30.

    A raised `RULE_HEALTH_WINDOW_DAYS` is caught by the refusal, which
    compares with the setting, not with this static floor.
    """
    field = Settings.model_fields["rule_match_retention_days"]
    bounds = {}
    for item in field.metadata:
        ge = getattr(item, "ge", None)
        le = getattr(item, "le", None)
        if ge is not None:
            bounds["ge"] = ge
        if le is not None:
            bounds["le"] = le
    assert bounds == {"ge": 30, "le": 3650}
    assert field.default == 90
