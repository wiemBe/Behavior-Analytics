"""Strict source-enabled transport and the default-off compatibility path (JDBC-1 P1)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.dto import LogSourceDTO
from app.providers.factory import build_provider, build_provider_for_instance
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.services import inventory_sync
from app.workers import tasks

ROWS = [{}, *[{"enabled": value} for value in (True, False, None, 0, 1, "true", "false")]]


@pytest.mark.parametrize("provider_class", [QRadarRestProvider, QRadarMCPProvider])
@pytest.mark.parametrize("values", ROWS)
async def test_on_accepts_only_json_bool(provider_class, values):
    provider = provider_class(
        base_url="https://qradar.test", sec_token="test", strict_source_enabled=True
    )
    try:
        dto = provider._to_log_source({"id": 1, "name": "source", **values})
        expected = values.get("enabled") if isinstance(values.get("enabled"), bool) else None
        assert dto is not None
        assert dto.enabled is expected
    finally:
        await provider.aclose()


@pytest.mark.parametrize("provider_class", [QRadarRestProvider, QRadarMCPProvider])
@pytest.mark.parametrize("values", ROWS)
async def test_off_mapper_keeps_old_coercion(provider_class, values):
    provider = provider_class(base_url="https://qradar.test", sec_token="test")
    try:
        dto = provider._to_log_source({"id": 1, "name": "source", **values})
        assert dto is not None
        assert dto.enabled is bool(values.get("enabled", True))
    finally:
        await provider.aclose()


@pytest.mark.parametrize("value", [0, 1, "true", "false", "", [], {}])
def test_dto_rejects_coercion(value):
    with pytest.raises(ValidationError):
        LogSourceDTO(qradar_id=1, name="source", enabled=value)


@pytest.mark.parametrize("value", [True, False, None])
def test_dto_preserves_optional_boolean(value):
    assert LogSourceDTO(qradar_id=1, name="source", enabled=value).enabled is value
    assert LogSourceDTO(qradar_id=1, name="source").enabled is None


@pytest.mark.parametrize("kind", ["rest", "mcp"])
@pytest.mark.parametrize("per_instance", [False, True])
@pytest.mark.parametrize("policy", [False, True])
async def test_constructors_explicit_old_defaults(kind, per_instance, policy):
    overrides = {"qradar_source_pause_enabled": True} if policy else {}
    settings = Settings(
        _env_file=None, qradar_provider=kind, qradar_host="https://qradar.test",
        qradar_sec_token="test", **overrides,
    )
    instance = SimpleNamespace(
        id=None, name="test", provider_kind=kind, console_host="https://qradar.test",
        sec_token="test", api_version="20.0", verify_ssl=True, ca_bundle_path=None,
        mcp_base_url=None,
    )
    provider = (
        build_provider_for_instance(instance, settings)
        if per_instance else build_provider(settings)
    )
    try:
        dto = provider._to_log_source({"id": 1, "name": "source"})
        assert dto.enabled is (None if policy else True)
    finally:
        await provider.aclose()
    sources = await MockQRadarProvider().list_log_sources()
    assert sources
    assert all(isinstance(source.enabled, bool) for source in sources)
    assert all(source.enabled is True for source in sources if source.status == "SUCCESS")
    assert any(source.enabled is False for source in sources)  # the existing disabled fixture


@pytest.mark.parametrize("value,expected", [(None, True), (False, False), (True, True)])
async def test_policy_off_preserves_existing_behavior(monkeypatch, value, expected):
    assert await _sync_enabled(monkeypatch, value=value, policy=False) is expected


@pytest.mark.parametrize("existing,stored,value,expected", [
    (True, False, None, False), (True, True, None, True), (False, None, None, False),
    (True, True, False, False), (True, False, True, True),
    (False, None, True, True), (False, None, False, False),
])
async def test_on_sync_preserves_unknown_and_copies_boolean(
    monkeypatch, existing, stored, value, expected
):
    assert await _sync_enabled(
        monkeypatch, value=value, policy=True, existing=existing, stored=stored
    ) is expected


async def _sync_enabled(monkeypatch, *, value, policy, existing=True, stored=False):
    provider = MockQRadarProvider()
    monkeypatch.setattr(provider, "list_log_source_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(provider, "list_log_source_groups", AsyncMock(return_value=[]))
    monkeypatch.setattr(provider, "list_log_sources", AsyncMock(return_value=[
        LogSourceDTO(qradar_id=1, name="source", enabled=value)
    ]))
    session = SimpleNamespace(flush=AsyncMock(), add=Mock())
    service = inventory_sync.InventorySyncService(
        session, provider, settings=Settings(
            _env_file=None, qradar_source_pause_enabled=policy,
            inventory_new_source_alerts_enabled=False,
        )
    )
    rows = {1: LogSource(qradar_id=1, name="stored", enabled=stored)} if existing else {}
    monkeypatch.setattr(service.repo, "by_qradar_id", AsyncMock(return_value=rows))
    monkeypatch.setattr(service, "_record_mentions", AsyncMock())
    monkeypatch.setattr(inventory_sync, "classify_sources", AsyncMock(return_value={}))
    await service.sync(QRadarInstance(name="test", console_host="mock", provider_kind="mock"))
    return rows[1].enabled


@pytest.mark.parametrize("policy", [False, True])
async def test_worker_passes_one_snapshot_to_provider_and_sync(monkeypatch, policy):
    settings = Settings(_env_file=None, qradar_source_pause_enabled=policy)
    resolve = AsyncMock(return_value=(settings, None))
    monkeypatch.setattr(tasks, "_effective_settings", resolve)
    instance = QRadarInstance(
        name="test", console_host="https://qradar.test", provider_kind="mcp", sec_token="test"
    )
    session = MagicMock()
    session.__aenter__.return_value = session
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [instance]))
    session.commit = AsyncMock()
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: lambda: session)
    providers = []

    def build(row, *, settings=None, **kwargs):
        assert settings is snapshot
        provider = build_provider_for_instance(row, settings)
        assert provider._strict_source_enabled is policy
        providers.append(provider)
        return provider

    snapshot = settings
    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    report = SimpleNamespace(
        skipped_locked=False, status="COMPLETE", log_sources_seen=1, created=1, updated=0,
        pages_done=1, pages_failed=0, failed_page=None, failure_kind=None,
        duration_ms=0, error=None,
    )
    sync = AsyncMock(return_value=report)

    def collector(session_arg, provider, *, settings):
        assert session_arg is session
        assert provider is providers[-1]
        assert settings is snapshot
        return SimpleNamespace(sync=sync)

    monkeypatch.setattr(tasks, "LogSourceCollector", collector)
    result = await tasks.sync_log_sources("test")
    assert result["results"][0]["status"] == "ok"
    resolve.assert_awaited_once_with("sync_log_sources")
    sync.assert_awaited_once_with(instance)
    assert len(providers) == 1
    assert providers[0]._client.is_closed
