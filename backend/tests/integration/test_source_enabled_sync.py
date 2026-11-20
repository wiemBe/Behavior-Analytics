"""Source-enabled sync and the operator's worker entry with resolved switches (JDBC-1 P1)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.models.base import utcnow
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource
from app.providers.dto import LogSourceDTO
from app.providers.factory import build_provider_for_instance
from app.providers.mock import MockQRadarProvider
from app.services.feature_switches import UNREADABLE_REASON
from app.services.inventory_sync import InventorySyncService
from app.workers import tasks

from .factories import make_instance, make_log_source

pytestmark = pytest.mark.integration


class SourceProvider(MockQRadarProvider):
    def __init__(self, value):
        super().__init__()
        self.value = value

    async def list_log_sources(self):
        return [LogSourceDTO(qradar_id=1, name="source", enabled=self.value)]


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [False, True])
async def test_none_keeps_stored_value(db_session, stored):
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, enabled=stored)
    settings = Settings(_env_file=None, qradar_source_pause_enabled=True)
    service = InventorySyncService(db_session, SourceProvider(None), settings=settings)
    await service.sync(instance)
    await db_session.refresh(source)
    assert source.enabled is stored


@pytest.mark.asyncio
async def test_new_none_conservative_false(db_session):
    instance = await make_instance(db_session)
    settings = Settings(_env_file=None, qradar_source_pause_enabled=True)
    provider = SourceProvider(None)
    service = InventorySyncService(db_session, provider, settings=settings)
    await service.sync(instance)
    source = await db_session.scalar(select(LogSource).where(LogSource.instance_id == instance.id))
    assert source.enabled is False
    provider.value = True
    await service.sync(instance)
    await db_session.refresh(source)
    assert source.enabled is True
    provider.value = False
    await service.sync(instance)
    await db_session.refresh(source)
    assert source.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("value,expected", [(None, True), (False, False), (True, True)])
async def test_policy_off_preserves_existing_behavior(db_session, existing, value, expected):
    instance = await make_instance(db_session)
    if existing:
        await make_log_source(db_session, instance, qradar_id=1, enabled=False)
    settings = Settings(_env_file=None, qradar_source_pause_enabled=False)
    await InventorySyncService(db_session, SourceProvider(value), settings=settings).sync(instance)
    source = await db_session.scalar(select(LogSource).where(LogSource.instance_id == instance.id))
    await db_session.refresh(source)
    assert source.enabled is expected


@pytest.mark.parametrize("row,env", [(True, False), (False, True), (None, False), (None, True)])
@pytest.mark.parametrize("kind", ["rest", "mcp"])
def test_worker_passes_resolved_switch(db_schema, monkeypatch, row, env, kind):
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    snapshots = []
    statements = []
    providers = []
    requests = []
    base = Settings(_env_file=None, qradar_source_pause_enabled=env, feature_switches_db=True)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "dispose_engine", AsyncMock())

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    async def prepare():
        async with maker() as session:
            instance = await make_instance(session)
            instance.provider_kind = kind
            instance.console_host = "https://qradar.test"
            instance.sec_token = "test"
            if row is not None:
                session.add(FeatureSwitch(
                    key="qradar_source_pause_enabled", enabled=row,
                    updated_at=utcnow(), updated_by="test",
                ))
            await session.commit()
            return instance.name

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/system/about"):
            return httpx.Response(200, json={"external_version": "test"})
        if request.url.path.endswith("/log_source_types") or request.url.path.endswith(
            "/log_source_groups"
        ):
            return httpx.Response(200, json=[])
        assert request.url.path.endswith("/log_sources")
        return httpx.Response(200, json=[{"id": 1, "name": "source"}])

    def build(instance, settings=None, **kwargs):
        snapshots.append(settings)
        provider = build_provider_for_instance(instance, settings)
        providers.append(provider)
        if kind == "rest":
            original = provider._client
            provider._client = httpx.AsyncClient(
                base_url="https://qradar.test/api/", transport=httpx.MockTransport(handler)
            )
            # The unused factory client has made no requests; close it on this worker loop.
            originals.append(original)
        else:
            async def tool(name, arguments=None):
                assert name in {"get_system_info", "list_log_source_types",
                                "list_log_source_groups", "list_log_sources"}
                if name == "get_system_info":
                    return {"external_version": "test"}
                return [{"id": 1, "name": "source"}] if name == "list_log_sources" else []
            monkeypatch.setattr(provider, "call_tool", tool)
        return provider

    originals = []
    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    real_sync = InventorySyncService.sync

    async def sync(service, *args, **kwargs):
        assert service.settings is snapshots[-1]
        return await real_sync(service, *args, **kwargs)

    monkeypatch.setattr(InventorySyncService, "sync", sync)
    try:
        name = asyncio.run(prepare())
        statements.clear()
        result = tasks.sync_log_sources_task.run(name)
        assert result["results"][0]["status"] == "ok", result
        policy = env if row is None else row
        assert len(snapshots) == len(providers) == 1
        assert snapshots[0].qradar_source_pause_enabled is policy
        assert providers[0]._strict_source_enabled is policy
        assert sum("FROM feature_switch" in statement for statement in statements) == 1

        async def verify():
            async with maker() as session:
                source = await session.scalar(select(LogSource))
                assert source.enabled is (not policy)

        asyncio.run(verify())
        assert bool(requests) is (kind == "rest")
    finally:
        async def close():
            for client in originals:
                await client.aclose()
            await engine.dispose()
        asyncio.run(close())


@pytest.mark.asyncio
async def test_unreadable_switch_is_false_and_worker_reports_disabled(db_schema, monkeypatch):
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    base = Settings(_env_file=None, qradar_source_pause_enabled=True)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    build = AsyncMock(side_effect=AssertionError("a disabled run must not build a provider"))
    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE feature_switch"))
        settings, reason = await tasks._effective_settings("sync_log_sources")
        assert settings.qradar_source_pause_enabled is False
        assert reason == UNREADABLE_REASON
        provider = build_provider_for_instance(
            # Only the provider's legacy mode matters here; no request is sent.
            SimpleNamespace(provider_kind="mcp", sec_token="test", mcp_base_url=None), settings,
        )
        try:
            assert provider._to_log_source({"id": 1, "name": "source"}).enabled is True
        finally:
            await provider.aclose()
        assert await tasks.sync_log_sources() == {"status": "disabled", "reason": reason}
        build.assert_not_called()
    finally:
        await engine.dispose()
