"""Family expansion and no-op/refusal guards on the offline CLI."""
import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cli import features
from app.core.config import Settings
from app.services.feature_management import FeatureError
from app.services.feature_switches import FEATURE_KEYS, ResolvedFeatures, ResolvedSwitch


def args(**kwargs):
    return argparse.Namespace(command="apply", values=[], source="installer", families="defaults",
                              alerting="defaults", only_differing=True, **kwargs)


@pytest.fixture
def context(monkeypatch):
    base = Settings(_env_file=None)
    state = ResolvedFeatures({key: ResolvedSwitch(getattr(base, key), "default")
                              for key in FEATURE_KEYS})
    session = AsyncMock()
    maker = MagicMock()
    maker.return_value.return_value.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(features, "get_sessionmaker", maker)
    monkeypatch.setattr(features, "get_settings", lambda: base)
    monkeypatch.setattr(features, "resolve", AsyncMock(return_value=state))
    change = AsyncMock()
    monkeypatch.setattr(features, "change", change)
    return maker, session, state, change


@pytest.mark.parametrize("family", features.FAMILIES)
@pytest.mark.parametrize("alerting", ["defaults", "shadow", "alerting"])
async def test_every_word_expands_to_collection_and_alerting(context, family, alerting):
    _, _, state, change = context
    namespace = args()
    namespace.families, namespace.alerting = family, alerting
    assert await features.run(namespace) == 0
    changes = change.call_args.args[2]
    for name, (keys, alert) in features.FAMILIES.items():
        for key in keys:
            assert changes.get(key, state.values[key].enabled) == (name == family)
        expected = (state.values[alert].enabled if alerting == "defaults"
                    else alerting == "alerting")
        actual = changes.get(alert, state.values[alert].enabled)
        assert actual == (expected if name == family else False)
    assert change.call_args.kwargs == {"actor": "installer"}


async def test_default_answers_never_open_database_or_audit(context):
    maker, _, _, change = context
    assert await features.run(args()) == 0
    maker.assert_not_called()
    change.assert_not_called()


async def test_defaults_shadow_changes_only_collecting_families(context):
    _, _, state, change = context
    # A disabled family may retain an inert alerting-on value; defaults must leave it alone.
    state.values["rule_alerting_enabled"] = ResolvedSwitch(True, "db")
    namespace = args()
    namespace.alerting = "shadow"
    await features.run(namespace)
    expected = {alert: False for keys, alert in features.FAMILIES.values()
                if state.values[keys[0]].enabled and state.values[alert].enabled}
    assert change.call_args.args[2] == expected
    assert "rule_alerting_enabled" not in expected


async def test_none_disables_all_mapped_keys_but_leaves_other_switches(context):
    _, _, state, change = context
    namespace = args()
    namespace.families = "none"
    await features.run(namespace)
    mapped = {key for keys, alert in features.FAMILIES.values() for key in (*keys, alert)}
    assert change.call_args.args[2] == {key: False for key in mapped if state.values[key].enabled}


async def test_only_differing_does_not_call_writer_for_unchanged_assignments(context):
    _, session, _, change = context
    namespace = args()
    namespace.families, namespace.values = None, ["waf_collection_enabled=on"]
    await features.run(namespace)
    change.assert_not_called()
    session.commit.assert_not_called()
    namespace.only_differing = False
    await features.run(namespace)
    assert change.call_args.args[2] == {"waf_collection_enabled": True}


@pytest.mark.parametrize("words", ["waf,ips", "waf ips", " waf , ips "])
def test_family_separators(words):
    assert features.family_words(words) == {"waf", "ips"}


@pytest.mark.parametrize("words", ["wrong", "waf,bad", "none,waf", "defaults ips", "", ",,,"])
async def test_bad_words_refused_before_database(context, words):
    maker, _, _, change = context
    namespace = args()
    namespace.families = words
    with pytest.raises(FeatureError, match="feature_switch_invalid_families"):
        await features.run(namespace)
    maker.assert_not_called()
    change.assert_not_called()


@pytest.mark.parametrize("argv", [
    ["apply", "--families", "wrong"], ["apply", "--families", "none waf"],
    ["apply", "--families", "defaults", "--alerting", "bad"], ["apply"],
    ["apply", "geo_collection_enabled=on", "--families", "none"],
    ["apply", "--alerting", "shadow"],
    ["apply", "waf_collection_enabled=on", "--alerting", "shadow"],
    ["apply", "unknown=on", "--only-differing"],
])
def test_cli_refuses_with_exit_two_and_no_database(context, monkeypatch, argv):
    maker, _, _, change = context
    monkeypatch.setattr("sys.argv", ["features", *argv])
    try:
        result = features.main()
    except SystemExit as exc:
        result = exc.code
    assert result == 2
    maker.assert_not_called()
    change.assert_not_called()


async def test_unreadable_does_not_apply_forced_off_values(context, monkeypatch):
    _, _, _, change = context
    monkeypatch.setattr(features, "resolve", AsyncMock(return_value=ResolvedFeatures(
        {}, reason="feature_switch_unreadable")))
    namespace = args()
    namespace.families = "waf"
    with pytest.raises(FeatureError, match="feature_switch_unreadable"):
        await features.run(namespace)
    change.assert_not_called()


def test_defaults_list_is_read_from_python_code(context, monkeypatch, capsys):
    maker, _, _, _ = context
    monkeypatch.setattr("sys.argv", ["features", "defaults"])
    assert features.main() == 0
    expected = [name for name, (keys, _) in features.FAMILIES.items()
                if Settings.model_fields[keys[0]].default]
    assert capsys.readouterr().out == "Families on by code default: " + ", ".join(expected) + "\n"
    maker.assert_not_called()


@pytest.mark.parametrize("family,collection,alert", [
    ("waf", "waf_collection_enabled", "waf_alerting_enabled"),
    ("ips", "ips_collection_enabled", "ips_alerting_enabled"),
    ("rule-match", "rule_match_collection_enabled", "rule_alerting_enabled"),
    ("property-watch", "pw_collection_enabled", "pw_alerting_enabled"),
    ("identity", "identity_collection_enabled", "identity_alerting_enabled"),
    ("custom-rules", "custom_rules_enabled", "custom_rules_alerting_enabled"),
])
async def test_installer_words_target_the_named_feature_keys(context, family, collection, alert):
    _, _, _, change = context
    namespace = args()
    namespace.families, namespace.alerting, namespace.only_differing = family, "alerting", False
    await features.run(namespace)
    changes = change.call_args.args[2]
    enabled = {key for key, value in changes.items() if value}
    expected = {collection, alert}
    if family == "waf":
        expected.update({"waf_outcome_collection_enabled", "waf_client_collection_enabled"})
    assert enabled == expected
