"""The runtime feature-switch allow-list and the pure overlay over ``Settings``."""

from __future__ import annotations

from app.core.config import Settings
from app.models.feature_switch import FEATURE_KEYS
from app.services.feature_switches import (
    ALERTING_REQUIRES,
    ResolvedFeatures,
    ResolvedSwitch,
    effective_settings,
)


def test_feature_keys_are_boolean_settings_fields() -> None:
    assert len(FEATURE_KEYS) == len(set(FEATURE_KEYS)) == 20
    assert set(FEATURE_KEYS) <= set(Settings.model_fields)
    assert all(Settings.model_fields[key].annotation is bool for key in FEATURE_KEYS)


def test_alerting_requires_names_only_allow_listed_switches() -> None:
    assert set(ALERTING_REQUIRES) <= set(FEATURE_KEYS)
    assert set(ALERTING_REQUIRES.values()) <= set(FEATURE_KEYS)
    # Every alerting switch of Settings is managed here and says what it needs.
    alerting = {name for name in Settings.model_fields if name.endswith("_alerting_enabled")}
    assert alerting <= set(FEATURE_KEYS)
    assert alerting <= set(ALERTING_REQUIRES)


def test_model_fields_set_separates_the_env_file_from_the_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("WAF_COLLECTION_ENABLED", raising=False)
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "CATCHALL_COLLECTION_ENABLED=false\nIPS_COLLECTION_ENABLED=false\n", encoding="utf-8"
    )

    settings = Settings(_env_file=env_file)

    # Written in the file, even at its own default: a source supplied it, so it is "set".
    assert {"catchall_collection_enabled", "ips_collection_enabled"} <= settings.model_fields_set
    # In neither the file nor the environment: the code default, so not "set".
    assert "waf_collection_enabled" not in settings.model_fields_set


def test_effective_settings_changes_only_the_feature_keys() -> None:
    base = Settings(debug=True, collection_interval_seconds=60, rule_match_window_seconds=900)
    flipped = ResolvedFeatures(
        values={key: ResolvedSwitch(not getattr(base, key), "db") for key in FEATURE_KEYS}
    )

    effective = effective_settings(base, flipped)

    changed = {
        name for name in Settings.model_fields if getattr(base, name) != getattr(effective, name)
    }
    assert changed == set(FEATURE_KEYS)
    assert (effective.debug, effective.collection_interval_seconds) == (True, 60)
    assert effective.rule_match_window_seconds == 900

    # A mapping outside the allow-list cannot reach another field.
    rogue = ResolvedFeatures(values={"debug": ResolvedSwitch(True, "db")})
    assert effective_settings(Settings(), rogue).debug is False
