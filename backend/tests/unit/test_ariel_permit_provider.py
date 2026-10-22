"""Provider lifecycle enforcement for the platform-wide Ariel permit."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import ProviderKind, Settings
from app.models.instance import QRadarInstance
from app.providers.factory import build_provider, build_provider_for_instance
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import MalformedUpstreamResponse, QRadarRestProvider
from app.services.ariel_permit import ArielPermitUnavailable

BASE_URL = "https://qradar.example.internal"
WINDOW_START = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)
LIFECYCLES = (
    "get_log_source_metrics",
    "get_log_source_metrics_bucketed",
    "observe_property_rejection",
    "_run_bounded_aggregate",
)


class FakeBudget:
    wait_seconds = 0.25
    instance_limit = 3
    global_limit = 6

    def __init__(self, *, grant: bool) -> None:
        self.grant = grant
        self.enters = 0
        self.exits = 0

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[object | None]:
        self.enters += 1
        try:
            yield object() if self.grant else None
        finally:
            self.exits += 1


def make_provider(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
    budget: FakeBudget,
) -> QRadarRestProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL + "/api",
        headers={"SEC": "test-token", "Version": "20.0", "Accept": "application/json"},
    )
    return QRadarRestProvider(
        base_url=BASE_URL,
        sec_token="test-token",
        client=client,
        permit=budget,  # type: ignore[arg-type]
    )


async def run_lifecycle(provider: QRadarRestProvider, lifecycle: str) -> Any:
    if lifecycle == "get_log_source_metrics":
        return await provider.get_log_source_metrics(WINDOW_START, WINDOW_END)
    if lifecycle == "get_log_source_metrics_bucketed":
        return await provider.get_log_source_metrics_bucketed(
            WINDOW_START,
            WINDOW_END,
            bucket_seconds=300,
            max_rows=10,
        )
    if lifecycle == "observe_property_rejection":
        return await provider.observe_property_rejection(
            qradar_log_source_id=7,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            property_names=["HTTP Method", "HTTP Response Code"],
        )
    if lifecycle == "_run_bounded_aggregate":
        return await provider._run_bounded_aggregate(
            "SELECT COUNT(*) AS count FROM events LAST 5 MINUTES",
            max_rows=10,
        )
    raise AssertionError(f"unknown lifecycle {lifecycle}")


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", LIFECYCLES)
async def test_refusal_raises_before_any_ariel_create(lifecycle: str) -> None:
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/ariel/searches"):
            posts.append(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    budget = FakeBudget(grant=False)
    provider = make_provider(handler, budget)
    try:
        with pytest.raises(ArielPermitUnavailable) as raised:
            await run_lifecycle(provider, lifecycle)
    finally:
        await provider.aclose()

    assert posts == []
    assert (budget.enters, budget.exits) == (1, 1)
    assert raised.value.waited_seconds == budget.wait_seconds
    assert "SELECT" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", LIFECYCLES)
async def test_grant_holds_exactly_one_permit_and_preserves_result(lifecycle: str) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            posts += 1
            return httpx.Response(201, json={"search_id": "search-1", "status": "WAIT"})
        if path.endswith("/ariel/searches/search-1/results"):
            rows = [{"value": "ok"}] if lifecycle == "_run_bounded_aggregate" else []
            return httpx.Response(200, json={"events": rows})
        if path.endswith("/ariel/searches/search-1"):
            return httpx.Response(
                200,
                json={"status": "COMPLETED", "progress": 100, "record_count": 1},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    budget = FakeBudget(grant=True)
    provider = make_provider(handler, budget)
    try:
        result = await run_lifecycle(provider, lifecycle)
    finally:
        await provider.aclose()

    assert posts == (2 if lifecycle == "observe_property_rejection" else 1)
    assert (budget.enters, budget.exits) == (1, 1)
    if lifecycle == "get_log_source_metrics":
        assert result == []
    elif lifecycle == "get_log_source_metrics_bucketed":
        assert result.buckets == {WINDOW_START: []} and result.truncated is False
    elif lifecycle == "observe_property_rejection":
        assert len(result) == 2
        assert all(item.outcome == "SEARCH_COMPLETED" for item in result)
    else:
        assert result == [{"value": "ok"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", LIFECYCLES)
async def test_failing_poll_still_releases_the_permit(lifecycle: str) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            posts += 1
            return httpx.Response(201, json={"search_id": "search-1", "status": "WAIT"})
        if path.endswith("/ariel/searches/search-1"):
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    budget = FakeBudget(grant=True)
    provider = make_provider(handler, budget)
    try:
        with pytest.raises(MalformedUpstreamResponse, match="status was not an object"):
            await run_lifecycle(provider, lifecycle)
    finally:
        await provider.aclose()

    assert posts == 1
    assert (budget.enters, budget.exits) == (1, 1)


def instance(kind: ProviderKind) -> QRadarInstance:
    return QRadarInstance(
        id=uuid.UUID("00000000-0000-0000-0000-000000000702"),
        name="lab",
        console_host=BASE_URL,
        api_version="20.0",
        sec_token="test-token",
        verify_ssl=True,
        provider_kind=kind.value,
    )


@pytest.mark.asyncio
async def test_rest_factories_use_the_process_engine_without_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("postgresql+asyncpg://test:test@localhost/unit")
    settings = Settings(
        _env_file=None,
        qradar_provider=ProviderKind.REST,
        qradar_host=BASE_URL,
        qradar_sec_token="test-token",
        encryption_key="x" * 44,
    )
    monkeypatch.setattr("app.providers.factory.get_engine", lambda: engine)
    default = build_provider(settings)
    bound = build_provider_for_instance(instance(ProviderKind.REST), settings)
    try:
        assert isinstance(default, QRadarRestProvider)
        assert isinstance(bound, QRadarRestProvider)
        assert default._permit is not None and default._permit._engine is engine
        assert default._permit.instance_id is None
        assert bound._permit is not None and bound._permit._engine is engine
        assert bound._permit.instance_id == instance(ProviderKind.REST).id
    finally:
        await default.aclose()
        await bound.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_instance_factory_derives_the_budget_from_its_run_session() -> None:
    engine = create_async_engine("postgresql+asyncpg://test:test@localhost/unit")
    settings = Settings(_env_file=None, encryption_key="x" * 44)
    async with AsyncSession(engine) as session:
        provider = build_provider_for_instance(
            instance(ProviderKind.REST),
            settings,
            audit_session=session,
        )
        try:
            assert isinstance(provider, QRadarRestProvider)
            assert provider._permit is not None
            assert provider._permit._engine is engine
            assert provider._permit.instance_id == instance(ProviderKind.REST).id
        finally:
            await provider.aclose()
    await engine.dispose()


def test_mock_and_mcp_factories_do_not_receive_permits() -> None:
    settings = Settings(_env_file=None, encryption_key="x" * 44)
    mock = build_provider_for_instance(instance(ProviderKind.MOCK), settings)
    mcp = build_provider_for_instance(instance(ProviderKind.MCP), settings)

    assert isinstance(mock, MockQRadarProvider) and not hasattr(mock, "_permit")
    assert isinstance(mcp, QRadarMCPProvider) and not hasattr(mcp, "_permit")
