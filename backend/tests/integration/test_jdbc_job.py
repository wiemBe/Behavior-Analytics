"""JDBC-1 J1d1: queue/claim/coalesce and the lease-safe collection run.

Only the provider is faked. Walk results come from the real J1a projector and
the real J1b result type, snapshots go through the real apply_jdbc_walk, and
every state/count/code is read back from the real database. Gated on
TEST_DATABASE_URL; one file at a time against its own schema.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.jdbc import jobs
from app.jdbc.dto import (
    JdbcConfigDTO,
    JdbcConfigWalk,
    JdbcWalkFailure,
    JdbcWalkState,
)
from app.jdbc.parameters import project_jdbc_config
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig, JdbcSyncState
from app.models.log_source import LogSource
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.services import jdbc_sync

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=1)
LEASE = 60
KWARGS: dict[str, Any] = {"wall_seconds": 600}
CANARY = "CANARY-PASSWORD-9f3a"


def fixed(value: datetime) -> Callable[[], datetime]:
    return lambda: value


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class FakeRestProvider(QRadarRestProvider):
    """A REST provider whose walk is injected; no transport or constructor used."""

    def __init__(
        self,
        walk: JdbcConfigWalk,
        *,
        exc: Exception | None = None,
        during_walk: Any = None,
    ) -> None:
        self.walk = walk
        self.exc = exc
        self.during_walk = during_walk
        self.calls: list[dict[str, Any]] = []

    async def walk_jdbc_configs(self, **kwargs: Any) -> JdbcConfigWalk:
        self.calls.append(kwargs)
        if self.during_walk is not None:
            await self.during_walk()
        if self.exc is not None:
            raise self.exc
        return self.walk


def config(qid: int, *, canary: str | None = None) -> JdbcConfigDTO:
    params: list[Any] = [
        {"id": 8005, "name": "databaseType", "value": "Postgres"},
        {"id": 8020, "name": "databaseServerHostname", "value": "db.example.com"},
        {"id": 8010, "name": "databaseName", "value": "app_audit"},
    ]
    if canary is not None:
        params.append({"id": 8050, "name": "databasePassword", "value": canary})
    return project_jdbc_config(qradar_id=qid, protocol_type_id=8, protocol_parameters=params)


def result(
    state: JdbcWalkState,
    configs: tuple[JdbcConfigDTO, ...] = (),
    *,
    pages: int = 3,
    rows: int = 4,
    failed: int = 0,
    elapsed: float = 1.25,
    failed_page: int | None = None,
    code: JdbcWalkFailure | None = None,
    rejected: Any = None,
) -> JdbcConfigWalk:
    return JdbcConfigWalk(
        configs=list(configs), pages_read=pages, rows_read=rows, state=state,
        failure_code=code, pages_failed=failed, rejected_rows=rejected or {},
        elapsed_seconds=elapsed, failed_page=failed_page,
    )


async def seed(
    db_session: AsyncSession, sources: tuple[tuple[int, int], ...] = ((100, 8),)
) -> QRadarInstance:
    instance = QRadarInstance(name=f"jdbc-job-{uuid.uuid4()}", console_host="qradar.lab.local")
    db_session.add(instance)
    await db_session.flush()
    for qid, protocol in sources:
        db_session.add(
            LogSource(
                instance_id=instance.id, qradar_id=qid, name=f"src-{qid}",
                protocol_type_id=protocol,
            )
        )
    await db_session.commit()
    return instance


async def sync(db_session: AsyncSession, instance_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db_session.execute(
            select(JdbcSyncState.__table__).where(
                JdbcSyncState.__table__.c.instance_id == instance_id
            )
        )
    ).mappings().one()
    return dict(row)


async def stored(db_session: AsyncSession, instance_id: uuid.UUID) -> list[dict[str, Any]]:
    ls, cfg = LogSource.__table__, JdbcSourceConfig.__table__
    rows = (
        await db_session.execute(
            select(cfg)
            .join(ls, cfg.c.log_source_id == ls.c.id)
            .where(ls.c.instance_id == instance_id)
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def capture(db_session: AsyncSession) -> tuple[list[tuple[str, Any]], Any]:
    engine = getattr(db_session.get_bind(), "sync_engine", db_session.get_bind())
    statements: list[tuple[str, Any]] = []

    def before(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", before)
    return statements, lambda: event.remove(engine, "before_cursor_execute", before)


async def test_claim_coalesces_and_expiry_reaps(db_session: AsyncSession) -> None:
    instance = await seed(db_session)
    iid = instance.id

    assert await jobs.request_jdbc_run(db_session, iid, now=NOW) is True
    await db_session.commit()
    before = await sync(db_session, iid)
    assert before["state"] == "QUEUED" and before["queued_at"] == NOW

    assert await jobs.request_jdbc_run(db_session, iid, now=NOW) is False  # queued coalesces

    first = await jobs.claim_jdbc_run(db_session, iid, now=NOW, lease_seconds=LEASE)
    await db_session.commit()
    assert first is not None
    claimed = await sync(db_session, iid)
    assert claimed["state"] == "RUNNING"
    assert claimed["attempt_id"] == first
    assert claimed["lease_expires_at"] == NOW + timedelta(seconds=LEASE)
    assert claimed["started_at"] == NOW

    assert await jobs.claim_jdbc_run(db_session, iid, now=NOW, lease_seconds=LEASE) is None

    await db_session.execute(
        update(JdbcSyncState.__table__)
        .where(JdbcSyncState.__table__.c.instance_id == iid)
        .values(last_complete_at=OLD, lease_expires_at=NOW - timedelta(seconds=1))
    )
    await db_session.commit()

    statements, stop = capture(db_session)
    try:
        second = await jobs.claim_jdbc_run(db_session, iid, now=NOW, lease_seconds=LEASE)
    finally:
        stop()
    await db_session.commit()
    assert second is not None and second != first
    assert any(
        "worker_lost" in (params.values() if isinstance(params, dict) else str(params))
        for _, params in statements
    ), "the expired worker must be recorded before the retry claim"

    after = await sync(db_session, iid)
    assert after["state"] == "RUNNING"
    assert after["attempt_id"] == second
    assert after["last_complete_at"] == OLD
    assert after["failure_code"] is None
    assert after["finished_at"] is None
    assert after["failed_page"] is None and after["response_bytes"] is None
    assert after["pages"] == 0 and after["rows"] == 0
    assert after["failures"] == 0 and after["elapsed_ms"] == 0
    assert after["lease_expires_at"] == NOW + timedelta(seconds=LEASE)


@pytest.mark.parametrize("state", list(JdbcWalkState))
async def test_complete_partial_failed_not_collected(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession], state: JdbcWalkState
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    await db_session.execute(
        update(JdbcSyncState.__table__)
        .where(JdbcSyncState.__table__.c.instance_id == iid)
        .values(state="IDLE")
    )
    await db_session.commit()
    await jobs.request_jdbc_run(db_session, iid, now=NOW)
    await db_session.commit()
    await db_session.execute(
        update(JdbcSyncState.__table__)
        .where(JdbcSyncState.__table__.c.instance_id == iid)
        .values(last_complete_at=OLD)
    )
    await db_session.commit()

    walk = result(
        state, (config(100),), failed=2, elapsed=1.25,
        failed_page=2 if state is JdbcWalkState.FAILED else None,
        code=JdbcWalkFailure.MALFORMED if state is JdbcWalkState.FAILED else None,
    )
    provider = FakeRestProvider(walk)

    outcome = await jobs.run_jdbc_collection(
        maker, instance, provider, clock=fixed(NOW), lease_seconds=LEASE, walk_kwargs=KWARGS
    )

    assert provider.calls == [KWARGS]
    assert outcome.state == state.value
    assert (outcome.pages, outcome.rows, outcome.failures, outcome.elapsed_ms) == (3, 4, 2, 1250)
    assert outcome.failure_code == (
        JdbcWalkFailure.MALFORMED.value if state is JdbcWalkState.FAILED else None
    )
    assert outcome.apply is not None and outcome.apply.written == 1

    row = await sync(db_session, iid)
    assert row["state"] == state.value
    assert (row["pages"], row["rows"], row["failures"], row["elapsed_ms"]) == (3, 4, 2, 1250)
    assert row["failed_page"] == (2 if state is JdbcWalkState.FAILED else None)
    assert row["failure_code"] == (
        JdbcWalkFailure.MALFORMED.value if state is JdbcWalkState.FAILED else None
    )
    assert row["finished_at"] == NOW and row["lease_expires_at"] is None
    assert row["last_complete_at"] == (NOW if state is JdbcWalkState.COMPLETE else OLD)
    assert row["response_bytes"] is None

    snapshots = await stored(db_session, iid)
    assert len(snapshots) == 1
    assert snapshots[0]["database_host"] == "db.example.com"
    assert snapshots[0]["config_state"] == config(100).config_state.value


async def test_soft_timeout_rolls_back_and_records_in_fresh_transaction(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    lsid = (
        await db_session.execute(select(LogSource.id).where(LogSource.instance_id == iid))
    ).scalar_one()
    db_session.add(
        JdbcSourceConfig(
            log_source_id=lsid, protocol_type_id=8, field_states={}, allowlist_version=1,
            config_state="COMPLETE", database_host="old.example.com", observed_at=OLD,
        )
    )
    await db_session.commit()

    real_apply = jdbc_sync.apply_jdbc_walk

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        await real_apply(*args, **kwargs)
        raise SoftTimeLimitExceeded("CANARY-EXCEPTION-TEXT-4d2e")

    monkeypatch.setattr(jobs.jdbc_sync, "apply_jdbc_walk", exploding)
    walk = result(JdbcWalkState.COMPLETE, (config(100),))

    with (
        caplog.at_level(logging.INFO, logger="app.jdbc.jobs"),
        pytest.raises(SoftTimeLimitExceeded),
    ):
        await jobs.run_jdbc_collection(
            maker, instance, FakeRestProvider(walk), clock=fixed(NOW),
            lease_seconds=LEASE, walk_kwargs=KWARGS,
        )

    assert "CANARY-EXCEPTION-TEXT" not in caplog.text
    snapshots = await stored(db_session, iid)
    assert len(snapshots) == 1
    assert snapshots[0]["database_host"] == "old.example.com"  # interrupted write rolled back
    row = await sync(db_session, iid)
    assert row["state"] == "FAILED"
    assert row["failure_code"] == "soft_timeout"
    assert row["finished_at"] == NOW and row["lease_expires_at"] is None
    assert row["attempt_id"] is not None
    assert "CANARY-EXCEPTION-TEXT" not in str(row)


async def test_non_rest_unsupported(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = await seed(db_session)
    provider = MockQRadarProvider()
    calls = 0

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("a non-REST provider must not send a walk request")

    monkeypatch.setattr(type(provider), "walk_jdbc_configs", forbidden)

    outcome = await jobs.run_jdbc_collection(
        maker, instance, provider, clock=fixed(NOW), lease_seconds=LEASE, walk_kwargs=KWARGS
    )

    assert calls == 0
    assert outcome.state == "UNSUPPORTED"
    assert outcome.failure_code == "provider_unsupported"
    row = await sync(db_session, instance.id)
    assert row["state"] == "UNSUPPORTED"
    assert row["failure_code"] == "provider_unsupported"
    assert row["finished_at"] == NOW and row["lease_expires_at"] is None


async def test_job_result_logs_no_canaries(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    instance = await seed(db_session)
    walk = result(JdbcWalkState.COMPLETE, (config(100, canary=CANARY),))

    with caplog.at_level(logging.INFO, logger="app.jdbc.jobs"):
        outcome = await jobs.run_jdbc_collection(
            maker, instance, FakeRestProvider(walk), clock=fixed(NOW),
            lease_seconds=LEASE, walk_kwargs=KWARGS,
        )

    state_text = (
        await db_session.execute(text("SELECT to_jsonb(s)::text FROM jdbc_sync_state s"))
    ).scalar_one()
    config_text = (
        await db_session.execute(text("SELECT to_jsonb(c)::text FROM jdbc_source_config c"))
    ).scalar_one()
    assert CANARY not in caplog.text
    assert CANARY not in repr(outcome)
    assert CANARY not in state_text and CANARY not in config_text
    assert "jdbc collection run" in caplog.text


async def test_no_lock_held_during_walk(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    await jobs.request_jdbc_run(db_session, iid, now=NOW)
    await db_session.commit()
    observed: list[bool] = []

    async def probe() -> None:
        async with maker() as other:
            await other.execute(
                select(JdbcSyncState.__table__.c.instance_id)
                .where(JdbcSyncState.__table__.c.instance_id == iid)
                .with_for_update(nowait=True)
            )
            observed.append(True)

    walk = result(JdbcWalkState.COMPLETE, (config(100),))
    provider = FakeRestProvider(walk, during_walk=probe)
    outcome = await jobs.run_jdbc_collection(
        maker, instance, provider, clock=fixed(NOW), lease_seconds=LEASE, walk_kwargs=KWARGS
    )

    assert observed == [True]
    assert outcome.state == "COMPLETE"
    assert (await sync(db_session, iid))["state"] == "COMPLETE"


async def test_superseded_attempt_does_not_overwrite(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    lsid = (
        await db_session.execute(select(LogSource.id).where(LogSource.instance_id == iid))
    ).scalar_one()
    db_session.add(
        JdbcSourceConfig(
            log_source_id=lsid, protocol_type_id=8, field_states={}, allowlist_version=1,
            config_state="COMPLETE", database_host="old.example.com", observed_at=OLD,
        )
    )
    await db_session.commit()
    stolen: uuid.UUID | None = None

    async def steal() -> None:
        nonlocal stolen
        async with maker() as other:
            # Expire the lease so a real re-claim records worker_lost and takes
            # over with a new attempt, committed before B begins.
            await other.execute(
                update(JdbcSyncState.__table__)
                .where(JdbcSyncState.__table__.c.instance_id == iid)
                .values(lease_expires_at=NOW - timedelta(seconds=1))
            )
            stolen = await jobs.claim_jdbc_run(other, iid, now=NOW, lease_seconds=LEASE)
            await other.commit()

    walk = result(JdbcWalkState.COMPLETE, (config(100),))
    provider = FakeRestProvider(walk, during_walk=steal)
    outcome = await jobs.run_jdbc_collection(
        maker, instance, provider, clock=fixed(NOW), lease_seconds=LEASE, walk_kwargs=KWARGS
    )

    assert outcome.state == "superseded"
    assert outcome.apply is None
    snapshots = await stored(db_session, iid)
    assert len(snapshots) == 1
    assert snapshots[0]["database_host"] == "old.example.com"  # stale apply never landed
    assert snapshots[0]["observed_at"] == OLD
    row = await sync(db_session, iid)
    assert stolen is not None and row["attempt_id"] == stolen
    assert row["state"] == "RUNNING" and row["finished_at"] is None
    assert row["lease_expires_at"] is not None


async def test_clock_advances_during_walk(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    later = NOW + timedelta(minutes=10)
    current = [NOW]

    def clock() -> datetime:
        return current[0]

    async def advance() -> None:
        current[0] = later

    walk = result(JdbcWalkState.COMPLETE, (config(100),))
    provider = FakeRestProvider(walk, during_walk=advance)
    outcome = await jobs.run_jdbc_collection(
        maker, instance, provider, clock=clock, lease_seconds=LEASE, walk_kwargs=KWARGS
    )

    assert outcome.state == "COMPLETE"
    row = await sync(db_session, iid)
    assert row["started_at"] == NOW
    assert row["finished_at"] == later
    assert row["started_at"] < row["finished_at"]
    assert row["last_complete_at"] == later
    snapshots = await stored(db_session, iid)
    assert len(snapshots) == 1
    assert snapshots[0]["observed_at"] == later  # the post-walk reading


async def test_failure_path_times_at_failure_time(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    walk_end = NOW + timedelta(minutes=3)
    fail_at = NOW + timedelta(minutes=5)
    readings = iter([NOW, walk_end, fail_at])

    def clock() -> datetime:
        return next(readings)

    real_apply = jdbc_sync.apply_jdbc_walk

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        await real_apply(*args, **kwargs)
        raise SoftTimeLimitExceeded("CANARY-EXCEPTION-TEXT-4d2e")

    monkeypatch.setattr(jobs.jdbc_sync, "apply_jdbc_walk", exploding)
    walk = result(JdbcWalkState.COMPLETE, (config(100),))
    with pytest.raises(SoftTimeLimitExceeded):
        await jobs.run_jdbc_collection(
            maker, instance, FakeRestProvider(walk), clock=clock,
            lease_seconds=LEASE, walk_kwargs=KWARGS,
        )

    row = await sync(db_session, iid)
    assert row["started_at"] == NOW
    assert row["finished_at"] == fail_at  # the failure reading, not the post-walk one


async def test_b_holds_row_lock_through_apply(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = await seed(db_session)
    iid = instance.id
    observed: list[bool] = []
    real_apply = jdbc_sync.apply_jdbc_walk

    async def probing_apply(*args: Any, **kwargs: Any) -> Any:
        summary = await real_apply(*args, **kwargs)
        async with maker() as other:
            try:
                await other.execute(
                    select(JdbcSyncState.__table__.c.instance_id)
                    .where(JdbcSyncState.__table__.c.instance_id == iid)
                    .with_for_update(nowait=True)
                )
            except DBAPIError:
                observed.append(True)
            else:
                observed.append(False)
        return summary

    monkeypatch.setattr(jobs.jdbc_sync, "apply_jdbc_walk", probing_apply)
    walk = result(JdbcWalkState.COMPLETE, (config(100),))
    outcome = await jobs.run_jdbc_collection(
        maker, instance, FakeRestProvider(walk), clock=fixed(NOW),
        lease_seconds=LEASE, walk_kwargs=KWARGS,
    )

    assert outcome.state == "COMPLETE"
    assert observed == [True]  # B's FOR UPDATE lock is held until it commits
