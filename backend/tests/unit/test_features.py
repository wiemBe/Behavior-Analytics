"""Pure load arithmetic and shared refusal paths, with no provider involved."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.models.search_cadence import SearchCadenceSetting
from app.services import feature_management as writes
from app.services.feature_overview import ACCESS, FAMILIES, estimate
from app.services.feature_switches import FEATURE_KEYS


def test_catalog_covers_every_switch_once_and_access_ids_exist():
    from app.providers.access_catalog import CATALOG_BY_ID

    keys = [key for _, _, keys in FAMILIES for key in keys]
    assert sorted(keys) == sorted(FEATURE_KEYS)
    assert all(key in CATALOG_BY_ID for keys in ACCESS.values() for key in keys)


@pytest.mark.parametrize("family,expected", [
    ("waf", 480), ("ips", 240), ("identity", 240), ("property_watch", 320),
    ("qradar_health", 16), ("geo", 240), ("catchall", 240), ("volume", 240),
    ("rule_match", 240),
])
def test_load_bounds_include_configured_budget_and_count(family, expected):
    base = Settings(_env_file=None).model_copy(update={"search_cadence_max_searches_per_tick": 30,
                                                       "pw_max_searches_per_tick": 40})
    cadence = SearchCadenceSetting(family="WAF_COLLECTION", cadence_seconds=900,
                                   updated_at=datetime.now(UTC))
    counts = {name: {"enabled": 2, "validated": 1, "total": 50}
              for name in ("waf", "ips", "identity", "property_watch")}
    result = estimate(family, cadence, counts, 2, base)
    assert result["upper_bound_searches_per_hour"] == expected
    assert result["count"] == 2
    assert result["enabled_consoles"] == 2
    assert result["reason"] is None
    cadence.cadence_seconds = 1800
    result = estimate(family, cadence, counts, 2, base)
    assert result["upper_bound_searches_per_hour"] <= expected


@pytest.mark.parametrize("family,_,keys", FAMILIES)
def test_missing_cadence_is_not_zero(family, _, keys):
    result = estimate(family, None, {}, 2, Settings(_env_file=None))
    assert result["upper_bound_searches_per_hour"] is None
    assert result["reason"] == "NOT_COLLECTED"


@pytest.mark.parametrize("family", ["explanations", "custom_rules", "inventory"])
def test_non_scheduled_work_is_not_estimated(family):
    cadence = SearchCadenceSetting(cadence_seconds=900)
    assert estimate(family, cadence, {}, 2, Settings(_env_file=None))["reason"] == "NOT_COLLECTED"


async def test_no_database_kill_switch_write(monkeypatch):
    base = Settings(_env_file=None, feature_switches_db=False)
    session = AsyncMock()
    with pytest.raises(writes.FeatureError, match="feature_switch_database_disabled") as caught:
        await writes.change(session, base, {"geo_collection_enabled": False}, actor="cli")
    assert caught.value.status == 409
    session.get.assert_not_called()


async def test_lock_connection_failure_is_fixed_503():
    session = AsyncMock()
    session.execute.side_effect = OperationalError("secret", {}, Exception("secret"))
    with pytest.raises(writes.FeatureError, match="feature_switch_unreadable") as caught:
        await writes.change(session, Settings(_env_file=None),
                            {"geo_collection_enabled": False}, actor="cli")
    assert caught.value.status == 503
    assert "secret" not in str(caught.value)


async def test_unknown_key_is_refused_before_database():
    session = AsyncMock()
    with pytest.raises(writes.FeatureError, match="feature_switch_not_found"):
        await writes.change(session, Settings(_env_file=None), {"unknown": True}, actor="cli")
    session.execute.assert_not_called()


@pytest.fixture
def write_context(monkeypatch):
    from app.services.feature_switches import ResolvedFeatures, ResolvedSwitch

    base = Settings(_env_file=None).model_copy(update=dict.fromkeys(FEATURE_KEYS, False))
    session = AsyncMock()
    session.get.return_value = None
    audit = AsyncMock()

    async def resolved(session, settings):
        return ResolvedFeatures({key: ResolvedSwitch(getattr(settings, key), "default")
                                 for key in FEATURE_KEYS})

    monkeypatch.setattr(writes, "resolve", resolved)
    monkeypatch.setattr(writes, "record_audit", audit)
    return session, base, audit


@pytest.mark.parametrize("key,required", writes.ALERTING_REQUIRES.items())
async def test_shared_dependency_guard(write_context, key, required):
    session, base, audit = write_context
    with pytest.raises(writes.FeatureError, match="feature_switch_dependency"):
        await writes.change(session, base, {key: True}, actor="cli")
    audit.assert_not_called()


@pytest.mark.parametrize("updates,key", [
    ({"waf_retention_enabled": True, "waf_metric_retention_days": 1}, "waf_collection_enabled"),
    ({"ips_retention_enabled": True, "ips_metric_retention_days": 1}, "ips_collection_enabled"),
    ({"rule_match_repair_enabled": True, "rule_match_repair_max_age_seconds": 1},
     "rule_match_collection_enabled"),
    ({"waf_new_endpoint_history_days": 9999}, "waf_collection_enabled"),
    ({"pw_max_rows_per_search": 999999}, "pw_collection_enabled"),
])
async def test_shared_settings_guard(write_context, updates, key):
    session, base, audit = write_context
    with pytest.raises(writes.FeatureError, match="feature_switch_settings_invalid"):
        await writes.change(session, base.model_copy(update=updates), {key: True}, actor="cli")
    audit.assert_not_called()


async def test_shared_noop_guards(write_context):
    session, base, audit = write_context
    await writes.change(session, base, {"geo_collection_enabled": False}, actor="cli")
    await writes.change(session, base, {"geo_collection_enabled": None}, actor="cli")
    audit.assert_not_called()
    session.delete.assert_not_called()


async def test_shared_unreadable_guard(write_context, monkeypatch):
    from app.services.feature_switches import ResolvedFeatures, ResolvedSwitch

    session, base, audit = write_context
    failed = ResolvedFeatures(dict.fromkeys(FEATURE_KEYS, ResolvedSwitch(False, "unreadable")),
                              reason="feature_switch_unreadable")
    monkeypatch.setattr(writes, "resolve", AsyncMock(return_value=failed))
    with pytest.raises(writes.FeatureError, match="feature_switch_unreadable"):
        await writes.change(session, base, {"geo_collection_enabled": True}, actor="cli")
    audit.assert_not_called()


@pytest.mark.parametrize("words", [["geo_collection_enabled=on", "ips_collection_enabled=bad"],
                                   ["geo_collection_enabled=on", "geo_collection_enabled=off"],
                                   ["geo_collection_enabled"]])
async def test_apply_refuses_before_database(monkeypatch, words):
    import argparse

    from app.cli import features

    def forbidden():
        raise AssertionError("invalid apply reached the database")

    monkeypatch.setattr(features, "get_sessionmaker", forbidden)
    with pytest.raises(writes.FeatureError, match="feature_switch_invalid_assignment"):
        await features.run(argparse.Namespace(command="apply", values=words, source="cli"))
