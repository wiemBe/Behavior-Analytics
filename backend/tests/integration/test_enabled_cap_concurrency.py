"""Enabled custom-rule and scheduled-search caps under concurrent writes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import custom_rules, searches
from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.custom_rule import CustomRule
from app.models.search import ScheduledSearch
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.scheduled_search import ScheduledSearchService
from tests.integration.factories import make_instance, make_log_source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run enabled-cap concurrency tests",
    ),
]

_AQL = "SELECT sourceip FROM events LAST 1 HOURS"
_HOURLY = "0 * * * *"
_ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))


class _RaceGate:
    """Hold the first request after its count until the second starts checking."""

    def __init__(self) -> None:
        self.first_counted = asyncio.Event()
        self.second_check_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.count_calls = 0
        self.lock_calls = 0

    async def before_lock(self) -> None:
        self.lock_calls += 1
        if self.lock_calls == 2:
            self.second_check_started.set()

    async def after_count(self) -> None:
        self.count_calls += 1
        if self.count_calls == 1:
            self.first_counted.set()
            await self.release_first.wait()
        elif self.count_calls == 2:
            # With the production lock removed, the second request reaches the
            # same zero count while the first transaction is still suspended.
            self.second_check_started.set()


@asynccontextmanager
async def _client(
    db_url: str,
    seen_sessions: set[int],
    **setting_updates: object,
) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session() -> AsyncIterator[Any]:
        async with maker() as session:
            seen_sessions.add(id(session))
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    settings = get_settings().model_copy(update=setting_updates)
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        await engine.dispose()


async def _race(
    first_request: Callable[[], Awaitable[httpx.Response]],
    second_request: Callable[[], Awaitable[httpx.Response]],
    gate: _RaceGate,
) -> list[httpx.Response]:
    first = asyncio.create_task(first_request())
    second: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(gate.first_counted.wait(), timeout=5)
        second = asyncio.create_task(second_request())
        await asyncio.wait_for(gate.second_check_started.wait(), timeout=5)
    finally:
        gate.release_first.set()
    if second is None:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        raise AssertionError("the second request was not started")
    return list(await asyncio.gather(first, second))


def _guard_custom_rule_cap(monkeypatch: pytest.MonkeyPatch, gate: _RaceGate) -> None:
    original_lock = custom_rules._lock_enabled_cap
    original_count = custom_rules._enabled_count

    async def observed_lock(session: Any) -> None:
        await gate.before_lock()
        await original_lock(session)

    async def held_count(session: Any) -> int:
        count = await original_count(session)
        await gate.after_count()
        return count

    monkeypatch.setattr(custom_rules, "_lock_enabled_cap", observed_lock)
    monkeypatch.setattr(custom_rules, "_enabled_count", held_count)


def _guard_search_cap(monkeypatch: pytest.MonkeyPatch, gate: _RaceGate) -> None:
    original_lock = searches._lock_enabled_cap
    original_count = ScheduledSearchService.count_enabled

    async def observed_lock(session: Any) -> None:
        await gate.before_lock()
        await original_lock(session)

    async def held_count(service: ScheduledSearchService) -> int:
        count = await original_count(service)
        await gate.after_count()
        return count

    monkeypatch.setattr(searches, "_lock_enabled_cap", observed_lock)
    monkeypatch.setattr(ScheduledSearchService, "count_enabled", held_count)


def _rule_payload(source_id: object, name: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "name": name,
        "subject_kind": "LOG_SOURCE",
        "log_source_id": str(source_id),
        "metric": "event_count",
        "operator": "GT",
        "threshold_value": 100,
        "sustain_buckets": 3,
        "recovery_buckets": 2,
        "severity": "HIGH",
        "enabled": enabled,
    }


def _search_payload(name: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "name": name,
        "aql_query": _AQL,
        "schedule_cron": _HOURLY,
        "enabled": enabled,
    }


def _assert_one_wins(responses: list[httpx.Response], success_status: int, detail: str) -> None:
    assert sorted(response.status_code for response in responses) == [success_status, 409]
    refused = next(response for response in responses if response.status_code == 409)
    assert refused.json()["detail"] == detail


async def test_concurrent_enabled_custom_rule_creates_share_one_slot(
    db_session: Any,
    db_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1201, name="race-create")
    await db_session.commit()
    gate = _RaceGate()
    _guard_custom_rule_cap(monkeypatch, gate)
    seen_sessions: set[int] = set()

    async with _client(
        db_schema,
        seen_sessions,
        collection_interval_seconds=300,
        custom_rules_max_enabled=1,
    ) as client:
        responses = await _race(
            lambda: client.post(
                "/api/v1/custom-rules", json=_rule_payload(source.id, "race-create-a")
            ),
            lambda: client.post(
                "/api/v1/custom-rules", json=_rule_payload(source.id, "race-create-b")
            ),
            gate,
        )

    _assert_one_wins(
        responses,
        201,
        "at most 1 rules may be enabled at once (1 already are)",
    )
    assert len(seen_sessions) == 2
    assert await db_session.scalar(
        select(func.count()).select_from(CustomRule).where(CustomRule.enabled.is_(True))
    ) == 1


async def test_concurrent_custom_rule_enables_share_one_slot(
    db_session: Any,
    db_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1202, name="race-patch")
    await db_session.commit()
    seen_sessions: set[int] = set()

    async with _client(
        db_schema,
        seen_sessions,
        collection_interval_seconds=300,
        custom_rules_max_enabled=1,
    ) as client:
        first = await client.post(
            "/api/v1/custom-rules",
            json=_rule_payload(source.id, "race-patch-a", enabled=False),
        )
        second = await client.post(
            "/api/v1/custom-rules",
            json=_rule_payload(source.id, "race-patch-b", enabled=False),
        )
        assert first.status_code == second.status_code == 201
        gate = _RaceGate()
        _guard_custom_rule_cap(monkeypatch, gate)
        seen_sessions.clear()
        responses = await _race(
            lambda: client.patch(
                f"/api/v1/custom-rules/{first.json()['id']}",
                json={"expected_updated_at": first.json()["updated_at"], "enabled": True},
            ),
            lambda: client.patch(
                f"/api/v1/custom-rules/{second.json()['id']}",
                json={"expected_updated_at": second.json()["updated_at"], "enabled": True},
            ),
            gate,
        )

    _assert_one_wins(
        responses,
        200,
        "at most 1 rules may be enabled at once (1 already are)",
    )
    assert len(seen_sessions) == 2
    assert await db_session.scalar(
        select(func.count()).select_from(CustomRule).where(CustomRule.enabled.is_(True))
    ) == 1


async def test_concurrent_enabled_search_creates_share_one_slot(
    db_session: Any,
    db_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _RaceGate()
    _guard_search_cap(monkeypatch, gate)
    seen_sessions: set[int] = set()

    async with _client(db_schema, seen_sessions, scheduled_searches_max_enabled=1) as client:
        responses = await _race(
            lambda: client.post("/api/v1/searches", json=_search_payload("search-create-a")),
            lambda: client.post("/api/v1/searches", json=_search_payload("search-create-b")),
            gate,
        )

    _assert_one_wins(responses, 201, "the limit is 1 enabled searches")
    assert len(seen_sessions) == 2
    assert await db_session.scalar(
        select(func.count()).select_from(ScheduledSearch).where(ScheduledSearch.enabled.is_(True))
    ) == 1


async def test_concurrent_search_enables_share_one_slot(
    db_session: Any,
    db_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_sessions: set[int] = set()

    async with _client(db_schema, seen_sessions, scheduled_searches_max_enabled=1) as client:
        first = await client.post(
            "/api/v1/searches", json=_search_payload("search-patch-a", enabled=False)
        )
        second = await client.post(
            "/api/v1/searches", json=_search_payload("search-patch-b", enabled=False)
        )
        assert first.status_code == second.status_code == 201
        gate = _RaceGate()
        _guard_search_cap(monkeypatch, gate)
        seen_sessions.clear()
        responses = await _race(
            lambda: client.patch(
                f"/api/v1/searches/{first.json()['id']}", json={"enabled": True}
            ),
            lambda: client.patch(
                f"/api/v1/searches/{second.json()['id']}", json={"enabled": True}
            ),
            gate,
        )

    _assert_one_wins(responses, 200, "the limit is 1 enabled searches")
    assert len(seen_sessions) == 2
    assert await db_session.scalar(
        select(func.count()).select_from(ScheduledSearch).where(ScheduledSearch.enabled.is_(True))
    ) == 1
