"""LOCK-4: source-lock compatibility through real collection and rebuild entries."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import log_source_policy
from app.anomaly.baseline import BaselineBuilder
from app.anomaly.engine import SKIPPED_DISABLED_IN_QRADAR, AnomalyEngine
from app.collectors.metric_collector import COLLECTOR_NAME, MetricCollector
from app.core.config import Settings
from app.log_source_policy import lock_sources, rebuild_baselines_locked
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceBaseline, LogSourceMetric
from app.models.log_source_pause import LogSourcePause
from app.models.monitoring import CollectionWatermark
from app.providers.dto import (
    LogSourceDTO,
    LogSourceMetricBucketBatch,
    LogSourceMetricSample,
    LogSourceWalk,
)
from app.providers.mock import MockQRadarProvider
from app.services.inventory_sync import InventorySyncService
from app.workers import tasks
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_engine_source_flag import _assert_nothing_written

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
T0 = datetime(2026, 11, 21, 12, tzinfo=UTC)
STEP = timedelta(minutes=5)
NOW = T0 + 2 * STEP


def _settings():
    return Settings(_env_file=None, qradar_source_pause_enabled=True)


@pytest.fixture
async def rig(db_schema):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


async def _timeout(session):
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def _wait_blocked(maker, waiter, blocker, operation):
    """Prove the exact wait edge, with a third read-only observer connection."""
    async with maker() as observer, asyncio.timeout(4):
        while True:
            blockers = await observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter},
            )
            if blocker in blockers:
                assert not operation.done()
                return
            if operation.done():
                await operation
                pytest.fail("operation completed without the expected source-lock wait")
            await asyncio.sleep(0.01)


async def _seed(maker):
    async with maker() as session:
        instance = await make_instance(session)
        sources = [await make_log_source(
            session, instance, id=uuid.UUID(int=i), qradar_id=i,
            enabled=True, monitoring_enabled=True,
        ) for i in (1, 2)]
        # A previously collected low-source row makes the first upsert an UPDATE
        # for low, while high is a genuine INSERT. No FK value changes on UPDATE.
        await add_metric(session, sources[0], T0)
        session.add(CollectionWatermark(
            instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=T0 - STEP,
        ))
        await session.commit()
        return instance.id, [source.id for source in sources]


class TwoBuckets(MockQRadarProvider):
    async def get_log_source_metrics_bucketed(self, start, end, **kwargs):
        return LogSourceMetricBucketBatch(
            buckets={at: [LogSourceMetricSample(
                qradar_id=qid, bucket_start=at, event_count=300, average_eps=1.0,
            ) for qid in (2, 1)] for at in (T0, T0 + STEP)},
            rows_returned=4, aql="fixture: two complete aggregate buckets",
        )


@pytest.mark.parametrize("holder", ["evaluator", "rebuild"])
async def test_collector_and_source_holder_complete_without_deadlock(rig, monkeypatch, holder):
    """The source holder must not wait on the collector's child-insert FK lock."""
    _, maker = rig
    instance_id, ids = await _seed(maker)
    first_written, continue_collection = asyncio.Event(), asyncio.Event()
    async with maker() as collector_session, maker() as holder_session:
        collector_pid = await _timeout(collector_session)
        holder_pid = await _timeout(holder_session)
        instance = await collector_session.get(QRadarInstance, instance_id)
        collector = MetricCollector(
            collector_session, TwoBuckets(), settings=_settings(), clock=lambda: NOW,
        )
        original = collector._write_bucket

        async def write_then_yield(*args, **kwargs):
            written = await original(*args, **kwargs)
            if args[1] == T0:
                first_written.set()
                await continue_collection.wait()
            return written

        # Schedule only between complete real writes; neither SQL nor row order changes.
        monkeypatch.setattr(collector, "_write_bucket", write_then_yield)

        async def collect():
            try:
                report = await collector.collect(instance)
                await collector_session.commit()
                return report
            except BaseException:
                await collector_session.rollback()
                raise

        async def hold_sources():
            try:
                if holder == "rebuild":
                    return await rebuild_baselines_locked(
                        holder_session,
                        BaselineBuilder(holder_session, settings=_settings(),
                                        source_pause_policy=True),
                        ids, now=NOW, source_pause_policy=True,
                    )
                sources = await lock_sources(holder_session, ids)
                engine = AnomalyEngine(holder_session, settings=_settings())
                for source in sources:
                    await engine.evaluate_pending(source)
                await holder_session.commit()
            except BaseException:
                await holder_session.rollback()
                raise

        collection = asyncio.create_task(collect())
        operation = None
        try:
            async with asyncio.timeout(4):
                await first_written.wait()
            operation = asyncio.create_task(hold_sources())
            # Probe before releasing collection; retain the result until after the
            # real operations finish so a FOR UPDATE mutation exposes its deadlock.
            waited_on_collector = False
            async with maker() as observer:
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": holder_pid},
                    )
                    waited_on_collector |= collector_pid in blockers
                    await asyncio.sleep(0.01)
            continue_collection.set()
            async with asyncio.timeout(12):
                results = await asyncio.gather(collection, operation, return_exceptions=True)
            errors = [result for result in results if isinstance(result, BaseException)]
            assert not errors, [(type(exc).__name__, str(exc)) for exc in errors]
            assert results[0].buckets_collected == 2
            assert not waited_on_collector, "holder waited on collector FK KEY SHARE"
        finally:
            continue_collection.set()
            for task in (collection, operation):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[task for task in (collection, operation) if task is not None],
                return_exceptions=True,
            )


@pytest.mark.parametrize("first", ["evaluator", "rebuild"])
async def test_rebuild_and_evaluator_ascending_batches_do_not_cycle(rig, first):
    engine, maker = rig
    _, ids = await _seed(maker)
    async with maker() as rebuild, maker() as evaluator:
        rebuild_pid, evaluator_pid = await _timeout(rebuild), await _timeout(evaluator)

        async def train():
            return await rebuild_baselines_locked(
                rebuild, BaselineBuilder(rebuild, settings=_settings(), source_pause_policy=True),
                ids, now=NOW, source_pause_policy=True,
            )

        async def evaluate():
            sources = await lock_sources(evaluator, ids)
            detector = AnomalyEngine(evaluator, settings=_settings())
            for source in sources:
                await detector.evaluate_pending(source)
            await evaluator.commit()

        # Partial low hold makes ordering material: the other participant must
        # wait before taking high, which the first participant still needs.
        owner = evaluator if first == "evaluator" else rebuild
        await lock_sources(owner, [ids[0]])
        observations = []

        def record(conn, cursor, sql, *args):
            if "FROM log_source_metric" in sql:
                observations.append(sql)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        operation = asyncio.create_task(train() if first == "evaluator" else evaluate())
        try:
            await _wait_blocked(
                maker, rebuild_pid if first == "evaluator" else evaluator_pid,
                evaluator_pid if first == "evaluator" else rebuild_pid, operation,
            )
            assert observations == [], "observation read preceded the source lock"
            await (evaluate() if first == "evaluator" else train())
            await operation
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await rebuild.rollback()
            await evaluator.rollback()


async def test_rebuild_8k_batch_lock_budget(rig):
    """Measure sparse-history batches; this is not a production latency bound."""
    engine, maker = rig
    async with maker() as session:
        instance = await make_instance(session)
        ids = [uuid.UUID(int=i) for i in range(1, 8001)]
        for offset in range(0, len(ids), 100):
            batch = ids[offset:offset + 100]
            session.add_all([LogSource(
                id=source_id, instance_id=instance.id, qradar_id=source_id.int,
                name=f"source-{source_id.int}", enabled=True, monitoring_enabled=True,
            ) for source_id in batch])
            await session.flush()
            session.add_all([LogSourceMetric(
                log_source_id=source_id, bucket_start=T0 - timedelta(weeks=week),
                average_eps=1.0, event_count=300, bucket_seconds=300,
            ) for source_id in batch for week in range(1, 4)])
            await session.flush()
        await session.commit()
    durations, counts = [], []
    held_since = None
    statements = 0

    def statement(conn, cursor, sql, *args):
        nonlocal statements
        statements += 1

    def after_statement(conn, cursor, sql, *args):
        nonlocal held_since
        if "FOR NO KEY UPDATE" in sql or "FOR UPDATE" in sql:
            held_since = time.perf_counter()

    def after_commit(session):
        nonlocal statements, held_since
        assert held_since is not None
        durations.append(time.perf_counter() - held_since)
        counts.append(statements)
        statements, held_since = 0, None

    event.listen(engine.sync_engine, "before_cursor_execute", statement)
    event.listen(engine.sync_engine, "after_cursor_execute", after_statement)
    try:
        async with maker() as session:
            event.listen(session.sync_session, "after_commit", after_commit)
            try:
                cells = await rebuild_baselines_locked(
                    session,
                    BaselineBuilder(session, settings=_settings(), source_pause_policy=True),
                    ids, now=NOW, source_pause_policy=True,
                )
            finally:
                event.remove(session.sync_session, "after_commit", after_commit)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", statement)
        event.remove(engine.sync_engine, "after_cursor_execute", after_statement)
    assert len(durations) == 80
    assert counts == [302] * 80  # lock + bulk exclusion read + 3 per source
    assert cells == 16000
    print("LOCK3_BATCH_BUDGET " + json.dumps({
        "batches": len(durations), "statements_per_batch": counts[0],
        "observations_per_source": 3, "min_seconds": min(durations),
        "median_seconds": statistics.median(durations),
        "p95_seconds": sorted(durations)[75], "max_seconds": max(durations),
        "sum_seconds": sum(durations),
    }))


async def test_source_lock_compiles_to_no_key_update(rig):
    engine, maker = rig
    _, ids = await _seed(maker)
    compiled = []

    def record(conn, cursor, sql, parameters, context, executemany):
        if "FROM log_source " in sql and "FOR " in sql:
            statement = context.compiled.statement
            compiled.append(str(statement.compile(dialect=asyncpg.dialect())))
            assert statement.get_execution_options()["populate_existing"] is True

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with maker() as session:
            await lock_sources(session, ids)
            await session.rollback()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert len(compiled) == 1
    assert compiled[0].endswith("ORDER BY log_source.id FOR NO KEY UPDATE")
    assert "FOR UPDATE" not in compiled[0]


class StoredFalseInventory(MockQRadarProvider):
    async def list_log_source_types(self):
        return []

    async def list_log_source_groups(self):
        return []

    async def walk_log_sources(self, **kwargs):
        return LogSourceWalk(
            sources=[LogSourceDTO(qradar_id=1, name="src", enabled=False)],
            complete=True, pages_done=1,
        )


@pytest.mark.parametrize("entry", ["task", "task_retry", "pending", "interval", "rebuild"])
@pytest.mark.parametrize("concurrent_pause", [False, True], ids=["no-pause", "inventory-pause"])
async def test_stored_false_holder_skips_regardless_of_pause(
    rig, monkeypatch, entry, concurrent_pause,
):
    """Reconcile commits while the real holder keeps its lock: ordering proves no wait.

    Task candidates start enabled, then become stored-false before lock acquisition.
    This reaches the task's post-lock guard rather than only its selection filter.
    The retry case also reaches its isolated-source guard after a batch rollback.
    """
    engine, maker = rig
    settings = Settings(
        _env_file=None, qradar_source_pause_enabled=True,
        inventory_new_source_alerts_enabled=False, baseline_min_samples=3,
    )
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    async with maker() as session:
        instance = await make_instance(session)
        source = await make_log_source(
            session, instance, qradar_id=1, enabled=entry.startswith("task"),
            monitoring_enabled=True, group_ids=[], first_seen_at=T0 - timedelta(weeks=4),
        )
        # Enough complete history to train if the stored-flag guard regresses.
        for week in range(1, 4):
            await add_metric(session, source, T0 - timedelta(weeks=week))
        metric = await add_metric(session, source, T0)
        await session.commit()
        source_id, instance_id = source.id, instance.id

    original_lock = log_source_policy.lock_sources
    lock_calls = 0
    reconciles = 0
    evaluated = []
    trained = []
    statements = []
    original_pending = AnomalyEngine.evaluate_pending
    original_interval = AnomalyEngine.evaluate_interval
    original_many = BaselineBuilder.rebuild_many

    async def pending(self, row):
        result = await original_pending(self, row)
        evaluated.append(result)
        return result

    async def interval(self, row, bucket):
        result = await original_interval(self, row, bucket)
        evaluated.append(result)
        return result

    async def rebuild_many(self, rows, **kwargs):
        trained.extend(row.id for row in rows)
        return await original_many(self, rows, **kwargs)

    def record(conn, cursor, sql, *args):
        statements.append(sql)

    async def lock_then_reconcile(session, ids):
        nonlocal lock_calls, reconciles
        lock_calls += 1
        if entry.startswith("task") and lock_calls == 1:
            # Candidate IDs were already read by the real task. Persist only the
            # flag here, leaving the missing pause for the first reconcile below.
            async with maker() as writer:
                await _timeout(writer)
                await writer.execute(update(LogSource).where(
                    LogSource.id == source_id,
                ).values(enabled=False))
                await writer.commit()
        await session.execute(text("SET LOCAL lock_timeout = '1s'"))
        locked = await original_lock(session, ids)
        assert [row.enabled for row in locked] == [False]
        if concurrent_pause and reconciles == 0:
            # No task/future releases this holder during the inventory call. A
            # successful commit before returning proves FK compatibility without
            # a scheduling-dependent negative pg_blocking_pids observation.
            async with maker() as inventory, asyncio.timeout(4):
                await inventory.execute(text("SET LOCAL lock_timeout = '1s'"))
                inst = await inventory.get(QRadarInstance, instance_id)
                start = len(statements)
                await InventorySyncService(
                    inventory, StoredFalseInventory(), settings=settings, clock=lambda: NOW,
                ).sync(inst, previous_complete_watermark=T0)
                await inventory.commit()
                reconcile_sql = statements[start:]
                assert not any(sql.startswith("UPDATE log_source SET") for sql in reconcile_sql)
                assert any(sql.startswith("INSERT INTO log_source_pause") for sql in reconcile_sql)
            assert session.in_transaction()
            assert session.sync_session.get_transaction().is_active
            assert await session.scalar(select(func.count()).select_from(LogSourcePause)) == 1
            reconciles += 1
        if entry == "task_retry" and lock_calls == 1:
            raise RuntimeError("exercise isolated-source retry after locked batch")
        return locked

    monkeypatch.setattr(log_source_policy, "lock_sources", lock_then_reconcile)
    # Engine imports its lock helper at module scope; the task imports it in its body.
    monkeypatch.setattr("app.anomaly.engine.lock_sources", lock_then_reconcile)
    monkeypatch.setattr(AnomalyEngine, "evaluate_pending", pending)
    monkeypatch.setattr(AnomalyEngine, "evaluate_interval", interval)
    monkeypatch.setattr(BaselineBuilder, "rebuild_many", rebuild_many)
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        if entry.startswith("task"):
            result = await tasks.evaluate_anomalies()
            assert result == {"sources": 1, "opened": 0, "resolved": 0, "failed": 0, "given_up": 0}
            assert evaluated == []  # Task guard must skip even calling the engine.
        else:
            async with maker() as holder:
                row = await holder.get(LogSource, source_id)
                if entry == "rebuild":
                    cells = await rebuild_baselines_locked(
                        holder,
                        BaselineBuilder(holder, settings=settings, source_pause_policy=True),
                        [source_id], now=NOW, source_pause_policy=True,
                    )
                    assert cells == 0
                else:
                    detector = AnomalyEngine(holder, settings=settings)
                    report = await (
                        detector.evaluate_pending(row) if entry == "pending"
                        else detector.evaluate_interval(row, metric)
                    )
                    assert report.skipped_reason == SKIPPED_DISABLED_IN_QRADAR
                    assert report.opened == report.resolved == [] and report.states == {}
                    await holder.commit()
        assert lock_calls == (2 if entry == "task_retry" else 1)
        assert reconciles == int(concurrent_pause)
        assert trained == []
        async with maker() as observer:
            row = await observer.get(LogSource, source_id)
            assert row.enabled is False
            await _assert_nothing_written(observer, row)
            assert await observer.scalar(select(func.count()).select_from(LogSourceBaseline)) == 0
            assert await observer.scalar(select(func.count()).select_from(LogSourcePause)) == int(
                concurrent_pause
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
