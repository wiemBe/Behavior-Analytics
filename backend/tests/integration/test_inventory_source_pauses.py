"""P4: actual inventory edges, transaction rollback and ordered source-row locks."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts import dispatcher
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.anomaly.engine import SKIPPED_DISABLED_IN_QRADAR, AnomalyEngine
from app.collectors.log_source_collector import COLLECTOR_NAME, LogSourceCollector
from app.core.config import Settings
from app.log_source_policy import lock_sources
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, AnomalyState, AnomalyType, NotificationChannel
from app.models.instance import QRadarInstance
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
)
from app.models.log_source_pause import LogSourcePause
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderUnavailableError
from app.providers.dto import LogSourceDTO, LogSourceWalk
from app.providers.mock import MockQRadarProvider
from app.services import inventory_sync, source_incident_closure
from app.services.inventory_sync import InventorySyncService
from app.services.source_pause_spans import open_pause
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_direction_tuning import _cells
from tests.integration.test_engine_source_pauses import _eps_baseline
from tests.integration.test_source_pause_closure import _incident
from tests.integration.test_source_pause_races import _timeout, _wait_blocked

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
T0 = utc()
PREVIOUS = T0 + timedelta(hours=1)
NOW = T0 + timedelta(hours=2, seconds=1)
REASON = "QRADAR_DISABLED"


def _settings(pause=True, **kwargs):
    return Settings(
        _env_file=None, qradar_source_pause_enabled=pause,
        inventory_new_source_alerts_enabled=False, **kwargs,
    )


class SourceProvider(MockQRadarProvider):
    def __init__(self, values, *, complete=True, failed=False):
        super().__init__()
        self.values = values
        self.complete = complete
        self.failed = failed

    async def list_log_source_types(self):
        return []

    async def list_log_source_groups(self):
        return []

    async def walk_log_sources(self, **kwargs):
        if self.failed:
            raise ProviderUnavailableError("inventory unavailable")
        return LogSourceWalk(
            sources=[LogSourceDTO(qradar_id=qid, name=f"seen-{qid}", enabled=value)
                     for qid, value in self.values],
            complete=self.complete, pages_done=1,
            pages_failed=0 if self.complete else 1,
            failed_page=None if self.complete else 2,
            failure_kind=None if self.complete else "timeout",
        )


@pytest.fixture
async def rig(db_schema):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


async def _seed(maker, flags=(True,), *, findings=False, starts=None):
    async with maker() as session:
        instance = await make_instance(session)
        sources = []
        for index, enabled in enumerate(flags, 1):
            source = await make_log_source(
                session, instance, id=uuid.UUID(int=index), qradar_id=index,
                enabled=enabled, monitoring_enabled=True,
                first_seen_at=starts[index - 1] if starts else T0,
            )
            sources.append(source)
            if findings:
                await _incident(
                    session, source, AnomalyType.VOLUME_DROP, AnomalyState.OPEN,
                    alert=AlertStatus.OPEN,
                )
                await _cells(session, source)
        session.add(CollectionWatermark(
            instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=PREVIOUS,
        ))
        await session.commit()
        return instance.id, [source.id for source in sources]


async def _sync(session, instance_id, values, *, complete=True, pause=True, now=NOW,
                previous=PREVIOUS, collector=False, failed=False, bucket_seconds=300):
    instance = await session.get(QRadarInstance, instance_id)
    provider = SourceProvider(values, complete=complete, failed=failed)
    settings = _settings(pause, collection_interval_seconds=bucket_seconds)
    if collector:
        return await LogSourceCollector(
            session, provider, settings=settings, clock=lambda: now,
        ).sync(instance)
    return await InventorySyncService(
        session, provider, settings=settings, clock=lambda: now,
    ).sync(instance, previous_complete_watermark=previous)


@contextmanager
def _record(engine):
    operations = []

    def record(conn, cursor, sql, parameters, context, executemany):
        operations.append((sql, parameters, context.compiled.positiontup
                           if context.compiled else None, executemany))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield operations
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


def _edge_statements(operations):
    tables = ("log_source_pause", "log_source_anomaly", "log_source_detector_state",
              "log_source_baseline", "anomaly_state_transition", "alert")
    return [sql for sql, *_ in operations if any(table in sql for table in tables)]


async def _assert_edge(maker, source_id, *, applied):
    async with maker() as session:
        assert (await session.get(LogSource, source_id)).enabled is (not applied)
        spans = (await session.scalars(select(LogSourcePause))).all()
        assert len(spans) == int(applied)
        if applied:
            assert spans[0].starts_at == PREVIOUS and spans[0].ends_at is None
        incident = await session.scalar(select(LogSourceAnomaly))
        assert incident.state == (AnomalyState.RESOLVED if applied else AnomalyState.OPEN)
        assert incident.resolved_at == (NOW if applied else None)
        alert = await session.scalar(select(Alert))
        assert alert.status == (AlertStatus.RESOLVED if applied else AlertStatus.OPEN)
        cells = (await session.scalars(select(LogSourceBaseline))).all()
        assert cells and all(cell.is_reliable is (not applied) for cell in cells)
        state = await session.scalar(select(LogSourceDetectorState))
        assert state.state == (AnomalyState.INSUFFICIENT_DATA if applied else AnomalyState.OPEN)
        transitions = (await session.scalars(select(AnomalyStateTransition))).all()
        assert len(transitions) == int(applied)
        if applied:
            assert transitions[0].actor == "system:inventory-sync"
            assert transitions[0].bucket_start is None
            assert transitions[0].observed_value is None


async def test_actual_sync_atomic_flag_span_closure(rig):
    _, maker = rig
    instance_id, [source_id] = await _seed(maker, findings=True)
    async with maker() as session:
        result = await _sync(session, instance_id, [(1, False)], collector=True)
        assert result.status == "COMPLETE"
        # Collector leaves the transaction to its worker; no part escaped early.
        await _assert_edge(maker, source_id, applied=False)
        await session.commit()
    await _assert_edge(maker, source_id, applied=True)
    async with maker() as session:
        watermark = await session.scalar(select(CollectionWatermark))
        assert watermark.watermark_at == NOW


async def test_partial_seen_edge_unseen_untouched(rig):
    _, maker = rig
    instance_id, ids = await _seed(maker, (True, True), findings=True)
    async with maker() as session:
        result = await _sync(session, instance_id, [(1, False)], complete=False, collector=True)
        assert result.status == "PARTIAL"
        await session.commit()
    async with maker() as session:
        assert (await session.get(LogSource, ids[0])).enabled is False
        assert (await session.get(LogSource, ids[1])).enabled is True
        spans = (await session.scalars(select(LogSourcePause))).all()
        assert [(s.log_source_id, s.starts_at) for s in spans] == [(ids[0], PREVIOUS)]
        incidents = (await session.scalars(select(LogSourceAnomaly).order_by(
            LogSourceAnomaly.log_source_id
        ))).all()
        assert [row.state for row in incidents] == [AnomalyState.RESOLVED, AnomalyState.OPEN]
        cells = (await session.scalars(select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ids[1]
        ))).all()
        assert all(c.is_reliable for c in cells)
        assert (await session.scalar(select(CollectionWatermark))).watermark_at == PREVIOUS


@pytest.mark.parametrize("helper", ["open_pause", "close_for_disabled_sources", "close_pause"])
async def test_apply_error_rolls_back_whole_edge(rig, monkeypatch, helper):
    _, maker = rig
    reenable = helper == "close_pause"
    instance_id, [source_id] = await _seed(maker, (not reenable,), findings=True)
    if reenable:
        async with maker() as session:
            await open_pause(session, [source_id], reason=REASON, starts_at=PREVIOUS, now=NOW)
            await session.commit()
    original = getattr(inventory_sync, helper)

    async def broken(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("edge apply failed")

    monkeypatch.setattr(inventory_sync, helper, broken)
    async with maker() as session:
        with pytest.raises(RuntimeError, match="edge apply failed"):
            await _sync(session, instance_id, [(1, reenable)], collector=True)
        await session.rollback()
    if not reenable:
        await _assert_edge(maker, source_id, applied=False)
    else:
        async with maker() as session:
            assert (await session.get(LogSource, source_id)).enabled is False
            assert (await session.scalar(select(LogSourcePause))).ends_at is None
            assert (await session.scalar(select(LogSourceAnomaly))).state == AnomalyState.OPEN
            assert (await session.scalar(select(CollectionWatermark))).watermark_at == PREVIOUS


@pytest.mark.parametrize("integrity", [True, False])
async def test_notification_failure_rolls_back_edge(rig, monkeypatch, integrity):
    _, maker = rig
    instance_id, [source_id] = await _seed(maker, findings=True)
    settings = _settings(notify_send_recovery=True)
    monkeypatch.setattr(dispatcher, "get_settings", lambda: settings)
    monkeypatch.setattr(source_incident_closure, "default_policy", lambda: RoutingPolicy([
        RoutingRule(NotificationChannel.WEBHOOK, "https://hooks.example.test/pause")
    ]))
    attempts = []

    def fail(mapper, connection, target):
        attempts.append(target.alert_id)
        if integrity:
            raise IntegrityError("notification insert", {}, ValueError("injected integrity"))
        raise RuntimeError("notification enqueue failed")

    event.listen(AlertNotification, "before_insert", fail)
    try:
        async with maker() as session:
            if integrity:
                await _sync(session, instance_id, [(1, False)], collector=True)
                await session.commit()
            else:
                with pytest.raises(RuntimeError, match="notification enqueue failed"):
                    await _sync(session, instance_id, [(1, False)], collector=True)
                await session.rollback()
    finally:
        event.remove(AlertNotification, "before_insert", fail)
    assert len(attempts) == 1
    await _assert_edge(maker, source_id, applied=integrity)
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(AlertNotification)) == 0


async def test_first_reconcile_false_idempotent(rig, monkeypatch):
    engine, maker = rig
    instance_id, [source_id] = await _seed(maker, (False,), findings=True)
    opened = AsyncMock(wraps=inventory_sync.open_pause)
    closed = AsyncMock(wraps=inventory_sync.close_for_disabled_sources)
    monkeypatch.setattr(inventory_sync, "open_pause", opened)
    monkeypatch.setattr(inventory_sync, "close_for_disabled_sources", closed)
    async with maker() as session:
        await _sync(session, instance_id, [(1, False)])
        await session.commit()
    await _assert_edge(maker, source_id, applied=True)
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, False)], now=NOW + timedelta(hours=1))
            await session.commit()
    assert opened.await_count == closed.await_count == 1
    edge_sql = _edge_statements(operations)
    assert len(edge_sql) == 1 and edge_sql[0].startswith("SELECT log_source_pause")
    await _assert_edge(maker, source_id, applied=True)


@pytest.mark.parametrize("values", [
    [False, True], [True, False], [False, False], [False, None], [True, None],
])
async def test_duplicate_dtos_decide_one_edge_from_stored_flag(rig, monkeypatch, values):
    _, maker = rig
    instance_id, [source_id] = await _seed(maker, findings=True)
    helpers = {}
    for name in ("open_pause", "close_pause", "close_for_disabled_sources"):
        helpers[name] = AsyncMock(wraps=getattr(inventory_sync, name))
        monkeypatch.setattr(inventory_sync, name, helpers[name])
    async with maker() as session:
        await _sync(session, instance_id, [(1, value) for value in values])
        await session.commit()
    disabled = values[-1] is False
    await _assert_edge(maker, source_id, applied=disabled)
    helpers["close_pause"].assert_not_awaited()
    for name in ("open_pause", "close_for_disabled_sources"):
        if disabled:
            helpers[name].assert_awaited_once()
            assert helpers[name].await_args.args[1] == [source_id]
        else:
            helpers[name].assert_not_awaited()


@pytest.mark.parametrize("mode", ["none", "stale", "partial-empty", "failed", "repeat-true"])
async def test_stale_or_none_never_writes_span(rig, mode):
    engine, maker = rig
    instance_id, ids = await _seed(maker, (True, False), findings=True)
    values = [(1, None), (2, None)] if mode == "none" else []
    if mode in {"stale", "repeat-true"}:
        values = [(1, True)]
    if mode == "stale":
        async with maker() as session:
            watermark = await session.scalar(select(CollectionWatermark))
            watermark.watermark_at = T0 - timedelta(days=7)
            await session.commit()
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, values, complete=mode != "partial-empty",
                        failed=mode == "failed", collector=True)
            await session.commit()
    edge_sql = _edge_statements(operations)
    if mode in {"stale", "repeat-true"}:
        assert len(edge_sql) == 1 and edge_sql[0].startswith("SELECT log_source_pause")
    else:
        assert edge_sql == []
    async with maker() as session:
        assert [(await session.get(LogSource, sid)).enabled for sid in ids] == [True, False]
        assert await session.scalar(select(func.count()).select_from(LogSourcePause)) == 0
        assert set(await session.scalars(select(LogSourceAnomaly.state))) == {AnomalyState.OPEN}


@pytest.mark.parametrize("seconds,expected", [(0, 0), (1, 600)])
async def test_reenable_closes_at_observation_rounded_up(rig, seconds, expected):
    _, maker = rig
    instance_id, [source_id] = await _seed(maker, (False,))
    async with maker() as session:
        await open_pause(session, [source_id], reason=REASON, starts_at=PREVIOUS, now=NOW)
        await session.commit()
        observed = T0 + timedelta(hours=2, seconds=seconds)
        await _sync(session, instance_id, [(1, True)], now=observed, bucket_seconds=600)
        await session.commit()
    async with maker() as session:
        span = await session.scalar(select(LogSourcePause))
        assert span.ends_at == T0 + timedelta(hours=2, seconds=expected)
        assert (await session.get(LogSource, source_id)).enabled is True


@pytest.mark.parametrize("value", [False, True, None])
async def test_new_source_explicit_flag(rig, value):
    engine, maker = rig
    instance_id, _ = await _seed(maker, ())
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, value)])
            await session.commit()
    async with maker() as session:
        source = await session.scalar(select(LogSource))
        assert source.enabled is (value is True)
        assert source.first_seen_at == NOW
        spans = (await session.scalars(select(LogSourcePause))).all()
        assert len(spans) == int(value is False)
        if value is False:
            assert spans[0].starts_at == NOW
        else:
            assert _edge_statements(operations) == []


@pytest.mark.parametrize("previous", [None, PREVIOUS])
async def test_direct_sync_start_falls_back_to_first_seen(rig, previous):
    _, maker = rig
    first_seen = PREVIOUS + timedelta(minutes=5)
    instance_id, _ = await _seed(maker, starts=[first_seen])
    async with maker() as session:
        await _sync(session, instance_id, [(1, False)], previous=previous)
        await session.commit()
        assert (await session.scalar(select(LogSourcePause))).starts_at == first_seen


async def test_policy_off_no_pause_or_closure_statements(rig):
    engine, maker = rig
    instance_id, _ = await _seed(maker, (True, False), findings=True)
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, False), (2, True), (3, False)], pause=False)
            await session.commit()
    assert _edge_statements(operations) == []
    async with maker() as session:
        assert set(await session.scalars(select(LogSourceAnomaly.state))) == {AnomalyState.OPEN}
        assert all(await session.scalars(select(LogSourceBaseline.is_reliable)))


@pytest.mark.parametrize("count,groups", [(3, 1), (30, 3)])
async def test_bulk_edge_statement_counts(rig, monkeypatch, count, groups):
    engine, maker = rig
    starts = [PREVIOUS + timedelta(minutes=i % groups) for i in range(count)]
    instance_id, ids = await _seed(maker, (False,) * count + (False,), starts=[*starts, T0])
    async with maker() as session:
        await open_pause(session, [ids[-1]], reason=REASON, starts_at=T0, now=NOW)
        await session.commit()
    helpers = {}
    for name in ("open_pause", "close_pause", "close_for_disabled_sources"):
        helpers[name] = AsyncMock(wraps=getattr(inventory_sync, name))
        monkeypatch.setattr(inventory_sync, name, helpers[name])
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(i, False) for i in range(count, 0, -1)]
                        + [(count + 1, True)])
            await session.commit()
    assert helpers["open_pause"].await_count == groups
    assert sorted(len(c.args[1]) for c in helpers["open_pause"].await_args_list) == [
        count // groups
    ] * groups
    helpers["close_pause"].assert_awaited_once()
    helpers["close_for_disabled_sources"].assert_awaited_once()
    assert set(helpers["close_for_disabled_sources"].await_args.args[1]) == set(ids[:-1])
    sql = [s for s, *_ in operations]
    assert sum(s.startswith("SELECT log_source_pause.log_source_id") for s in sql) == 1
    assert sum(s.startswith("INSERT INTO log_source_pause") for s in sql) == groups
    assert sum(s.startswith("UPDATE log_source_pause") for s in sql) == 1
    assert sum("FROM log_source_anomaly" in s for s in sql) == 1
    for helper in helpers.values():
        for call in helper.await_args_list:
            assert call.args[1] == sorted(call.args[1])
    [(read_sql, params, _, _)] = [op for op in operations if op[0].startswith(
        "SELECT log_source_pause.log_source_id"
    )]
    assert "= ANY (" in read_sql
    assert len(params) == 2  # One UUID array and the reason, independent of fleet size.
    assert list(params[0]) == ids


async def test_reenable_helper_receives_sorted_ids(rig, monkeypatch):
    _, maker = rig
    instance_id, ids = await _seed(maker, (False,) * 3)
    closed = AsyncMock(wraps=inventory_sync.close_pause)
    monkeypatch.setattr(inventory_sync, "close_pause", closed)
    async with maker() as session:
        await open_pause(session, ids, reason=REASON, starts_at=PREVIOUS, now=NOW)
        await session.commit()
        await _sync(session, instance_id, [(3, True), (2, True), (1, True)])
        await session.commit()
    closed.assert_awaited_once()
    assert closed.await_args.args[1] == ids
    async with maker() as session:
        assert all(await session.scalars(select(LogSourcePause.ends_at)))


async def test_flush_updates_ascending_ids(rig):
    engine, maker = rig
    instance_id, ids = await _seed(maker, (True,) * 300)
    updated = []
    phase = {"assigned": 0, "edge": False}

    def assigned(target, value, oldvalue, initiator):
        assert not phase["edge"], "source attribute assigned after the first edge statement"
        phase["assigned"] += 1

    def statement(conn, cursor, sql, params, context, executemany):
        if sql.startswith("UPDATE log_source SET"):
            assert phase["assigned"] == len(ids), "autoflush inside the source assignment loop"
            offset = context.compiled.positiontup.index("log_source_id")
            updated.extend(uuid.UUID(str(p[offset])) for p in (params if executemany else [params]))
        if sql.startswith("INSERT INTO log_source_pause"):
            assert updated == ids, "edge ran before every source UPDATE"
            phase["edge"] = True

    # Attribute listeners cover every source column, not only the enabled flag.
    def after_edge(target, value, oldvalue, initiator):
        assert not phase["edge"], "source attribute assigned after the first edge statement"

    callbacks = [(getattr(LogSource, column.key), assigned if column.key == "name" else after_edge)
                 for column in LogSource.__table__.columns]
    for attribute, callback in callbacks:
        event.listen(attribute, "set", callback)
    event.listen(engine.sync_engine, "before_cursor_execute", statement)
    try:
        async with maker() as session:
            await _sync(session, instance_id, [(i, i % 2 == 0) for i in range(300, 0, -1)])
            await session.commit()
        assert phase["edge"] and updated == ids
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", statement)
        for attribute, callback in callbacks:
            event.remove(attribute, "set", callback)


@pytest.mark.parametrize("first", ["disable", "engine"])
async def test_two_sessions_engine_batch_vs_sync_no_deadlock(rig, first):
    _, maker = rig
    instance_id, ids = await _seed(maker, (True,) * 3)
    async with maker() as setup:
        source = await setup.get(LogSource, ids[1])
        await _eps_baseline(setup, source, T0)
        first_metric = None
        for i in range(2):
            metric = await add_metric(setup, source, T0 + timedelta(minutes=i * 5),
                                      average_eps=3.0, event_count=900, stored_event_count=900)
            if i == 0:
                first_metric = metric
        # Build the first confirmation, leaving a bucket that can open an incident.
        await AnomalyEngine(setup, settings=_settings()).evaluate_interval(source, first_metric)
        await setup.commit()
    async with maker() as inventory, maker() as evaluator:
        inventory_pid = await _timeout(inventory)
        evaluator_pid = await _timeout(evaluator)
        for session in (inventory, evaluator):
            await session.execute(text("SET LOCAL deadlock_timeout = '1s'"))
        # Prime the evaluator with a stale enabled=True ORM object, as in the real task.
        source = await evaluator.get(LogSource, ids[1])
        engine = AnomalyEngine(evaluator, settings=_settings())

        async def evaluate_batch():
            batch = await lock_sources(evaluator, ids)
            assert [s.id for s in batch] == ids
            return await engine.evaluate_pending(source)

        async def sync():
            return await _sync(inventory, instance_id, [(3, True), (2, False), (1, True)])

        if first == "disable":
            # Partial hold first: evaluator waits on low while inventory still needs high.
            await lock_sources(inventory, [ids[0]])
            operation = asyncio.create_task(evaluate_batch())
            try:
                await _wait_blocked(maker, evaluator_pid, operation)
                await sync()
                async with maker() as observer:
                    # No source changes may escape before the edge transaction commits.
                    old_rows = (await observer.scalars(select(LogSource).order_by(
                        LogSource.id
                    ))).all()
                    assert [row.enabled for row in old_rows] == [True, True, True]
                    assert [row.name for row in old_rows] == ["src", "src", "src"]
                await inventory.commit()
                result = await operation
                await evaluator.commit()
                assert result.skipped_reason == SKIPPED_DISABLED_IN_QRADAR
            finally:
                await inventory.rollback()
                await asyncio.gather(operation, return_exceptions=True)
        else:
            # Retry-style partial hold: a descending sync would hold high and wait on low,
            # then our request for high below would form a real cycle.
            await lock_sources(evaluator, [ids[0]])
            operation = asyncio.create_task(sync())
            try:
                await _wait_blocked(maker, inventory_pid, operation)
                await lock_sources(evaluator, [ids[-1]])
                result = await evaluate_batch()
                assert result.opened
                await evaluator.commit()
                await operation
                await inventory.commit()
            finally:
                await evaluator.rollback()
                await asyncio.gather(operation, return_exceptions=True)
    async with maker() as session:
        assert (await session.get(LogSource, ids[1])).enabled is False
        assert await session.scalar(select(func.count()).select_from(LogSourceAnomaly).where(
            LogSourceAnomaly.state.in_([AnomalyState.CANDIDATE, AnomalyState.OPEN,
                                       AnomalyState.RECOVERING])
        )) == 0
        span = await session.scalar(select(LogSourcePause))
        assert span.log_source_id == ids[1] and span.starts_at == PREVIOUS and span.ends_at is None
        assert [(await session.get(LogSource, sid)).name for sid in ids] == [
            "seen-1", "seen-2", "seen-3"
        ]
        if first == "engine":
            incident = await session.scalar(select(LogSourceAnomaly))
            assert incident.state == AnomalyState.RESOLVED and incident.resolved_at == NOW
