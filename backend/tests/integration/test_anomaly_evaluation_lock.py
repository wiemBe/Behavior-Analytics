"""FIX-45d: anomaly evaluation runs one at a time across the process.

The tests drive the task itself (`evaluate_anomalies`) against PostgreSQL and
hold its advisory lock on another session, the way a second worker would. The
engine is stubbed so a call is visible: a skipped run must reach neither the
engine nor the source query.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager, contextmanager

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.anomaly.engine import AnomalyEngine, EvaluationReport
from app.core.config import get_settings
from app.services.locks import CollectorAdvisoryLock, collector_lock_key
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_EVALUATION_KEY = collector_lock_key(
    tasks.ANOMALY_EVALUATION_LOCK_ID, "anomaly_evaluation"
)
_SCHEDULER_KEY = collector_lock_key(
    tasks.SEARCH_SCHEDULER_LOCK_ID, "search_scheduler"
)
_LOCK_HELD = """
    SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND objsubid = 2
      AND classid = :ns AND objid = :key
      AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
"""
_NORMAL = {"sources": 1, "opened": 0, "resolved": 0, "failed": 0, "given_up": 0}


class _Evaluation:
    """The engine stub: records every call, and raises while `fail` is set."""

    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []
        self.fail = False

    async def __call__(self, engine, log_source):  # type: ignore[no-untyped-def]
        self.calls.append(log_source.id)
        if self.fail:
            raise RuntimeError("engine fault")
        return EvaluationReport(log_source.id, None, skipped_reason="stub")


@contextmanager
def _statements(engine):  # type: ignore[no-untyped-def]
    """The SQL statements the engine executes inside the block."""
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


@pytest.fixture
async def env(db_schema, monkeypatch):
    """One monitoring-enabled source and a recording engine, on this test's schema."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    evaluation = _Evaluation()
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)

    async def evaluate_pending(engine, log_source):  # type: ignore[no-untyped-def]
        return await evaluation(engine, log_source)

    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", evaluate_pending)
    async with maker() as session:
        instance = await make_instance(session)
        await make_log_source(session, instance)
        await session.commit()
    try:
        yield engine, maker, evaluation
    finally:
        await engine.dispose()


@asynccontextmanager
async def _held(maker, lock_id: uuid.UUID, name: str):
    """Hold `lock_id`/`name` on a session of its own, as another worker does."""
    async with maker() as session, CollectorAdvisoryLock(
        session, get_settings(), lock_id, name
    ) as acquired:
        assert acquired, "the test must take the lock it holds"
        yield


async def _lock_held(maker, key: int) -> int:
    async with maker() as session:
        return await session.scalar(
            text(_LOCK_HELD),
            {"ns": get_settings().collection_advisory_lock_namespace, "key": key},
        )


async def test_a_run_while_the_lock_is_held_skips_and_evaluates_nothing(
    env, caplog
) -> None:
    engine, maker, evaluation = env
    caplog.set_level(logging.INFO, logger="app.tasks")

    async with _held(maker, tasks.ANOMALY_EVALUATION_LOCK_ID, "anomaly_evaluation"):
        assert await _lock_held(maker, _EVALUATION_KEY) == 1
        with _statements(engine) as statements:
            assert await tasks.evaluate_anomalies() == {"status": "skipped_locked"}
        assert evaluation.calls == [], "the skipped run reached the engine"
        assert not [
            statement for statement in statements if "FROM log_source" in statement
        ], "the skipped run queried no source"
        assert await _lock_held(maker, _EVALUATION_KEY) == 1, (
            "the skipper left the holder's lock alone"
        )

    skipped = [
        record
        for record in caplog.records
        if record.name == "app.tasks" and "already in progress" in record.getMessage()
    ]
    assert len(skipped) == 1
    assert "anomaly evaluation" in skipped[0].getMessage()
    assert skipped[0].levelno == logging.INFO
    assert await _lock_held(maker, _EVALUATION_KEY) == 0


async def test_after_the_holder_releases_the_next_run_evaluates(env) -> None:
    _, maker, evaluation = env

    async with _held(maker, tasks.ANOMALY_EVALUATION_LOCK_ID, "anomaly_evaluation"):
        assert await tasks.evaluate_anomalies() == {"status": "skipped_locked"}

    result = await tasks.evaluate_anomalies()

    assert result == _NORMAL, "the acquired run keeps the existing result shape"
    assert len(evaluation.calls) == 1
    assert await _lock_held(maker, _EVALUATION_KEY) == 0


async def test_a_failed_run_releases_the_lock(env) -> None:
    _, maker, evaluation = env
    evaluation.fail = True

    with pytest.raises(RuntimeError, match="engine fault"):
        await tasks.evaluate_anomalies()
    assert len(evaluation.calls) == 1

    assert await _lock_held(maker, _EVALUATION_KEY) == 0, "the failure released the lock"
    evaluation.fail = False
    result = await tasks.evaluate_anomalies()
    assert result == _NORMAL
    assert len(evaluation.calls) == 2


async def test_the_search_scheduler_lock_does_not_skip_the_evaluation(env) -> None:
    _, maker, evaluation = env
    assert _EVALUATION_KEY != _SCHEDULER_KEY, "the two tasks do not share a key"

    async with _held(maker, tasks.SEARCH_SCHEDULER_LOCK_ID, "search_scheduler"):
        assert await tasks.evaluate_anomalies() == _NORMAL
    assert len(evaluation.calls) == 1
    assert await _lock_held(maker, _EVALUATION_KEY) == 0


async def test_a_lock_held_in_another_database_is_not_counted(env) -> None:
    """`pg_locks` is cluster-wide: the held-lock query must count this database only."""
    import os

    from sqlalchemy.engine import make_url

    _engine, maker, _evaluation = env
    namespace = get_settings().collection_advisory_lock_namespace
    url = make_url(os.environ["TEST_DATABASE_URL"])
    other = None
    for name in ("postgres", "template1"):
        if name == url.database:
            continue
        candidate = create_async_engine(url.set(database=name))
        try:
            async with candidate.connect():
                other = candidate
                break
        except Exception:
            await candidate.dispose()
    if other is None:
        pytest.skip("no second database reachable on the test server")
    unscoped = _LOCK_HELD.split("      AND database")[0]
    try:
        async with other.connect() as foreign:
            await foreign.execute(
                text("SELECT pg_advisory_lock(:ns, :key)"),
                {"ns": namespace, "key": _EVALUATION_KEY},
            )
            try:
                async with maker() as session:
                    params = {"ns": namespace, "key": _EVALUATION_KEY}
                    assert await session.scalar(text(unscoped), params) == 1
                assert await _lock_held(maker, _EVALUATION_KEY) == 0
            finally:
                await foreign.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"),
                    {"ns": namespace, "key": _EVALUATION_KEY},
                )
    finally:
        await other.dispose()
