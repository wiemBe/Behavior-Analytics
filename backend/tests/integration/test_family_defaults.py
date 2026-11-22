"""Fresh-install dispatches have no Ariel work without family configuration."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.providers.mock import MockQRadarProvider
from app.services.feature_switches import effective_settings, resolve
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration


async def test_empty_family_dispatches_send_no_search(db_schema, monkeypatch):
    for key in list(os.environ):
        if key.lower() in Settings.model_fields:
            monkeypatch.delenv(key)
    # Rule matches are on by default since D-105 and have no empty-scope guard, so the fresh
    # install would send a search; this test is about the families that do have one. Pin it off.
    settings = Settings(_env_file=None, rule_match_collection_enabled=False)
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    provider = MockQRadarProvider(seed=1)
    # Guard both the collector boundary and direct Ariel creation: the mock's keyed
    # methods do not delegate to create_ariel_search as the REST provider does.
    search = AsyncMock(side_effect=AssertionError("unconfigured family sent an Ariel search"))
    for method in (
        "create_ariel_search", "get_ips_keyed", "get_identity_keyed", "get_pw_keyed",
        "get_waf_observations", "get_waf_observations_bucketed",
        "get_waf_breakdown", "get_waf_breakdown_bucketed",
        "get_rule_matches", "get_rule_matches_windowed", "get_console_event_totals",
    ):
        monkeypatch.setattr(provider, method, search)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **kw: provider)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            for qradar_id in (312, 412, 512):
                await make_log_source(
                    session, instance, qradar_id=qradar_id,
                    enabled=True, monitoring_enabled=True,
                )
            # Empty DB switch table inherits these code defaults, with default provenance.
            resolved = await resolve(session, settings)
            settings = effective_settings(settings, resolved)
            for key in (
                "waf_collection_enabled", "ips_collection_enabled",
                "identity_collection_enabled", "pw_collection_enabled", "custom_rules_enabled",
            ):
                assert resolved.values[key].enabled is True
                assert resolved.values[key].source == "default"
            await session.commit()
        sent = []
        waf = await tasks.dispatch_waf_collection(enqueue=sent.append)
        assert waf["status"] == "ok" and waf["dispatched"] == 0
        assert sent == []
        for dispatch in (
            tasks.dispatch_ips_collection,
            tasks.dispatch_identity_collection,
            tasks.dispatch_property_watch_collection,
        ):
            result = await dispatch()
            assert result["instances"] == 1
            assert len(result["results"]) == 1
            assert result["results"][0]["status"] in {"ok", "idle"}, result
            assert result["results"][0]["searches_sent"] == 0
            search.assert_not_called()
        custom = await tasks.evaluate_custom_rules()
        assert custom["status"] != "disabled"
        # Rule matches have no empty-scope guard: they stay off in this fixture (FIX-105).
        assert await tasks.collect_rule_matches() == {"status": "disabled"}
        search.assert_not_called()
    finally:
        await engine.dispose()
