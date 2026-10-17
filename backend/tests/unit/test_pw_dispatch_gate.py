"""Property-watch collection ticks every minute and sends nothing before its cadence slot."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.anomaly.capabilities import _PW_TASK
from app.core.config import Settings
from app.models.enums import SearchFamily
from app.providers.factory import build_provider_for_instance
from app.workers import tasks
from app.workers.celery_app import celery_app


class _Session:
    async def commit(self) -> None:
        pass


class _Context:
    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, *args: object) -> None:
        pass


def test_the_beat_entries_exist_and_name_registered_tasks() -> None:
    schedule = celery_app.conf.beat_schedule
    dispatch = schedule["dispatch-property-watch-collection"]
    assert dispatch["task"] == "dispatch_property_watch_collection" == _PW_TASK
    assert dispatch["schedule"] == 60.0
    retention = schedule["apply-property-watch-retention"]
    assert retention["task"] == "apply_property_watch_retention"
    assert retention["schedule"] == float(Settings().pw_retention_interval_seconds)
    assert "dispatch_property_watch_collection" in celery_app.tasks
    assert "apply_property_watch_retention" in celery_app.tasks


@pytest.mark.asyncio
async def test_disabled_tick_does_not_open_a_database_session(monkeypatch) -> None:
    sessionmaker = Mock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(pw_collection_enabled=False))
    monkeypatch.setattr(tasks, "get_sessionmaker", sessionmaker)

    assert await tasks.dispatch_property_watch_collection() == {
        "status": "disabled",
        "instances": 0,
        "results": [],
    }
    sessionmaker.assert_not_called()


@pytest.mark.asyncio
async def test_early_tick_does_not_build_a_provider_or_plan_watches(monkeypatch) -> None:
    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family is SearchFamily.PROPERTY_WATCH
            return None

    for_each = AsyncMock()
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(pw_collection_enabled=True))
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: lambda: _Context())
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)

    assert await tasks.dispatch_property_watch_collection() == {
        "status": "not_due",
        "family": "PROPERTY_WATCH",
    }
    for_each.assert_not_awaited()


def test_the_provider_factory_passes_the_source_cap_to_every_provider() -> None:
    settings = Settings(pw_max_sources_per_search=7)
    mock = Mock(provider_kind="mock")
    assert build_provider_for_instance(mock, settings)._pw_max_sources_per_search == 7  # type: ignore[attr-defined]
    rest = Mock(
        provider_kind="rest", sec_token="t", console_host="https://qradar.example",
        api_version="19.0", verify_ssl=True, ca_bundle_path=None,
    )
    assert build_provider_for_instance(rest, settings)._pw_max_sources_per_search == 7  # type: ignore[attr-defined]


def test_the_process_wide_mock_provider_gets_the_source_cap_too() -> None:
    from app.core.config import ProviderKind
    from app.providers.factory import build_provider

    settings = Settings(qradar_provider=ProviderKind.MOCK, pw_max_sources_per_search=9)
    assert build_provider(settings)._pw_max_sources_per_search == 9  # type: ignore[attr-defined]


def test_the_process_wide_rest_provider_gets_the_source_cap_too() -> None:
    from app.core.config import ProviderKind
    from app.providers.factory import build_provider

    settings = Settings(
        qradar_provider=ProviderKind.REST,
        qradar_host="https://qradar.example",
        qradar_sec_token="t",
        pw_max_sources_per_search=11,
    )
    assert build_provider(settings)._pw_max_sources_per_search == 11  # type: ignore[attr-defined]
