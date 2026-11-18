"""FIX-72: bounded background snapshots on a console-sized fake catalog."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.models.property_catalog import PropertyCatalogEntry, PropertyCatalogState
from app.services.locks import CollectorAdvisoryLock
from app.services.property_catalog import refresh_catalog_snapshot, stored_catalog
from tests.integration.factories import make_instance
from tests.unit.test_property_catalog import EXPRS, PROPS, _expr, _prop
from tests.unit.test_qradar_rest_provider import build_provider

pytestmark = pytest.mark.integration


def large_provider(*, fail: bool = False, timeout_once: bool = False):
    seen: list[tuple[str, int]] = []
    retried = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal retried
        assert request.method == "GET"
        endpoint = request.url.path
        start, end = map(int, request.headers["Range"].split("=")[1].split("-"))
        seen.append((endpoint, start))
        assert end - start + 1 == 500
        if endpoint == PROPS:
            rows = [_prop(i, f"Property_{i:05}") for i in range(start, min(end + 1, 12000))]
        else:
            assert endpoint == EXPRS
            if start == 500 and fail:
                return httpx.Response(503, json={"secret": "DO-NOT-LEAK"})
            if start == 500 and timeout_once and not retried:
                retried = True
                raise httpx.ReadTimeout("DO-NOT-LEAK")
            rows = [_expr(f"ID{i % 12000}", i % 7) for i in range(start, min(end + 1, 40000))]
        return httpx.Response(200, json=rows)

    provider, sleep = build_provider(handler)
    return provider, seen, sleep


@pytest.mark.parametrize("timeout_once", [False, True])
async def test_large_walk_retry_atomic_failure_search_and_statement_bound(
    db_session, timeout_once
) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    statements: list[str] = []
    engine = db_session.bind

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    try:
        provider, seen, sleep = large_provider(timeout_once=timeout_once)
        started = time.perf_counter()
        assert await refresh_catalog_snapshot(db_session, instance.id, lambda: provider)
        duration = time.perf_counter() - started
        print(f"FIX72 12000 properties / 40000 expressions: {duration:.3f}s; "
              f"{len(seen)} GET pages (retry={timeout_once}); {len(statements)} SQL statements")
        assert len(seen) == 106 + int(timeout_once)  # Includes empty terminal pages.
        assert sleep.delays == ([1] if timeout_once else [])
        assert len(statements) <= 25
        assert duration < 30
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    state = await db_session.get(PropertyCatalogState, instance.id)
    assert state.status == "COMPLETE"
    assert state.property_count == 12000 and state.expression_count == 40000
    old_as_of = state.as_of
    out = await stored_catalog(db_session, instance_id=instance.id, source_type_id=3, limit=200)
    assert len(out.properties) == 200
    assert all(p.applies_to_source_type for p in out.properties)
    result = await stored_catalog(
        db_session, instance_id=instance.id, source_type_id=None, q="pRoPeRtY_11999", limit=1
    )
    assert [p.name for p in result.properties] == ["Property_11999"]
    identifier = await stored_catalog(
        db_session, instance_id=instance.id, source_type_id=None, q="id11999"
    )
    assert identifier.properties[0].name == "Property_11999"
    literal = await stored_catalog(
        db_session, instance_id=instance.id, source_type_id=None, q="%"
    )
    assert literal.properties == []  # Contains search, not a wildcard language.
    provider, seen, sleep = large_provider(fail=True)
    assert await refresh_catalog_snapshot(db_session, instance.id, lambda: provider)
    await db_session.refresh(state)
    assert state.status == "PARTIAL" and state.as_of == old_as_of
    assert state.failure["kind"] == "upstream"
    assert state.failure["endpoint"] == "property_expressions"
    assert state.failure["http_status"] == 503 and state.failure["attempts"] == 3
    assert state.failure["page_start"] == 500
    assert "DO-NOT-LEAK" not in str(state.failure)
    assert await db_session.scalar(select(func.count()).select_from(PropertyCatalogEntry)) == 12000


async def test_first_names_survive_unknown_applicability_and_lock_refuses_concurrent(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    provider, _, _ = large_provider(fail=True)
    async with CollectorAdvisoryLock(db_session, get_settings(), instance.id, "property_catalog"):
        maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
        async with maker() as other:
            assert not await refresh_catalog_snapshot(other, instance.id, lambda: provider)
    assert await refresh_catalog_snapshot(db_session, instance.id, lambda: provider)
    out = await stored_catalog(db_session, instance_id=instance.id, source_type_id=3)
    assert out.status == "AVAILABLE" and out.catalog_status == "PARTIAL"
    assert len(out.properties) == 50
    assert {p.applies_to_source_type for p in out.properties} == {None}
    assert out.applicability_complete is False


async def test_refresh_state_visible_during_walk_and_close_error_does_not_discard(
    db_session,
) -> None:
    from app.providers.mock import MockQRadarProvider

    instance = await make_instance(db_session)
    await db_session.commit()
    entered = asyncio.Event()
    release = asyncio.Event()

    class Paused(MockQRadarProvider):
        async def read_property_catalog(self):
            entered.set()
            await release.wait()
            return await super().read_property_catalog()

        async def aclose(self):
            raise RuntimeError("DO-NOT-LEAK")

    async def run():
        maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
        async with maker() as other:
            return await refresh_catalog_snapshot(other, instance.id, lambda: Paused())

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        state = await db_session.get(PropertyCatalogState, instance.id)
        assert state.status == "REFRESHING" and state.last_started_at is not None
    finally:
        release.set()
        await task
    await db_session.refresh(state)
    assert state.status == "COMPLETE"


async def test_setup_failure_records_safe_cause_and_recovers(db_session) -> None:
    from app.providers.mock import MockQRadarProvider

    instance = await make_instance(db_session)
    await db_session.commit()

    def broken():
        raise ValueError("private URL and credential")

    assert await refresh_catalog_snapshot(db_session, instance.id, broken)
    out = await stored_catalog(db_session, instance_id=instance.id, source_type_id=None)
    assert out.catalog_status == "FAILED" and out.as_of is None
    assert out.failure.endpoint == "setup" and out.failure.failure_class == "ValueError"
    assert out.property_count is None and out.properties == []
    assert "private" not in out.model_dump_json()
    assert await refresh_catalog_snapshot(db_session, instance.id, lambda: MockQRadarProvider())
    out = await stored_catalog(db_session, instance_id=instance.id, source_type_id=None)
    assert out.catalog_status == "COMPLETE" and out.failure is None


async def test_worker_orchestration_dispatch_and_disabled_instance(db_session, monkeypatch) -> None:
    from unittest.mock import Mock

    from app.providers.mock import MockQRadarProvider
    from app.workers import tasks

    instance = await make_instance(db_session)
    await db_session.commit()
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    factory = Mock(side_effect=lambda instance: MockQRadarProvider())
    monkeypatch.setattr(tasks, "build_provider_for_instance", factory)
    queued = []
    monkeypatch.setattr(tasks.refresh_property_catalog, "delay", lambda ident: queued.append(ident))
    await tasks._dispatch_property_catalog_refresh()
    assert queued == [str(instance.id)]
    await tasks._refresh_property_catalog(instance.id)
    factory.assert_called_once()
    out = await stored_catalog(db_session, instance_id=instance.id, source_type_id=212)
    assert out.catalog_status == "COMPLETE" and out.properties
    instance.enabled = False
    await db_session.commit()
    await tasks._refresh_property_catalog(instance.id)
    await tasks._dispatch_property_catalog_refresh()
    assert queued == [str(instance.id)] and factory.call_count == 1
