"""IPS collection ticks every minute and sends nothing before its cadence slot."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.models.enums import SearchFamily
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


def test_dispatch_ticks_every_minute() -> None:
    assert celery_app.conf.beat_schedule["dispatch-ips-collection"]["schedule"] == 60.0


@pytest.mark.asyncio
async def test_disabled_tick_does_not_open_a_database_session(monkeypatch) -> None:
    sessionmaker = Mock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(ips_collection_enabled=False))
    monkeypatch.setattr(tasks, "get_sessionmaker", sessionmaker)

    assert await tasks.dispatch_ips_collection() == {
        "status": "disabled",
        "instances": 0,
        "results": [],
    }
    sessionmaker.assert_not_called()


@pytest.mark.asyncio
async def test_early_tick_does_not_build_a_provider_or_plan_sources(monkeypatch) -> None:
    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family is SearchFamily.IPS_COLLECTION
            return None

    for_each = AsyncMock()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: Mock(ips_collection_enabled=True),
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: lambda: _Context())
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)

    assert await tasks.dispatch_ips_collection() == {
        "status": "not_due",
        "family": "IPS_COLLECTION",
    }
    for_each.assert_not_awaited()
