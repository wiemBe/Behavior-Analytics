"""P7: transaction-owned source locks and both inventory/evaluator interleavings."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.anomaly.engine import (
    SKIPPED_DISABLED_IN_QRADAR,
    SKIPPED_OVERLAPS_PAUSE,
    SKIPPED_SOURCE_REMOVED,
    AnomalyEngine,
    EvaluationReport,
)
from app.core.config import Settings
from app.log_source_policy import lock_sources
from app.models.alert import Alert
from app.models.base import utcnow
from app.models.enums import AnomalyState, AnomalyType
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.monitoring import AnomalyEvaluationFault, AnomalyEvaluationWatermark
from app.services.source_incident_closure import close_for_disabled_sources
from app.services.source_pause_spans import open_pause
from app.workers import tasks
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_engine_source_pauses import _eps_baseline

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
T0 = utc()


def _settings(pause=True):
    return Settings(_env_file=None, qradar_source_pause_enabled=pause)


@pytest.fixture
async def rig(db_schema, monkeypatch):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", _settings)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


@contextmanager
def _record(engine):
    operations = []

    def statement(conn, cursor, sql, params, *args):
        operations.append((sql, params))

    def commit(conn):
        operations.append(("COMMIT", ()))

    event.listen(engine.sync_engine, "before_cursor_execute", statement)
    event.listen(engine.sync_engine, "commit", commit)
    try:
        yield operations
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", statement)
        event.remove(engine.sync_engine, "commit", commit)


def _locks(operations):
    return [(sql, params) for sql, params in operations if "FOR UPDATE" in sql]


async def _seed(maker, count=1, *, metrics=False):
    async with maker() as session:
        instance = await make_instance(session)
        # Deliberately insert in reverse UUID order, independently of qradar_id.
        sources = [
            await make_log_source(
                session, instance, qradar_id=index, id=uuid.UUID(int=index),
                enabled=True, monitoring_enabled=True,
            )
            for index in range(count, 0, -1)
        ]
        if metrics:
            for source in sources:
                await _eps_baseline(session, source, T0)
                for index in range(2):
                    await add_metric(
                        session, source, T0 + timedelta(minutes=5 * index),
                        average_eps=3.0, event_count=900, stored_event_count=900,
                    )
        await session.commit()
    return sorted(source.id for source in sources)


async def _timeout(session):
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def _wait_blocked(maker, pid, operation):
    """Observe an actual PostgreSQL lock wait, rather than relying on a sleep."""
    async with maker() as observer:
        async with asyncio.timeout(4):
            while True:
                blocked = await observer.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                )
                if blocked:
                    assert not operation.done()
                    return
                if operation.done():
                    await operation
                    pytest.fail("operation completed without waiting for the source lock")
                await asyncio.sleep(0.01)


async def _no_writes(session, source_id):
    for model in (
        LogSourceAnomaly, LogSourceDetectorState, AnomalyEvaluationWatermark, AnomalyEvaluationFault
    ):
        assert await session.scalar(
            select(func.count()).select_from(model).where(model.log_source_id == source_id)
        ) == 0
    assert await session.scalar(select(func.count()).select_from(AnomalyStateTransition)) == 0
    assert await session.scalar(select(func.count()).select_from(Alert)) == 0


async def _no_active(maker, source_id):
    async with maker() as session:
        assert (await session.get(LogSource, source_id)).enabled is False
        assert await session.scalar(
            select(func.count()).select_from(LogSourceAnomaly).where(
                LogSourceAnomaly.log_source_id == source_id,
                LogSourceAnomaly.anomaly_type.in_(
                    [AnomalyType.NO_EVENTS, AnomalyType.VOLUME_DROP, AnomalyType.VOLUME_SPIKE]
                ),
                LogSourceAnomaly.state.in_(
                    [AnomalyState.CANDIDATE, AnomalyState.OPEN, AnomalyState.RECOVERING]
                ),
            )
        ) == 0


async def test_evaluation_locks_at_most_100_uuid_order(rig):
    engine, maker = rig
    ids = await _seed(maker, 250)
    with _record(engine) as operations:
        result = await tasks.evaluate_anomalies()
    assert result == dict(sources=250, opened=0, resolved=0, failed=0, given_up=0)
    locks = _locks(operations)
    assert len(locks) == 3
    assert [list(params) for _, params in locks] == [ids[:100], ids[100:200], ids[200:]]
    assert all("ORDER BY log_source.id FOR UPDATE" in sql for sql, _ in locks)
    assert ["lock" if "FOR UPDATE" in sql else "commit" for sql, _ in operations
            if "FOR UPDATE" in sql or sql == "COMMIT"] == ["lock", "commit"] * 3
    fleet_reads = [sql for sql, _ in operations
                   if "FROM log_source" in sql and "FOR UPDATE" not in sql
                   and "FROM log_source_metric" not in sql]
    assert len(fleet_reads) == 1 and fleet_reads[0].startswith("SELECT log_source.id ")


@pytest.mark.parametrize("entry", ["evaluate_interval", "evaluate_pending", "evaluate_latest"])
async def test_direct_interval_refreshes_after_wait(rig, entry):
    _, maker = rig
    [source_id] = await _seed(maker, metrics=True)
    async with maker() as inventory, maker() as evaluator:
        source = await evaluator.get(LogSource, source_id)
        metric = await evaluator.scalar(select(LogSourceMetric).limit(1))
        await _timeout(inventory)
        pid = await _timeout(evaluator)
        locked = (await lock_sources(inventory, [source_id]))[0]
        locked.enabled = False
        await inventory.flush()
        engine = AnomalyEngine(evaluator, settings=_settings())
        call = getattr(engine, entry)
        operation = asyncio.create_task(
            call(source, metric) if entry == "evaluate_interval" else call(source)
        )
        try:
            await _wait_blocked(maker, pid, operation)
            await inventory.commit()
            report = await operation
            assert source.enabled is False
            assert report.skipped_reason == SKIPPED_DISABLED_IN_QRADAR
            await evaluator.commit()
            await _no_writes(evaluator, source_id)
        finally:
            await inventory.rollback()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("entry", ["evaluate_pending", "evaluate_interval"])
async def test_pause_spans_are_read_after_wait(rig, entry):
    _, maker = rig
    [source_id] = await _seed(maker, metrics=True)
    async with maker() as inventory, maker() as evaluator:
        source = await evaluator.get(LogSource, source_id)
        metric = await evaluator.scalar(select(LogSourceMetric).order_by(
            LogSourceMetric.bucket_start.desc()
        ).limit(1))
        await _timeout(inventory)
        pid = await _timeout(evaluator)
        await lock_sources(inventory, [source_id])
        await open_pause(
            inventory, [source_id], reason="QRADAR_DISABLED", starts_at=T0, now=T0
        )
        engine = AnomalyEngine(evaluator, settings=_settings())
        operation = asyncio.create_task(
            engine.evaluate_pending(source) if entry == "evaluate_pending"
            else engine.evaluate_interval(source, metric)
        )
        try:
            await _wait_blocked(maker, pid, operation)
            await inventory.commit()
            assert (await operation).skipped_reason == SKIPPED_OVERLAPS_PAUSE
            await evaluator.commit()
            assert await evaluator.scalar(select(func.count()).select_from(LogSourceAnomaly)) == 0
        finally:
            await inventory.rollback()
            await asyncio.gather(operation, return_exceptions=True)


async def test_two_sessions_disable_first(rig):
    _, maker = rig
    [source_id] = await _seed(maker, metrics=True)
    async with maker() as session:
        source = await session.get(LogSource, source_id)
        engine = AnomalyEngine(session, settings=_settings())
        for metric in (await session.scalars(select(LogSourceMetric).order_by(
            LogSourceMetric.bucket_start
        ))).all():
            await engine.evaluate_interval(source, metric)
        await session.commit()
    # Leave one bucket unevaluated. Without the source lock the evaluator would
    # judge it and commit a fresh active incident after the disable, so _no_active
    # proves the invariant rather than only the wait observation.
    async with maker() as session:
        source = await session.get(LogSource, source_id)
        await add_metric(
            session, source, T0 + timedelta(minutes=10),
            average_eps=3.0, event_count=900, stored_event_count=900,
        )
        await session.commit()
    async with maker() as inventory, maker() as evaluator:
        source = await evaluator.get(LogSource, source_id)
        await _timeout(inventory)
        pid = await _timeout(evaluator)
        locked = (await lock_sources(inventory, [source_id]))[0]
        locked.enabled = False
        summary = await close_for_disabled_sources(inventory, [source_id], observed_at=utcnow())
        assert summary.incidents_closed > 0
        operation = asyncio.create_task(
            AnomalyEngine(evaluator, settings=_settings()).evaluate_pending(source)
        )
        try:
            await _wait_blocked(maker, pid, operation)
            await inventory.commit()
            report = await operation
            await evaluator.commit()
            # Check the persisted invariant before the report: a missing lock can
            # only be caught here by an incident that survived the disable.
            await _no_active(maker, source_id)
            assert report.skipped_reason == SKIPPED_DISABLED_IN_QRADAR
        finally:
            await inventory.rollback()
            await asyncio.gather(operation, return_exceptions=True)


async def test_two_sessions_evaluate_first(rig):
    _, maker = rig
    [source_id] = await _seed(maker, metrics=True)
    async with maker() as evaluator, maker() as inventory:
        await _timeout(evaluator)
        pid = await _timeout(inventory)
        source = await evaluator.get(LogSource, source_id)
        engine = AnomalyEngine(evaluator, settings=_settings())
        with _record(rig[0]) as operations:
            for metric in (await evaluator.scalars(select(LogSourceMetric).order_by(
                LogSourceMetric.bucket_start
            ))).all():
                await engine.evaluate_interval(source, metric)
        assert len(_locks(operations)) == 1
        assert await evaluator.scalar(select(func.count()).select_from(LogSourceAnomaly)) > 0

        async def disable():
            locked = (await lock_sources(inventory, [source_id]))[0]
            locked.enabled = False
            summary = await close_for_disabled_sources(
                inventory, [source_id], observed_at=utcnow()
            )
            assert summary.incidents_closed > 0
            await inventory.commit()

        operation = asyncio.create_task(disable())
        try:
            await _wait_blocked(maker, pid, operation)
            await evaluator.commit()
            await operation
        finally:
            await evaluator.rollback()
            await asyncio.gather(operation, return_exceptions=True)
    await _no_active(maker, source_id)


@pytest.mark.parametrize("row", [None, False])
async def test_policy_off_preserves_existing_behavior(rig, monkeypatch, row):
    engine, maker = rig
    ids = await _seed(maker, 101, metrics=True)
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(False))
    async with maker() as session:
        (await session.get(LogSource, ids[0])).enabled = False
        if row is not None:
            session.add(FeatureSwitch(
                key="qradar_source_pause_enabled", enabled=row,
                updated_at=utcnow(), updated_by="test",
            ))
        await session.commit()
    # Base commit's off-path body, writes discarded. The real task must reproduce
    # reports and persisted watermarks from identical inputs.
    async with maker() as session:
        legacy = AnomalyEngine(session, settings=_settings(False))
        sources = (await session.scalars(select(LogSource).where(
            LogSource.monitoring_enabled.is_(True)
        ))).all()
        reports = [await legacy.evaluate_pending(source) for source in sources]
        expected = dict(
            sources=len(sources), opened=sum(len(r.opened) for r in reports),
            resolved=sum(len(r.resolved) for r in reports),
            failed=sum(r.failed for r in reports), given_up=sum(r.given_up for r in reports),
        )
        expected_marks = list((await session.execute(select(
            AnomalyEvaluationWatermark.log_source_id, AnomalyEvaluationWatermark.evaluated_through
        ).order_by(AnomalyEvaluationWatermark.log_source_id))).all())
        await session.rollback()
    with _record(engine) as operations:
        assert await tasks.evaluate_anomalies() == expected
    assert not _locks(operations)
    assert sum(sql == "COMMIT" for sql, _ in operations) == 1
    async with maker() as session:
        assert list((await session.execute(select(
            AnomalyEvaluationWatermark.log_source_id, AnomalyEvaluationWatermark.evaluated_through
        ).order_by(AnomalyEvaluationWatermark.log_source_id))).all()) == expected_marks


async def test_policy_off_failure_propagates_without_commit(rig, monkeypatch):
    engine, maker = rig
    await _seed(maker, 2)
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(False))

    async def broken(self, source):
        raise RuntimeError("off-path failure")

    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", broken)
    with _record(engine) as operations, pytest.raises(RuntimeError, match="off-path failure"):
        await tasks.evaluate_anomalies()
    assert not _locks(operations)
    assert not any(sql == "COMMIT" for sql, _ in operations)


@pytest.mark.parametrize("failure", ["evaluation", "commit", "lock"])
async def test_batch_failure_rolls_back_only_that_batch(rig, monkeypatch, caplog, failure):
    engine, maker = rig
    ids = await _seed(maker, 250)

    class FailingSession(AsyncSession):
        commits = 0

        async def commit(self):
            self.commits += 1
            if failure == "commit" and self.commits == 2:
                raise RuntimeError("batch commit failure")
            await super().commit()

        async def scalars(self, statement, *args, **kwargs):
            if failure == "lock" and "FOR UPDATE" in str(statement):
                params = statement.compile().params
                if ids[100] in next(iter(params.values())):
                    raise RuntimeError("batch lock failure")
            return await super().scalars(statement, *args, **kwargs)

    failing_maker = async_sessionmaker(engine, class_=FailingSession, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: failing_maker)

    async def evaluate(self, source):
        if failure == "evaluation" and source.id == ids[149]:
            raise RuntimeError("batch evaluation failure")
        self.session.add(AnomalyEvaluationWatermark(
            log_source_id=source.id, evaluated_through=T0, updated_at=T0
        ))
        await self.session.flush()
        return EvaluationReport(source.id, T0, opened=["VOLUME_DROP"], failed=1, given_up=1)

    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", evaluate)
    result = await tasks.evaluate_anomalies()
    # The failed batch is retried source by source: every source but the one that
    # fails alone is still evaluated and committed.
    opened, given_up, missing = {
        "evaluation": (249, 249, ids[149]),
        "commit": (250, 250, None),
        "lock": (249, 249, ids[100]),
    }[failure]
    assert result == dict(sources=250, opened=opened, resolved=0, failed=250, given_up=given_up)
    async with maker() as session:
        persisted = list((await session.scalars(select(
            AnomalyEvaluationWatermark.log_source_id
        ).order_by(AnomalyEvaluationWatermark.log_source_id))).all())
    assert persisted == ([i for i in ids if i != missing] if missing is not None else ids)
    records = [r for r in caplog.records if r.message == "anomaly evaluation batch rolled back"]
    assert len(records) == 1
    assert records[0].batch_offset == 100 and records[0].batch_sources == 100
    retries = [r for r in caplog.records
               if r.message == "anomaly evaluation source failed in batch retry"]
    assert len(retries) == (0 if failure == "commit" else 1)


@pytest.mark.parametrize("boundary", ["commit", "rollback", "savepoint_rollback"])
async def test_lock_ownership_ends_with_transaction(rig, boundary):
    engine, maker = rig
    [source_id] = await _seed(maker)
    async with maker() as session:
        nested = await session.begin_nested() if boundary == "savepoint_rollback" else None
        await lock_sources(session, [source_id])
        if nested:
            await nested.rollback()
        else:
            await getattr(session, boundary)()
        with _record(engine) as operations:
            await lock_sources(session, [source_id])
        assert len(_locks(operations)) == 1


async def test_lock_helper_rejects_oversized_batch(rig):
    engine, maker = rig
    async with maker() as session:
        with _record(engine) as operations, pytest.raises(ValueError, match="exceeds 100"):
            await lock_sources(session, [uuid.UUID(int=i) for i in range(101)])
        assert operations == []


async def test_task_rechecks_disabled_after_batch_wait(rig, monkeypatch):
    _, maker = rig
    [source_id] = await _seed(maker)
    calls = []

    async def evaluate(self, source):
        calls.append(source.id)
        return EvaluationReport(source.id, None)

    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", evaluate)
    started = asyncio.Event()
    pids = []

    class WaitingSession(AsyncSession):
        async def scalars(self, statement, *args, **kwargs):
            if "FOR UPDATE" in str(statement):
                pids.append(await _timeout(self))
                started.set()
            return await super().scalars(statement, *args, **kwargs)

    waiting_maker = async_sessionmaker(rig[0], class_=WaitingSession, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: waiting_maker)
    async with maker() as inventory:
        await _timeout(inventory)
        locked = (await lock_sources(inventory, [source_id]))[0]
        locked.enabled = False
        await inventory.flush()
        operation = asyncio.create_task(tasks.evaluate_anomalies())
        try:
            await asyncio.wait_for(started.wait(), 4)
            await _wait_blocked(maker, pids[0], operation)
            await inventory.commit()
            assert (await operation)["sources"] == 1
            assert calls == []
        finally:
            await inventory.rollback()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("entry", ["evaluate_pending", "evaluate_interval"])
async def test_direct_entry_refreshes_reenable_after_wait(rig, entry):
    _, maker = rig
    [source_id] = await _seed(maker, metrics=True)
    async with maker() as setup:
        (await setup.get(LogSource, source_id)).enabled = False
        await setup.commit()
    async with maker() as inventory, maker() as evaluator:
        source = await evaluator.get(LogSource, source_id)
        metric = await evaluator.scalar(select(LogSourceMetric).limit(1))
        await _timeout(inventory)
        pid = await _timeout(evaluator)
        locked = (await lock_sources(inventory, [source_id]))[0]
        locked.enabled = True
        await inventory.flush()
        engine = AnomalyEngine(evaluator, settings=_settings())
        operation = asyncio.create_task(
            engine.evaluate_pending(source) if entry == "evaluate_pending"
            else engine.evaluate_interval(source, metric)
        )
        try:
            await _wait_blocked(maker, pid, operation)
            await inventory.commit()
            assert (await operation).skipped_reason is None
            assert source.enabled is True
            await evaluator.commit()
            assert await evaluator.scalar(select(func.count()).select_from(
                LogSourceDetectorState
            )) > 0
        finally:
            await inventory.rollback()
            await asyncio.gather(operation, return_exceptions=True)


async def test_source_failure_retains_engine_savepoint_handling(rig, monkeypatch):
    _, maker = rig
    ids = await _seed(maker, 2, metrics=True)
    original = AnomalyEngine.evaluate_interval

    async def broken(self, source, metric):
        if source.id == ids[0]:
            raise RuntimeError("single source failure")
        return await original(self, source, metric)

    monkeypatch.setattr(AnomalyEngine, "evaluate_interval", broken)
    result = await tasks.evaluate_anomalies()
    assert result == dict(sources=2, opened=0, resolved=0, failed=1, given_up=0)
    async with maker() as session:
        assert await session.get(AnomalyEvaluationWatermark, ids[0]) is None
        assert await session.get(AnomalyEvaluationWatermark, ids[1]) is not None
        faults = list((await session.scalars(select(AnomalyEvaluationFault))).all())
        assert len(faults) == 1 and faults[0].log_source_id == ids[0]


async def test_committed_savepoint_retains_lock_ownership(rig):
    engine, maker = rig
    [source_id] = await _seed(maker)
    async with maker() as session:
        with _record(engine) as operations:
            async with session.begin_nested():
                async with session.begin_nested():
                    await lock_sources(session, [source_id])
                await lock_sources(session, [source_id])
            await lock_sources(session, [source_id])
        assert len(_locks(operations)) == 1
        await session.rollback()
        with _record(engine) as operations:
            await lock_sources(session, [source_id])
        assert len(_locks(operations)) == 1


@pytest.mark.parametrize("entry", ["evaluate_pending", "evaluate_interval"])
async def test_direct_entry_skips_removed_source(rig, entry):
    _, maker = rig
    [source_id] = await _seed(maker, metrics=True)
    async with maker() as session:
        source = await session.get(LogSource, source_id)
        metric = await session.scalar(select(LogSourceMetric).limit(1))
    async with maker() as session:
        await session.delete(await session.get(LogSource, source_id))
        await session.commit()
    async with maker() as session:
        engine = AnomalyEngine(session, settings=_settings())
        call = getattr(engine, entry)
        report = await (call(source) if entry == "evaluate_pending" else call(source, metric))
        assert report.skipped_reason == SKIPPED_SOURCE_REMOVED
        await session.commit()
        await _no_writes(session, source_id)


async def test_lock_helper_scans_only_requested_ids(rig):
    _, maker = rig
    ids = await _seed(maker, 3)
    async with maker() as session:
        await lock_sources(session, ids)

        class CountingDict(dict):
            items_calls = 0

            def items(self):
                type(self).items_calls += 1
                return super().items()

        counting = CountingDict(session.info["source_pause_locks"])
        session.info["source_pause_locks"] = counting
        with _record(rig[0]) as operations:
            assert [source.id for source in await lock_sources(session, [ids[1]])] == [ids[1]]
        assert counting.items_calls == 0
        assert not _locks(operations)


async def test_engine_reused_across_batches(rig, monkeypatch):
    engine, maker = rig
    await _seed(maker, 250)

    async def evaluate(self, source):
        # A real once-per-run load, cached on the engine instance after the first call.
        await self._open_fault_sources()
        return EvaluationReport(source.id, None)

    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", evaluate)
    with _record(engine) as operations:
        await tasks.evaluate_anomalies()
    loads = [sql for sql, _ in operations if "FROM anomaly_evaluation_fault" in sql]
    assert len(loads) == 1


async def test_poison_source_does_not_starve_batch(rig, monkeypatch):
    _, maker = rig
    ids = await _seed(maker, 3)

    async def evaluate(self, source):
        if source.id == ids[1]:
            raise RuntimeError("poison source")
        self.session.add(AnomalyEvaluationWatermark(
            log_source_id=source.id, evaluated_through=T0, updated_at=T0
        ))
        await self.session.flush()
        return EvaluationReport(source.id, T0, opened=["VOLUME_DROP"])

    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", evaluate)
    result = await tasks.evaluate_anomalies()
    assert result == dict(sources=3, opened=2, resolved=0, failed=1, given_up=0)
    async with maker() as session:
        persisted = list((await session.scalars(select(
            AnomalyEvaluationWatermark.log_source_id
        ).order_by(AnomalyEvaluationWatermark.log_source_id))).all())
    assert persisted == [ids[0], ids[2]]
