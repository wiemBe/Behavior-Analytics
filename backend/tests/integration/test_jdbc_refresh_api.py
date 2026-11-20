"""JDBC-1 J1n: the queued manual refresh route and the two post-inventory kick fixes.

Only the Celery enqueue and the QRadar transport are faked. DB-gated; run one file at a time
per database.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSyncState
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.feature_switches import invalidate_effective_settings
from app.workers import tasks

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="needs TEST_DATABASE_URL"),
]

SYNC = JdbcSyncState.__table__
URL = "/api/v1/log-sources/jdbc/sync"
ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
OFF_DETAIL = (
    "JDBC collection is turned off. An administrator can turn it on under Administration "
    "-> Features."
)
QUEUE_FAILED = "the JDBC refresh could not be queued"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _engine() -> Any:
    return create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)


async def _seed(*names: str, enabled: bool = True) -> list[uuid.UUID]:
    engine = _engine()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            made = [
                QRadarInstance(name=n, console_host="qradar.lab.local", enabled=enabled)
                for n in names
            ]
            s.add_all(made)
            await s.commit()
            return [i.id for i in made]
    finally:
        await engine.dispose()


async def _rows() -> dict[uuid.UUID, dict[str, Any]]:
    engine = _engine()
    try:
        async with async_sessionmaker(engine)() as s:
            got = (await s.execute(select(SYNC))).mappings().all()
            return {r["instance_id"]: dict(r) for r in got}
    finally:
        await engine.dispose()


async def _audits() -> list[Any]:
    engine = _engine()
    try:
        async with async_sessionmaker(engine)() as s:
            return list(await s.scalars(select(AuditLog).where(AuditLog.action == "jdbc.sync")))
    finally:
        await engine.dispose()


async def _set_state(iid: uuid.UUID, **values: Any) -> None:
    engine = _engine()
    try:
        async with async_sessionmaker(engine)() as s:
            await s.execute(update(SYNC).where(SYNC.c.instance_id == iid).values(**values))
            await s.commit()
    finally:
        await engine.dispose()


def _client(principal: Principal | None = ADMIN, **settings: Any) -> TestClient:
    engine = _engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as s:
            yield s
            await s.commit()

    app = create_app()
    app.dependency_overrides[get_session] = _session
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    override = get_settings().model_copy(update=settings)
    app.dependency_overrides[get_settings] = lambda: override
    invalidate_effective_settings()
    return TestClient(app, raise_server_exceptions=False)


class Sent:
    """A stand-in for the task's `delay`: records keyword calls, can be told to fail."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        monkeypatch.setattr(tasks.collect_jdbc_configs_task, "delay", self)

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        if self.fail:
            raise ConnectionError("broker down CANARY")
        self.calls.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.calls)}")


def test_actual_post_202_no_provider(db_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.factory as factory
    from app.workers.celery_app import celery_app

    (iid,) = _run(_seed("c1"))
    sent = Sent(monkeypatch)

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("the route reached a provider")

    for name in ("build_provider", "build_provider_for_instance", "get_provider"):
        monkeypatch.setattr(factory, name, boom)
    monkeypatch.setattr(tasks, "build_provider_for_instance", boom)
    monkeypatch.setattr(celery_app, "send_task", boom)
    resp = _client(jdbc_collection_enabled=True).post(URL)
    assert resp.status_code == 202
    assert resp.json() == {"queued": True, "already_running": False, "task_ids": ["task-1"]}
    assert sent.calls == [{"instance_id": str(iid)}]
    assert _run(_rows())[iid]["state"] == "QUEUED"
    (audit,) = _run(_audits())
    assert (audit.outcome, audit.actor) == ("SUCCESS", "admin")


def test_switch_off_not_collected_no_enqueue(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(_seed("c1"))
    sent = Sent(monkeypatch)
    resp = _client(jdbc_collection_enabled=False).post(URL)
    assert (resp.status_code, resp.json()["detail"]) == (409, OFF_DETAIL)
    assert sent.calls == []
    assert _run(_rows()) == {}


def test_already_queued_running(db_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    (iid,) = _run(_seed("c1"))
    sent = Sent(monkeypatch)
    client = _client(jdbc_collection_enabled=True)
    assert client.post(URL).json()["queued"] is True
    again = client.post(URL)
    assert again.status_code == 202
    assert again.json() == {"queued": False, "already_running": True, "task_ids": []}
    assert len(sent.calls) == 1

    now = datetime.now(UTC)
    _run(_set_state(iid, state="RUNNING", lease_expires_at=now + timedelta(hours=1)))
    assert client.post(URL).json()["already_running"] is True
    assert len(sent.calls) == 1

    # An expired lease is not live: the pass is queued again.
    _run(_set_state(iid, state="RUNNING", lease_expires_at=now - timedelta(minutes=1)))
    resp = client.post(URL)
    assert resp.json() == {"queued": True, "already_running": False, "task_ids": ["task-2"]}
    assert _run(_rows())[iid]["state"] == "QUEUED"


def test_broker_503_fixed_error(db_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(_seed("c1"))
    Sent(monkeypatch, fail=True)
    resp = _client(jdbc_collection_enabled=True).post(URL)
    assert (resp.status_code, resp.json()["detail"]) == (503, QUEUE_FAILED)
    assert "CANARY" not in resp.text
    (audit,) = _run(_audits())
    assert audit.outcome == "FAILURE"
    assert audit.detail == {"queued": False, "error_class": "ConnectionError"}


def test_admin_required(db_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(_seed("c1"))
    sent = Sent(monkeypatch)
    denied = _client(VIEWER, jdbc_collection_enabled=True).post(URL)
    assert denied.status_code == 403
    missing = _client(None, auth_provider="oidc", jdbc_collection_enabled=True).post(URL)
    assert missing.status_code == 401
    assert sent.calls == [] and _run(_rows()) == {}


def test_lease_released_on_enqueue_failure(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (iid,) = _run(_seed("c1"))
    Sent(monkeypatch, fail=True)
    client = _client(jdbc_collection_enabled=True)
    assert client.post(URL).status_code == 503
    # No QUEUED row is left behind to coalesce the next request forever.
    assert iid not in _run(_rows())
    sent = Sent(monkeypatch)
    resp = client.post(URL)
    assert resp.status_code == 202 and resp.json()["queued"] is True
    assert sent.calls == [{"instance_id": str(iid)}]


def test_instance_filter_and_unknown_console(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, two = _run(_seed("c1", "c2"))
    (off,) = _run(_seed("c3", enabled=False))
    sent = Sent(monkeypatch)
    client = _client(jdbc_collection_enabled=True)
    assert client.post(URL, params={"instance_id": str(two)}).json()["queued"] is True
    assert sent.calls == [{"instance_id": str(two)}]
    for missing in (off, uuid.uuid4()):
        assert client.post(URL, params={"instance_id": str(missing)}).status_code == 404
    assert client.post(URL).json()["task_ids"] == ["task-2"]  # only c1: c2 queued, c3 disabled
    assert set(_run(_rows())) == {one, two}


# ------------------------------------------------------------------ the inventory kick
class Rig:
    def __init__(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        settings = Settings(_env_file=None, jdbc_collection_enabled=True)
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: self.maker)
        monkeypatch.setattr(tasks, "get_settings", lambda: settings)
        monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: Provider())

        class Collector:
            def __init__(self, session: AsyncSession, provider: object, *, settings: Any) -> None:
                self.session = session

            async def sync(self, instance: QRadarInstance) -> SimpleNamespace:
                instance.description = "inventory-committed"
                return SimpleNamespace(
                    status="COMPLETE", skipped_locked=False, log_sources_seen=0, created=0,
                    updated=0, pages_done=1, pages_failed=0, failed_page=None,
                    failure_kind=None, duration_ms=1, error=None)

        monkeypatch.setattr(tasks, "LogSourceCollector", Collector)

    async def seed(self, name: str) -> uuid.UUID:
        async with self.maker() as s:
            inst = QRadarInstance(name=name, console_host="qradar.lab.local")
            s.add(inst)
            await s.commit()
            return inst.id

    async def description(self, iid: uuid.UUID) -> str | None:
        async with self.maker() as s:
            return await s.scalar(
                select(QRadarInstance.description).where(QRadarInstance.id == iid))

    async def state(self, iid: uuid.UUID) -> str | None:
        async with self.maker() as s:
            return await s.scalar(select(SYNC.c.state).where(SYNC.c.instance_id == iid))


class Provider:
    async def aclose(self) -> None:
        return None


@pytest.fixture
async def rig(db_schema: str, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    rig = Rig(db_schema, monkeypatch)
    try:
        yield rig
    finally:
        await rig.engine.dispose()


@pytest.mark.asyncio
async def test_kick_task_walks_the_console_by_id(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    iid = await rig.seed("c1")
    await rig.seed("other")
    sent = Sent(monkeypatch)
    await tasks.sync_log_sources(instance_name="c1")
    assert sent.calls == [{"instance_id": str(iid)}]

    # The real task body, with exactly what the kick sent: it finds the console.
    import inspect

    assert "instance_id" in inspect.signature(tasks.collect_jdbc_configs_task.run).parameters
    seen: list[str] = []

    async def fake_run(maker: Any, instance: QRadarInstance, *a: Any, **k: Any) -> Any:
        seen.append(instance.name)
        return SimpleNamespace(
            state="COMPLETE", failure_code=None, pages=1, rows=1, failures=0, elapsed_ms=1,
            failed_page=None)

    monkeypatch.setattr(tasks, "run_jdbc_collection", fake_run)
    result = await tasks.collect_jdbc_configs(**sent.calls[0])
    assert result.get("status") != "no-instance"
    assert seen == ["c1"] and result["instances"] == 1


@pytest.mark.asyncio
async def test_kick_db_error_keeps_inventory_commit(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    iid = await rig.seed("c1")
    sent = Sent(monkeypatch)

    async def broken(session: AsyncSession, *a: Any, **k: Any) -> bool:
        await session.execute(text("SELECT * FROM no_such_table_jdbc1n"))
        return True

    monkeypatch.setattr(tasks, "request_jdbc_run", broken)
    with caplog.at_level(logging.WARNING, logger="app.tasks"):
        result = await tasks.sync_log_sources(instance_name="c1")
    assert result["results"][0]["status"] == "ok", result
    assert await rig.description(iid) == "inventory-committed"
    assert "jdbc inventory kick could not request its pass" in caplog.text
    assert sent.calls == [] and await rig.state(iid) is None


@pytest.mark.asyncio
async def test_kick_enqueue_error_keeps_queued_row(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    iid = await rig.seed("c1")
    Sent(monkeypatch, fail=True)
    with caplog.at_level(logging.WARNING, logger="app.tasks"):
        result = await tasks.sync_log_sources(instance_name="c1")
    assert result["results"][0]["status"] == "ok", result
    assert await rig.description(iid) == "inventory-committed"
    assert await rig.state(iid) == "QUEUED"
    assert "jdbc inventory kick could not enqueue its task" in caplog.text
