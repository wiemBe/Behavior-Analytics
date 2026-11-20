"""JDBC-1 J1d2: the real worker task, its lease paths and the inventory kick.

Only the QRadar transport and the Celery enqueue are faked. Settings resolution,
the queued/coalesced lifecycle, snapshots and every state/count/code go through
the real code against real Postgres. Gated on TEST_DATABASE_URL; one file at a
time against its own schema.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.jdbc import jobs
from app.jdbc.dto import JdbcConfigDTO, JdbcConfigWalk, JdbcWalkState
from app.jdbc.parameters import project_jdbc_config
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSyncState
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.qradar_rest import QRadarRestProvider
from app.services.feature_switches import UNREADABLE_REASON
from app.workers import tasks

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SYNC = JdbcSyncState.__table__
WALK_KWARGS = {"page_timeout_seconds": 30.0, "page_attempts": 3, "wall_seconds": 600}


def make_walk(state: JdbcWalkState, *, exc: Exception | None = None) -> FakeRestProvider:
    params = [
        {"id": 8005, "name": "databaseType", "value": "Postgres"},
        {"id": 8020, "name": "databaseServerHostname", "value": "db.example.com"},
    ]
    config: JdbcConfigDTO = project_jdbc_config(
        qradar_id=100, protocol_type_id=8, protocol_parameters=params
    )
    walk = JdbcConfigWalk(
        configs=[config], pages_read=3, rows_read=4, state=state, elapsed_seconds=1.25
    )
    return FakeRestProvider(walk, exc=exc)


class FakeRestProvider(QRadarRestProvider):
    """A REST provider whose walk is injected; no transport or constructor used."""

    def __init__(self, walk: JdbcConfigWalk | None = None, *, exc: Exception | None = None) -> None:
        self.walk = walk
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def walk_jdbc_configs(self, **kwargs: Any) -> JdbcConfigWalk:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        assert self.walk is not None
        return self.walk

    async def aclose(self) -> None:
        return None


class Rig:
    """A real engine and sessionmaker, with the task's collaborators pointed at it."""

    def __init__(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.monkeypatch = monkeypatch
        self.provider_builds = 0
        self.provider: Any = None
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: self.maker)
        monkeypatch.setattr(tasks, "build_provider_for_instance", self._build)

    def _build(self, *args: object, **kwargs: object) -> Any:
        self.provider_builds += 1
        return self.provider

    def base(self, **env: bool) -> None:
        settings = Settings(_env_file=None, **env)
        self.monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    async def seed(self, name: str) -> uuid.UUID:
        async with self.maker() as session:
            instance = QRadarInstance(name=name, console_host="qradar.lab.local")
            session.add(instance)
            await session.flush()
            session.add(
                LogSource(
                    instance_id=instance.id, qradar_id=100, name="src-100", protocol_type_id=8
                )
            )
            await session.commit()
            return instance.id

    async def rows(self) -> list[dict[str, Any]]:
        async with self.maker() as session:
            return [dict(r) for r in (await session.execute(select(SYNC))).mappings().all()]


@pytest.fixture
async def rig(db_schema: str, monkeypatch: pytest.MonkeyPatch):
    rig = Rig(db_schema, monkeypatch)
    try:
        yield rig
    finally:
        await rig.engine.dispose()


async def test_actual_task_off_sends_no_requests(rig: Rig) -> None:
    rig.base(jdbc_collection_enabled=False)
    assert await tasks.collect_jdbc_configs() == {"status": "disabled"}
    assert rig.provider_builds == 0
    assert await rig.rows() == []

    # An unreadable switch table is a distinct, explicit disabled reason.
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))
    assert await tasks.collect_jdbc_configs() == {
        "status": "disabled", "reason": UNREADABLE_REASON,
    }
    assert rig.provider_builds == 0
    assert await rig.rows() == []


async def test_actual_task_lease_lock_timeout_paths(rig: Rig) -> None:
    rig.base(jdbc_collection_enabled=True)

    # COMPLETE: the real task stores the pass and forwards fixed counts only.
    complete_id = await rig.seed("complete")
    provider = make_walk(JdbcWalkState.COMPLETE)
    rig.provider = provider
    result = await tasks.collect_jdbc_configs("complete")
    assert result["instances"] == 1
    assert result["results"][0] == {
        "instance": "complete", "status": "COMPLETE", "failure_code": None,
        "pages": 3, "rows": 4, "failures": 0, "elapsed_ms": 1250, "failed_page": None,
    }
    assert provider.calls == [WALK_KWARGS]
    rows = {r["instance_id"]: r for r in await rig.rows()}
    assert rows[complete_id]["state"] == "COMPLETE"
    assert rows[complete_id]["last_complete_at"] is not None
    assert rig.provider_builds == 1

    # A live lease coalesces: claim returns None and no walk is sent.
    lease_id = await rig.seed("lease")
    async with rig.maker() as session:
        await jobs.request_jdbc_run(session, lease_id, now=datetime.now(UTC))
        await session.execute(
            update(SYNC).where(SYNC.c.instance_id == lease_id).values(
                state="RUNNING", started_at=datetime.now(UTC),
                lease_expires_at=datetime.now(UTC) + timedelta(hours=1), attempt_id=uuid.uuid4(),
            )
        )
        await session.commit()
    assert {r["instance_id"]: r for r in await rig.rows()}[lease_id]["state"] == "RUNNING"
    coalesced = make_walk(JdbcWalkState.COMPLETE)
    rig.provider = coalesced
    result = await tasks.collect_jdbc_configs("lease")
    assert result["results"][0]["instance"] == "lease", result
    assert result["results"][0]["status"] == "coalesced"
    assert coalesced.calls == []
    lease_rows = {r["instance_id"]: r for r in await rig.rows()}
    assert lease_rows[lease_id]["state"] == "RUNNING"

    # Soft timeout: J1d1 records FAILED/soft_timeout once, in its own transaction.
    timeout_id = await rig.seed("timeout")
    rig.provider = make_walk(JdbcWalkState.COMPLETE, exc=SoftTimeLimitExceeded())
    with pytest.raises(SoftTimeLimitExceeded):
        await tasks.collect_jdbc_configs("timeout")
    rows = {r["instance_id"]: r for r in await rig.rows()}
    assert rows[timeout_id]["state"] == "FAILED"
    assert rows[timeout_id]["failure_code"] == "soft_timeout"
    assert rows[timeout_id]["finished_at"] is not None
    assert rows[timeout_id]["lease_expires_at"] is None
    # The generic timeout recorder has no JDBC branch; it records nothing twice.
    async with rig.maker() as session:
        assert (await session.scalars(select(CollectionWatermark))).all() == []
    # Earlier consoles are untouched by a failed pass on another one.
    assert rows[complete_id]["state"] == "COMPLETE"
    assert rows[lease_id]["state"] == "RUNNING"
    assert lease_id in rows


async def test_post_inventory_kick_coalesces(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.base(jdbc_collection_enabled=True)
    enqueued: list[str] = []
    monkeypatch.setattr(
        tasks.collect_jdbc_configs_task, "delay", lambda instance_id: enqueued.append(instance_id)
    )

    holder = {"status": "COMPLETE"}

    class Collector:
        def __init__(self, session: AsyncSession, provider: object, *, settings: Settings) -> None:
            pass

        async def sync(self, instance: QRadarInstance) -> SimpleNamespace:
            return SimpleNamespace(
                status=holder["status"], skipped_locked=False, log_sources_seen=0,
                created=0, updated=0, pages_done=1, pages_failed=0, failed_page=None,
                failure_kind=None, duration_ms=1, error=None,
            )

    class DummyProvider:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(tasks, "LogSourceCollector", Collector)
    rig.provider = DummyProvider()

    complete_id = await rig.seed("complete")
    await tasks.sync_log_sources(instance_name="complete")
    assert enqueued == [str(complete_id)]
    rows = {r["instance_id"]: r for r in await rig.rows()}
    assert rows[complete_id]["state"] == "QUEUED"

    # A second pass while the row is QUEUED coalesces: no second task is sent.
    await tasks.sync_log_sources(instance_name="complete")
    assert enqueued == [str(complete_id)]

    # PARTIAL also kicks; a failed pass does not.
    partial_id = await rig.seed("partial")
    holder["status"] = "PARTIAL"
    await tasks.sync_log_sources(instance_name="partial")
    assert enqueued == [str(complete_id), str(partial_id)]

    failed_id = await rig.seed("failed")
    holder["status"] = "FAILED"
    await tasks.sync_log_sources(instance_name="failed")
    assert enqueued == [str(complete_id), str(partial_id)]
    assert failed_id not in {r["instance_id"] for r in await rig.rows()}

    # Off: no request, no enqueue, inventory result unchanged.
    rig.base(jdbc_collection_enabled=False)
    off_id = await rig.seed("off")
    holder["status"] = "COMPLETE"
    result = await tasks.sync_log_sources(instance_name="off")
    assert result["results"][0]["status"] == "ok"
    assert enqueued == [str(complete_id), str(partial_id)]
    assert off_id not in {r["instance_id"] for r in await rig.rows()}
