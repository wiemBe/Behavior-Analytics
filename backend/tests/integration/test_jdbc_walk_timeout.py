"""FIX-96: the real WalkTask soft-timeout path for JDBC, and the job's
empty-unproven honesty state. Only the transport and the settings are faked;
the task, the lease lifecycle and every persisted code go through real code
against real Postgres. Gated on TEST_DATABASE_URL; one file at a time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.jdbc import jobs
from app.jdbc.dto import JdbcConfigWalk, JdbcWalkState
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig, JdbcSyncState
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.qradar_rest import QRadarRestProvider
from app.workers import tasks
from app.workers.celery_app import celery_app

pytestmark = pytest.mark.integration

SYNC = JdbcSyncState.__table__
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
WALK_KWARGS = {"page_timeout_seconds": 30.0, "page_attempts": 3, "wall_seconds": 600}


class TimeoutRestProvider(QRadarRestProvider):
    """A REST provider whose walk lets the signal unwind the event loop itself."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch

    async def walk_jdbc_configs(self, **kwargs: Any) -> JdbcConfigWalk:
        selector = asyncio.get_running_loop()._selector
        original = selector.select

        def interrupt(timeout: float) -> None:
            self.monkeypatch.setattr(selector, "select", original)
            raise SoftTimeLimitExceeded()

        self.monkeypatch.setattr(selector, "select", interrupt)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        return None


class EmptyRestProvider(QRadarRestProvider):
    """A REST provider whose walk is COMPLETE but empty."""

    def __init__(self) -> None:
        pass

    async def walk_jdbc_configs(self, **kwargs: Any) -> JdbcConfigWalk:
        return JdbcConfigWalk(
            configs=[], pages_read=1, rows_read=0, state=JdbcWalkState.COMPLETE
        )

    async def aclose(self) -> None:
        return None


async def seed(session: AsyncSession, *, jdbc_source: bool = True) -> uuid.UUID:
    """One console, optionally with a known JDBC source (so an empty walk is unproven)."""
    instance = QRadarInstance(
        name=f"jdbc-timeout-{uuid.uuid4()}", console_host="qradar.lab.local"
    )
    session.add(instance)
    await session.flush()
    if jdbc_source:
        session.add(
            LogSource(instance_id=instance.id, qradar_id=100, name="src-100", protocol_type_id=8)
        )
    await session.commit()
    return instance.id


async def seed_sync_row(
    session: AsyncSession,
    instance_id: uuid.UUID,
    *,
    state: str,
    attempt_id: uuid.UUID | None = None,
    lease_expires_at: datetime | None = None,
) -> None:
    """A sync row in the state a late signal must leave untouched."""
    session.add(JdbcSyncState(
        instance_id=instance_id, state=state, attempt_id=attempt_id,
        lease_expires_at=lease_expires_at,
        queued_at=NOW if state == "QUEUED" else None,
        started_at=NOW if state == "RUNNING" else None,
        finished_at=NOW if state == "COMPLETE" else None,
    ))
    await session.commit()


async def sync_rows(session: AsyncSession) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(select(SYNC))).mappings().all()]


def test_walk_task_soft_timeout_records_jdbc_failed_state(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1: the signal outside the coroutine reaches WalkTask, not the job's own handler."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    provider = TimeoutRestProvider(monkeypatch)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(
        tasks, "get_settings", lambda: Settings(_env_file=None, jdbc_collection_enabled=True)
    )
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: provider)
    monkeypatch.setattr(tasks, "dispose_engine", AsyncMock())

    async def prepare() -> uuid.UUID:
        async with maker() as session:
            return await seed(session)

    instance_id = asyncio.run(prepare())
    try:
        with pytest.raises(SoftTimeLimitExceeded):
            celery_app.tasks["collect_jdbc_configs"].apply(throw=True)

        async def verify() -> list[dict[str, Any]]:
            async with maker() as session:
                return await sync_rows(session)

        rows = asyncio.run(verify())
        assert len(rows) == 1
        row = rows[0]
        assert row["instance_id"] == instance_id
        assert row["state"] == "FAILED"
        assert row["failure_code"] == "soft_timeout"
        assert row["finished_at"] is not None
        assert row["lease_expires_at"] is None
        assert row["attempt_id"] is not None  # scoped to the attempt that was running
        assert tasks._WALK_TARGET.get() is None

        async def no_watermark() -> list[CollectionWatermark]:
            async with maker() as session:
                return list((await session.scalars(select(CollectionWatermark))).all())

        assert asyncio.run(no_watermark()) == []
    finally:
        asyncio.run(engine.dispose())


async def test_empty_unproven_walk_records_partial_state(
    db_schema: str, caplog: pytest.LogCaptureFixture
) -> None:
    """B4: a COMPLETE empty walk with known JDBC inventory is not a clean pass."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await seed(session)
            instance = (await session.scalars(select(QRadarInstance))).one()

        with caplog.at_level(logging.INFO, logger="app.jdbc.jobs"):
            outcome = await jobs.run_jdbc_collection(
                maker, instance, EmptyRestProvider(),
                clock=lambda: NOW, lease_seconds=60, walk_kwargs=WALK_KWARGS,
            )

        assert outcome.state == "PARTIAL"
        assert outcome.failure_code == "empty_unproven"
        assert outcome.apply is not None and outcome.apply.empty_unproven is True

        records = [record for record in caplog.records if record.name == "app.jdbc.jobs"]
        assert len(records) == 1
        assert records[0].empty_unproven is True
        assert records[0].state == "PARTIAL"
        assert records[0].failure_code == "empty_unproven"

        async with maker() as session:
            rows = await sync_rows(session)
            snapshots = list((await session.scalars(select(JdbcSourceConfig))).all())
        assert len(rows) == 1
        assert rows[0]["state"] == "PARTIAL"
        assert rows[0]["failure_code"] == "empty_unproven"
        assert rows[0]["last_complete_at"] is None
        assert rows[0]["lease_expires_at"] is None
        assert snapshots == []
    finally:
        await engine.dispose()


# --------------------------------------------------- r2: attempt-scoped timeout
def install_task_env(
    monkeypatch: pytest.MonkeyPatch,
    maker: async_sessionmaker[AsyncSession],
    *,
    build: Any = None,
) -> None:
    """Only the transport and settings are faked; the task and DB path are real."""
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(
        tasks, "get_settings",
        lambda: Settings(_env_file=None, jdbc_collection_enabled=True),
    )
    monkeypatch.setattr(tasks, "dispose_engine", AsyncMock())
    if build is not None:
        monkeypatch.setattr(tasks, "build_provider_for_instance", build)


def signal_on_build(*args: Any, **kwargs: Any) -> Any:
    """The target is set, but no attempt has been claimed when the signal lands."""
    raise SoftTimeLimitExceeded()


class FinishedThenSignalProvider(EmptyRestProvider):
    """A COMPLETE walk, then the signal in aclose() after the job already finished."""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self.maker = maker
        self.before: list[dict[str, Any]] = []

    async def aclose(self) -> None:
        async with self.maker() as session:
            self.before = await sync_rows(session)
        raise SoftTimeLimitExceeded()


def test_soft_timeout_before_claim_creates_no_row(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No published attempt: no _ensure_row, no UPDATE, no row at all."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        asyncio.run(seed_in(maker))
        install_task_env(monkeypatch, maker, build=signal_on_build)
        with pytest.raises(SoftTimeLimitExceeded):
            celery_app.tasks["collect_jdbc_configs"].apply(throw=True)
        assert asyncio.run(rows_in(maker)) == []
    finally:
        asyncio.run(engine.dispose())


def test_soft_timeout_before_claim_keeps_queued_request(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual QUEUED request is not overwritten by a signal with no claim."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        instance_id = asyncio.run(seed_in(maker))

        async def prepare() -> None:
            async with maker() as session:
                await seed_sync_row(session, instance_id, state="QUEUED")

        asyncio.run(prepare())
        install_task_env(monkeypatch, maker, build=signal_on_build)
        with pytest.raises(SoftTimeLimitExceeded):
            celery_app.tasks["collect_jdbc_configs"].apply(throw=True)
        rows = asyncio.run(rows_in(maker))
        assert len(rows) == 1
        assert rows[0]["state"] == "QUEUED"
        assert rows[0]["queued_at"] == NOW
        assert rows[0]["failure_code"] is None
        assert rows[0]["finished_at"] is None
    finally:
        asyncio.run(engine.dispose())


def test_soft_timeout_before_claim_keeps_other_workers_running_lease(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live lease owned by another attempt is not marked FAILED by this task."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    lease_until = NOW + timedelta(seconds=60)
    other_attempt = uuid.uuid4()
    try:
        instance_id = asyncio.run(seed_in(maker))

        async def prepare() -> None:
            async with maker() as session:
                await seed_sync_row(
                    session, instance_id, state="RUNNING",
                    attempt_id=other_attempt, lease_expires_at=lease_until,
                )

        asyncio.run(prepare())
        install_task_env(monkeypatch, maker, build=signal_on_build)
        with pytest.raises(SoftTimeLimitExceeded):
            celery_app.tasks["collect_jdbc_configs"].apply(throw=True)
        rows = asyncio.run(rows_in(maker))
        assert len(rows) == 1
        assert rows[0]["state"] == "RUNNING"
        assert rows[0]["attempt_id"] == other_attempt
        assert rows[0]["lease_expires_at"] == lease_until
        assert rows[0]["failure_code"] is None
        assert rows[0]["finished_at"] is None
    finally:
        asyncio.run(engine.dispose())


def test_soft_timeout_after_finish_keeps_complete(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signal lands after _finish committed COMPLETE: the row is unchanged."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    provider = FinishedThenSignalProvider(maker)
    try:
        asyncio.run(seed_in(maker, jdbc_source=False))  # no JDBC source: walk is COMPLETE
        install_task_env(monkeypatch, maker, build=lambda *a, **k: provider)
        with pytest.raises(SoftTimeLimitExceeded):
            celery_app.tasks["collect_jdbc_configs"].apply(throw=True)
        assert provider.before
        assert provider.before[0]["state"] == "COMPLETE"
        assert provider.before[0]["failure_code"] is None
        assert asyncio.run(rows_in(maker)) == provider.before
    finally:
        asyncio.run(engine.dispose())


def test_soft_timeout_other_collector_still_records_watermark(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-JDBC walk still records its time_limit watermark through WalkTask."""
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        instance_id = asyncio.run(seed_in(maker))
        install_task_env(monkeypatch, maker, build=signal_on_build)
        with pytest.raises(SoftTimeLimitExceeded):
            celery_app.tasks["sync_log_sources"].apply(throw=True)

        async def verify() -> list[CollectionWatermark]:
            async with maker() as session:
                return list((await session.scalars(select(CollectionWatermark))).all())

        marks = asyncio.run(verify())
        assert len(marks) == 1
        assert marks[0].instance_id == instance_id
        assert marks[0].collector == "log_source_inventory"
        assert marks[0].last_error == "time_limit"
        assert marks[0].consecutive_failures == 1
        assert marks[0].collection_metadata["inventory_pass"]["status"] == "FAILED"
        assert marks[0].collection_metadata["inventory_pass"]["failure_kind"] == "time_limit"
        assert asyncio.run(rows_in(maker)) == []
    finally:
        asyncio.run(engine.dispose())


async def seed_in(
    maker: async_sessionmaker[AsyncSession], *, jdbc_source: bool = True
) -> uuid.UUID:
    async with maker() as session:
        return await seed(session, jdbc_source=jdbc_source)


async def rows_in(maker: async_sessionmaker[AsyncSession]) -> list[dict[str, Any]]:
    async with maker() as session:
        return await sync_rows(session)
