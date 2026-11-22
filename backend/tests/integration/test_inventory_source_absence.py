"""X3b writer, operator entry, SQL contract and real PostgreSQL lock schedules."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import log_source_policy
from app.anomaly.engine import SKIPPED_OVERLAPS_PAUSE, AnomalyEngine
from app.cli import doctor as doctor_cli
from app.collectors.log_source_collector import COLLECTOR_NAME, LogSourceCollector
from app.core.config import Settings
from app.i18n.messages import MessageRef, render
from app.jdbc.dto import JdbcConfigWalk, JdbcWalkState
from app.models.enums import AlertStatus, AnomalyState, AnomalyType
from app.models.feature_switch import FeatureSwitch
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig
from app.models.log_source import AnomalyStateTransition, LogSource, LogSourceAnomaly
from app.models.log_source_pause import LogSourcePause
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderUnavailableError
from app.providers.dto import LogSourceDTO, LogSourceWalk
from app.providers.mock import MockQRadarProvider
from app.repositories.jdbc import JdbcRepository
from app.services import inventory_sync
from app.services.inventory_sync import absence_source_lock_statement
from app.services.jdbc_sync import apply_jdbc_walk
from app.services.source_pause_spans import open_pause
from app.workers import tasks
from tests.integration.factories import add_metric, make_instance
from tests.integration.test_engine_source_pauses import _count_baseline
from tests.integration.test_source_pause_closure import _incident

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 22, 12, 1, tzinfo=UTC)
BEFORE = NOW - timedelta(minutes=10)
SENTENCE = "Closed without a verdict: source absent from the completed QRadar inventory."
CODE = "anomaly.source_absent_without_verdict"
BACKEND = Path(__file__).resolve().parents[2]


def settings(on=True):
    return Settings(_env_file=None, qradar_source_pause_enabled=on,
                    inventory_new_source_alerts_enabled=False, feature_switches_db=False)


class Inventory(MockQRadarProvider):
    def __init__(self, count=3):
        super().__init__()
        self.ids = list(range(1, count + 1))
        self.complete = self.validated = True
        self.rows_dropped = self.duplicate_ids = 0
        self.failed = self.types_failed = False
        self.enabled = True
        self.name = "source"

    async def list_log_source_types(self):
        if self.types_failed:
            raise ProviderUnavailableError("fixture")
        return []

    async def list_log_source_groups(self):
        return []

    async def walk_log_sources(self, **kwargs):
        if self.failed:
            raise ProviderUnavailableError("fixture")
        return LogSourceWalk(
            sources=[LogSourceDTO(qradar_id=q, name=f"{self.name}-{q}",
                                  enabled=self.enabled, protocol_type_id=8) for q in self.ids],
            complete=self.complete, validated=self.validated, rows_dropped=self.rows_dropped,
            duplicate_ids=self.duplicate_ids, pages_done=1,
        )


@pytest.fixture
async def rig(db_schema):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


async def sync(session, instance, provider, *, on=True, now=NOW):
    return await LogSourceCollector(session, provider, settings=settings(on),
                                    clock=lambda: now).sync(instance)


async def mark(session, instance):
    return await session.scalar(select(CollectionWatermark).where(
        CollectionWatermark.instance_id == instance.id,
        CollectionWatermark.collector == COLLECTOR_NAME,
    ))


async def spans(session):
    return list(await session.scalars(select(LogSourcePause).where(
        LogSourcePause.reason == "ABSENT").order_by(LogSourcePause.log_source_id)))


async def seed(session, count=3):
    instance = await make_instance(session)
    provider = Inventory(count)
    await sync(session, instance, provider, now=BEFORE)
    sources = {s.qradar_id: s for s in await session.scalars(select(LogSource).where(
        LogSource.instance_id == instance.id))}
    return instance, provider, sources


@contextmanager
def recorded(engine):
    operations = []

    def record(conn, cursor, sql, params, context, many):
        operations.append((sql, params, context.compiled))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield operations
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


@pytest.mark.parametrize("age", [1, 48])
async def test_complete_absence_closes_without_verdict(db_session, age):
    instance, provider, sources = await seed(db_session)
    source = sources[3]
    source.monitoring_enabled = True
    db_session.add(JdbcSourceConfig(
        log_source_id=source.id, protocol_type_id=8, database_host="db.example.test",
        field_states={}, allowlist_version=1, config_state="COMPLETE", observed_at=BEFORE,
        last_success_at=BEFORE,
    ))
    incident, alert = await _incident(db_session, source, AnomalyType.NO_EVENTS,
                                     AnomalyState.OPEN, alert=AlertStatus.ACKNOWLEDGED)
    provider.ids = [1, 2]
    first = await sync(db_session, instance, provider, now=NOW - timedelta(hours=age))
    assert first.absence["confirmed"] == 0 and await spans(db_session) == []
    second = await sync(db_session, instance, provider)
    assert second.absence["confirmed"] == 1
    [pause] = await spans(db_session)
    assert pause.log_source_id == source.id and pause.ends_at is None
    assert pause.starts_at == max(NOW - timedelta(hours=age), NOW - timedelta(hours=24))
    assert source.enabled is True
    await db_session.refresh(incident)
    await db_session.refresh(alert)
    assert incident.state == AnomalyState.RESOLVED and incident.resolved_at == NOW
    assert alert.status == AlertStatus.RESOLVED and alert.resolution_reason == SENTENCE
    assert alert.resolution_reason_i18n == MessageRef(CODE).to_wire()
    [transition] = list(await db_session.scalars(select(AnomalyStateTransition).where(
        AnomalyStateTransition.anomaly_id == incident.id)))
    assert transition.reason == SENTENCE and transition.reason_i18n == MessageRef(CODE).to_wire()
    assert transition.bucket_start is transition.observed_value is transition.expected_value is None
    assert render(MessageRef(CODE)) == SENTENCE
    config = await db_session.get(JdbcSourceConfig, source.id)
    assert config.database_host == "db.example.test" and config.observed_at == BEFORE
    metric = await add_metric(db_session, source, NOW.replace(minute=0, second=0),
                              average_eps=0.0, event_count=0)
    result = await AnomalyEngine(db_session, settings=settings()).evaluate_interval(source, metric)
    assert result.skipped_reason == SKIPPED_OVERLAPS_PAUSE and result.states == {}
    page = await JdbcRepository(db_session, settings()).list(
        now=NOW, cadence_seconds=300, width_seconds=300, anchor=0,
        inventory_cadence_seconds=3600, include_absent=True)
    absent = next(row for row in page.items if row.id == source.id)
    assert absent.flow.state == "ABSENT_FROM_INVENTORY"
    assert absent.volume.event_count is None


@pytest.mark.parametrize("bad", ["partial", "failed", "types", "malformed", "duplicate",
                                 "unvalidated", "empty"])
async def test_partial_failed_malformed_or_duplicate_walk_never_absent(db_session, bad):
    instance, provider, _ = await seed(db_session)
    provider.ids = [1, 2]
    await sync(db_session, instance, provider)
    provider.complete = bad != "partial"
    provider.failed = bad == "failed"
    provider.types_failed = bad == "types"
    provider.rows_dropped = int(bad == "malformed")
    provider.duplicate_ids = int(bad == "duplicate")
    provider.validated = bad != "unvalidated"
    if bad == "empty":
        provider.ids = []
    for _ in range(2):
        report = await sync(db_session, instance, provider)
        assert report.absence["state"] == "skipped" and report.absence["confirmed"] == 0
        assert await spans(db_session) == []


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("enabled", [True, False, None])
async def test_reappearance_preserves_qradar_false_and_closes_only_absent(
    db_session, complete, enabled,
):
    instance, provider, sources = await seed(db_session)
    source = sources[3]
    source.enabled = False
    await db_session.flush()
    await open_pause(db_session, [source.id], reason="QRADAR_DISABLED", starts_at=BEFORE, now=NOW)
    provider.ids = [1, 2]
    await sync(db_session, instance, provider)
    await sync(db_session, instance, provider)
    provider.ids = [3]
    provider.enabled = enabled
    provider.complete = complete
    provider.validated = False  # positive presence does not require validation
    await sync(db_session, instance, provider, now=NOW + timedelta(seconds=1))
    [pause] = await spans(db_session)
    assert pause.ends_at == NOW.replace(minute=5, second=0)
    disabled = await db_session.scalar(select(LogSourcePause).where(
        LogSourcePause.log_source_id == source.id, LogSourcePause.reason == "QRADAR_DISABLED"))
    assert source.enabled is (enabled if enabled is not None else False)
    assert (disabled.ends_at is None) is (enabled is not True)


async def test_absence_idempotent_and_held_has_no_write(rig):
    engine, maker = rig
    async with maker() as session:
        instance, provider, sources = await seed(session, 23)
        provider.ids = [1, 2]
        for _ in range(3):
            report = await sync(session, instance, provider)
            assert report.absence["state"] == "held" and await spans(session) == []
        provider.ids = list(range(1, 23))
        await sync(session, instance, provider)
        await sync(session, instance, provider)
        [pause] = await spans(session)
        assert pause.log_source_id == sources[23].id
        # A new incident after an absence must not be re-closed by later syncs.
        incident, _ = await _incident(
            session, sources[23], AnomalyType.NO_EVENTS, AnomalyState.OPEN)
        for _ in range(4):
            with recorded(engine) as seen:
                report = await sync(session, instance, provider)
            assert report.absence["candidates"] == report.absence["confirmed"] == 0
            assert not any("INSERT INTO log_source_pause" in sql or
                           "UPDATE log_source_anomaly" in sql for sql, _, _ in seen)
        assert len(await spans(session)) == 1
        assert incident.state == AnomalyState.OPEN


@pytest.mark.parametrize("where", ["closure", "later"])
async def test_absence_transaction_rollback_and_race(rig, monkeypatch, where):
    _, maker = rig
    async with maker() as session:
        instance, provider, sources = await seed(session)
        incident, _ = await _incident(session, sources[3], AnomalyType.NO_EVENTS, AnomalyState.OPEN)
        provider.ids = [1, 2]
        await sync(session, instance, provider)
        instance_id, incident_id = instance.id, incident.id
        await session.commit()
    original = inventory_sync.close_for_disabled_sources

    async def failure(*args, **kwargs):
        if where == "later":
            await original(*args, **kwargs)
        raise RuntimeError("edge failure")

    monkeypatch.setattr(inventory_sync, "close_for_disabled_sources", failure)
    async with maker() as session:
        instance = await session.get(QRadarInstance, instance_id)
        with pytest.raises(RuntimeError, match="edge failure"):
            await sync(session, instance, provider)
        await session.rollback()
    async with maker() as session:
        assert await spans(session) == []
        assert (await session.get(LogSourceAnomaly, incident_id)).state == AnomalyState.OPEN
        assert (await session.scalar(select(CollectionWatermark))).collection_metadata[
            "absence_candidates"]


async def test_missing_parameter_page_not_source_deletion(db_session):
    instance, provider, _ = await seed(db_session)
    walk = JdbcConfigWalk(configs=[], state=JdbcWalkState.PARTIAL)
    for _ in range(2):
        await apply_jdbc_walk(db_session, instance.id, walk, now=NOW)
        await sync(db_session, instance, provider)
    assert await spans(db_session) == []


def cli(url, *args, **overrides):
    parsed = make_url(url)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432), "PYTHONPATH": str(BACKEND),
        "FEATURE_SWITCHES_DB": "false", "QRADAR_SOURCE_PAUSE_ENABLED": "true",
    } | overrides
    return subprocess.run(  # noqa: S603 — actual CLI entry, isolated test database
        [sys.executable, "-m", "app.cli.inventory", "absence", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=30, check=False,
    )


async def test_accept_cli_entry_lifts_only_breaker_until_confirmation(rig, db_schema):
    _, maker = rig
    async with maker() as session:
        instance, provider, _ = await seed(session, 23)
        name, instance_id = instance.name, instance.id
        provider.ids = [1, 2]
        assert (await sync(session, instance, provider)).absence["state"] == "held"
        await session.commit()
    shown = cli(db_schema, "show", "--instance", name)
    assert shown.returncode == 0 and "held: 21; accept armed: no" in shown.stdout
    refused = cli(db_schema, "accept", "--instance", name, QRADAR_SOURCE_PAUSE_ENABLED="false")
    assert refused.returncode == 2 and "pauses are off" in refused.stderr
    accepted = cli(db_schema, "accept", "--instance", name, "--actor", "operator")
    assert accepted.returncode == 0 and "two-pass evidence is required" in accepted.stdout
    again = cli(db_schema, "accept", "--instance", name)
    assert again.returncode == 0 and "already armed" in again.stdout
    shown = cli(db_schema, "show", "--instance", name)
    assert shown.returncode == 0 and "accept armed: yes" in shown.stdout
    async with maker() as session:
        instance = await session.get(QRadarInstance, instance_id)
        watermark = await mark(session, instance)
        assert watermark.collection_metadata["absence_accept"]["actor"] == "operator"
        [audit] = list(await session.scalars(select(AuditLog)))
        assert audit.action == "inventory.absence.accept" and audit.actor == "operator"
        assert audit.object_id == str(instance_id) and audit.detail == {"held_count": 21}
        provider.validated = False
        assert (await sync(session, instance, provider)).absence["state"] == "skipped"
        assert "absence_accept" in watermark.collection_metadata and await spans(session) == []
        provider.validated = True
        assert (await sync(session, instance, provider)).absence["candidates"] == 21
        assert "absence_accept" in watermark.collection_metadata and await spans(session) == []
        provider.complete = False
        await sync(session, instance, provider)
        assert "absence_accept" in watermark.collection_metadata and await spans(session) == []
        provider.complete = True
        report = await sync(session, instance, provider)
        assert report.absence["confirmed"] == 21 and len(await spans(session)) == 21
        assert "absence_accept" not in watermark.collection_metadata
        await session.commit()
    refused = cli(db_schema, "accept", "--instance", name)
    assert refused.returncode == 2 and "not held" in refused.stderr
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.parametrize("case", ["missing", "not_held", "bad_count", "database"])
async def test_accept_cli_other_entry_exits(rig, db_schema, case):
    _, maker = rig
    name = "missing-instance"
    if case != "missing":
        async with maker() as session:
            instance, _, _ = await seed(session)
            name = instance.name
            if case == "bad_count":
                watermark = await mark(session, instance)
                watermark.collection_metadata = {"inventory_pass": {
                    "status": "COMPLETE", "absence": {"state": "held", "held_count": "bad"}}}
            await session.commit()
    result = cli(db_schema, "accept", "--instance", name,
                 **({"POSTGRES_PORT": "1"} if case == "database" else {}))
    assert result.returncode == (1 if case == "database" else 2)
    assert ("Inventory command failed" if case == "database" else "refused") in result.stderr
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_doctor_held_command_and_armed_clause(rig, monkeypatch, capsys):
    _, maker = rig
    async with maker() as session:
        instance, provider, _ = await seed(session, 23)
        provider.ids = [1, 2]
        await sync(session, instance, provider)
        watermark = await mark(session, instance)
        watermark.collection_metadata = {**watermark.collection_metadata,
            "absence_accept": {"armed_at": NOW.isoformat(), "actor": "operator", "held_count": 21}}
        await session.commit()
    doctor = doctor_cli.Doctor(settings=settings(), sessionmaker=maker, now=lambda: NOW)
    for name, _ in doctor_cli._CHECK_RUNBOOKS:
        if name != "watermarks":
            monkeypatch.setattr(doctor, f"check_{name}", AsyncMock(return_value=
                doctor_cli.CheckResult(name, doctor_cli.Status.PASS, "fixture")))
    monkeypatch.setattr(doctor_cli, "Doctor", lambda: doctor)
    monkeypatch.setattr(doctor_cli, "dispose_engine", AsyncMock())
    assert await doctor_cli._async_main(False) == 1
    output = capsys.readouterr().out
    assert "python -m app.cli.inventory absence accept --instance <name>" in output
    assert "absence accept armed for 1 instance(s), 21 held sources" in output
    assert "source-" not in output
    async with maker() as session:
        instance = await session.get(QRadarInstance, instance.id)
        provider.validated = False
        await sync(session, instance, provider)
        await session.commit()
    # Armed accept must stay visible even after a skipped run replaces the held state.
    assert await doctor_cli._async_main(False) == 1
    assert "absence accept armed for 1 instance(s), 21 held sources" in capsys.readouterr().out


@pytest.fixture
def base_code(tmp_path, monkeypatch):
    git = shutil.which("git")
    assert git is not None
    modules = {}
    for key, path in (("service", "services/inventory_sync.py"),
                      ("collector", "collectors/log_source_collector.py")):
        source = subprocess.run(  # noqa: S603 — fixed read-only object/path
            [git, "show", f"3bbb9783:backend/app/{path}"], cwd=BACKEND,
            check=True, capture_output=True, text=True).stdout
        copy = tmp_path / f"base_{key}.py"
        copy.write_text(source)
        spec = importlib.util.spec_from_file_location(f"_jdbc1x3b_base_{key}", copy)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        spec.loader.exec_module(module)
        modules[key] = module
    modules["collector"].InventorySyncService = modules["service"].InventorySyncService
    return modules


@pytest.mark.parametrize("on", [False, True])
@pytest.mark.parametrize("count", [3, 800])
@pytest.mark.parametrize("unknown", [False, True])
async def test_switch_off_unchanged_and_on_no_edge_exact_exception(
    rig, base_code, on, count, unknown,
):
    engine, maker = rig
    shapes = []
    for cls in (base_code["collector"].LogSourceCollector, LogSourceCollector):
        async with maker() as session:
            instance, provider, _ = await seed(session, count)
            provider.enabled = None if unknown else True
            collector = cls(session, provider, settings=settings(on), clock=lambda: NOW)
            with recorded(engine) as seen:
                await collector.sync(instance)
            shapes.append([sql for sql, _, _ in seen])
            assert await spans(session) == []
            await session.rollback()
    base, actual = shapes
    assert len(base) == (15 if on and not unknown else 14)
    assert len(actual) == (15 if on else 14)
    if on and unknown:
        # The old disabled reconciler skipped unknown flags entirely. State-based ABSENT
        # presence still requires this one bulk read; every other statement stays identical.
        [new] = [sql for sql in actual if "FROM log_source_pause" in sql]
        assert [sql for sql in actual if "FROM log_source_pause" not in sql] == base
    else:
        changed = [(old, new) for old, new in zip(base, actual, strict=True) if old != new]
        assert len(changed) == int(on)
        if on:
            old, new = changed[0]
            assert "FROM log_source_pause" in old
    if on:
        assert new == (
            "SELECT log_source_pause.log_source_id, log_source_pause.reason \n"
            "FROM log_source_pause \nWHERE log_source_pause.log_source_id = ANY ($1::UUID[]) "
            "AND (log_source_pause.reason = $2::VARCHAR OR log_source_pause.reason = $3::VARCHAR "
            "AND log_source_pause.log_source_id = ANY ($4::UUID[])) "
            "AND log_source_pause.ends_at IS NULL"
        )


@pytest.mark.parametrize("count", [3, 800])
@pytest.mark.parametrize("edge", ["absent", "reappear"])
async def test_constant_edge_statements_and_lock_before_updates(rig, count, edge):
    engine, maker = rig
    async with maker() as session:
        instance, provider, sources = await seed(session, count)
        provider.ids.remove(count)
        await sync(session, instance, provider)
        if edge == "reappear":
            await sync(session, instance, provider)
            provider.ids.append(count)
        provider.name = "changed"
        with recorded(engine) as seen:
            await sync(session, instance, provider)
        locks = [(index, sql, params, compiled)
                 for index, (sql, params, compiled) in enumerate(seen)
                 if "FROM log_source " in sql and "FOR NO KEY UPDATE" in sql]
        assert len(locks) == 1
        index, sql, params, compiled = locks[0]
        assert "= ANY ($1::UUID[])" in sql
        assert sql.endswith("ORDER BY log_source.id FOR NO KEY UPDATE")
        assert str(compiled.statement.compile(dialect=asyncpg.dialect())).endswith(
            "ORDER BY log_source.id FOR NO KEY UPDATE")
        locked = set(params[0])
        assert locked == {source.id for source in sources.values()}
        updates = [(i, c) for i, (s, _, c) in enumerate(seen)
                   if s.startswith("UPDATE log_source SET")]
        assert updates and min(i for i, _ in updates) > index
        updated = set()
        # Driver parameters contain the real values; compilation defaults can be empty.
        for i, _ in updates:
            _, values, _ = seen[i]
            rows = values if values and isinstance(values[0], tuple) else [values]
            updated.update(row[-1] for row in rows)
        assert updated <= locked
        print(f"X3B_SQL {edge} {count}: {len(seen)} statements")
        assert len(seen) == (20 if edge == "absent" else 17)


async def test_absence_source_lock_compiles_to_no_key_update():
    sql = str(absence_source_lock_statement({uuid.UUID(int=2), uuid.UUID(int=1)}).compile(
        dialect=asyncpg.dialect()))
    assert sql == ("SELECT log_source.id \nFROM log_source \n"
                   "WHERE log_source.id = ANY ($1::UUID[]) "
                   "ORDER BY log_source.id FOR NO KEY UPDATE")


async def wait_blocked(maker, waiter, blocker, operation):
    """Database round trips yield the loop; no timing sleeps decide the schedule."""
    async with maker() as observer, asyncio.timeout(4):
        while True:
            blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter})
            if blocker in blockers:
                assert not operation.done()
                return
            if operation.done():
                await operation
                pytest.fail("operation finished without the expected PostgreSQL wait")


async def timeout(session):
    await session.execute(text("SET LOCAL lock_timeout = '8s'"))
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def race_seed(maker, missing):
    async with maker() as session:
        instance = await make_instance(session)
        sources = [LogSource(id=uuid.UUID(int=q), instance_id=instance.id, qradar_id=q,
                             name=f"source-{q}", enabled=True, monitoring_enabled=True,
                             protocol_type_id=8, first_seen_at=BEFORE) for q in (2, 1)]
        session.add_all(sources)
        await session.flush()
        for source in sources:
            await _count_baseline(session, source, NOW.replace(minute=0))
            for index in range(3):
                await add_metric(
                    session, source, NOW.replace(minute=0) + timedelta(minutes=5 * index),
                                 average_eps=0.0, event_count=0)
        session.add(CollectionWatermark(
            instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=BEFORE,
            collection_metadata={"absence_candidates": [str(uuid.UUID(int=missing))],
                                 "absence_complete_rows": 2}))
        await session.commit()
        return instance.id


@pytest.mark.parametrize("missing", [1, 2], ids=["absent-lower", "absent-higher"])
@pytest.mark.parametrize("holder", ["engine", "daily"])
async def test_absence_prelock_vs_real_engine_batch_and_daily_rebuild(
    rig, monkeypatch, missing, holder,
):
    engine, maker = rig
    instance_id = await race_seed(maker, missing)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", settings)
    held, release = asyncio.Event(), asyncio.Event()
    original = log_source_policy.lock_sources
    holder_pid = None
    intercepted = False

    async def split_ordered_batch(session, ids):
        nonlocal holder_pid, intercepted
        if len(ids) == 2 and not intercepted:
            intercepted = True
            holder_pid = await timeout(session)
            # Pause the real ascending batch after its lowest parent is locked.
            await original(session, [min(ids)])
            held.set()
            await release.wait()
        return await original(session, ids)

    monkeypatch.setattr(log_source_policy, "lock_sources", split_ordered_batch)
    operation = asyncio.create_task(tasks.evaluate_anomalies() if holder == "engine"
                                    else tasks.rebuild_baselines())
    try:
        await asyncio.wait_for(held.wait(), 4)
        async with maker() as session:
            pid = await timeout(session)
            instance = await session.get(QRadarInstance, instance_id)
            provider = Inventory(2)
            provider.ids = [3 - missing]
            provider.name = "changed"  # dirty parent on the other side of the absent UUID
            with recorded(engine) as seen:
                syncing = asyncio.create_task(sync(session, instance, provider))
                try:
                    await wait_blocked(maker, pid, holder_pid, syncing)
                    assert not any(sql.startswith("UPDATE log_source SET") for sql, _, _ in seen)
                    release.set()
                    result = await asyncio.wait_for(operation, 7)
                    if holder == "engine":
                        assert result.get("failed", 0) == 0
                    report = await asyncio.wait_for(syncing, 7)
                    assert report.absence["confirmed"] == 1
                    await session.commit()
                finally:
                    if not syncing.done():
                        syncing.cancel()
                    await asyncio.gather(syncing, return_exceptions=True)
        async with maker() as observer:
            assert len(await spans(observer)) == 1
            if holder == "engine":
                incidents = list(await observer.scalars(select(LogSourceAnomaly).where(
                    LogSourceAnomaly.log_source_id == uuid.UUID(int=missing),
                    LogSourceAnomaly.anomaly_type == AnomalyType.NO_EVENTS)))
                assert incidents and all(row.state == AnomalyState.RESOLVED for row in incidents)
                transitions = list(await observer.scalars(select(AnomalyStateTransition).where(
                    AnomalyStateTransition.anomaly_id.in_([row.id for row in incidents]),
                    AnomalyStateTransition.actor == "system:inventory-sync")))
                assert transitions and all(row.reason_i18n == MessageRef(CODE).to_wire()
                                           for row in transitions)
    finally:
        release.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def test_two_syncs_wait_and_close_once(rig):
    _, maker = rig
    instance_id = await race_seed(maker, 1)
    async with maker() as first, maker() as second:
        first_pid, second_pid = await timeout(first), await timeout(second)
        instance1 = await first.get(QRadarInstance, instance_id)
        instance2 = await second.get(QRadarInstance, instance_id)
        provider = Inventory(2)
        provider.ids = [2]
        assert (await sync(first, instance1, provider)).absence["confirmed"] == 1
        second_provider = Inventory(2)
        second_provider.ids = [2]
        second_provider.walk_log_sources = AsyncMock(wraps=second_provider.walk_log_sources)
        operation = asyncio.create_task(sync(second, instance2, second_provider))
        try:
            await wait_blocked(maker, second_pid, first_pid, operation)
            second_provider.walk_log_sources.assert_not_awaited()
            await first.commit()
            report = await asyncio.wait_for(operation, 7)
            assert report.absence["confirmed"] == report.absence["candidates"] == 0
            assert len(await spans(second)) == 1
            await second.commit()
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("stage", ["candidate", "confirmation"])
async def test_accept_starts_fresh_two_passes_at_either_held_stage(rig, db_schema, stage):
    _, maker = rig
    async with maker() as session:
        instance, provider, sources = await seed(session, 23)
        name, iid = instance.name, instance.id
        provider.ids = [1, 2]
        if stage == "confirmation":
            watermark = await mark(session, instance)
            watermark.collection_metadata = {**watermark.collection_metadata,
                "absence_candidates": [str(s.id) for q, s in sources.items() if q > 2]}
        assert (await sync(session, instance, provider)).absence["state"] == "held"
        await session.commit()
    accepted = cli(db_schema, "accept", "--instance", name)
    assert accepted.returncode == 0
    async with maker() as session:
        instance = await session.get(QRadarInstance, iid)
        watermark = await mark(session, instance)
        assert watermark.collection_metadata["absence_candidates"] == []
        first = await sync(session, instance, provider)
        assert first.absence["candidates"] == 21 and first.absence["confirmed"] == 0
        assert "absence_accept" in watermark.collection_metadata and await spans(session) == []
        second = await sync(session, instance, provider)
        assert second.absence["confirmed"] == 21 and len(await spans(session)) == 21
        assert "absence_accept" not in watermark.collection_metadata


async def test_switch_off_unchanged_with_retained_absent(rig, base_code):
    engine, maker = rig
    async with maker() as seed_session:
        instance, _, sources = await seed(seed_session)
        iid = instance.id
        sid = sources[3].id
        await open_pause(seed_session, [sid], reason="ABSENT", starts_at=BEFORE, now=NOW)
        await seed_session.commit()
    shapes = []
    for cls in (base_code["collector"].LogSourceCollector, LogSourceCollector):
        async with maker() as session:
            instance = await session.get(QRadarInstance, iid)
            provider = Inventory()
            with recorded(engine) as seen:
                await cls(session, provider, settings=settings(False), clock=lambda: NOW).sync(
                    instance)
            shapes.append([sql for sql, _, _ in seen])
            [pause] = await spans(session)
            assert pause.ends_at is None
            assert not any("INSERT INTO log_source_pause" in sql or
                           "UPDATE log_source_pause" in sql for sql, _, _ in seen)
            await session.rollback()
    assert shapes[0] == shapes[1]


@pytest.mark.parametrize("missing", [3, 800])
async def test_mass_edge_uses_only_existing_closure_chunks(rig, missing):
    engine, maker = rig
    async with maker() as session:
        instance, provider, sources = await seed(session, missing + 1)
        watermark = await mark(session, instance)
        watermark.collection_metadata = {**watermark.collection_metadata,
            "absence_candidates": [str(s.id) for q, s in sources.items() if q > 1],
            "absence_accept": {"held_count": missing, "actor": "fixture",
                               "armed_at": NOW.isoformat()}}
        await session.flush()
        provider.ids = [1]
        provider.name = "changed"
        with recorded(engine) as seen:
            report = await sync(session, instance, provider)
        assert report.absence["confirmed"] == missing and len(await spans(session)) == missing
        assert sum("INSERT INTO log_source_pause" in sql for sql, _, _ in seen) == 1
        assert sum("FROM log_source_anomaly" in sql for sql, _, _ in seen) == (missing + 499) // 500
        assert len(seen) == 18 + 3 * ((missing + 499) // 500)
        print(f"X3B_MASS_SQL {missing}: {len(seen)} statements")


@pytest.mark.parametrize("db_enabled", [True, False])
async def test_accept_cli_resolves_database_override(rig, db_schema, db_enabled):
    _, maker = rig
    async with maker() as session:
        instance, provider, _ = await seed(session, 23)
        name = instance.name
        provider.ids = [1, 2]
        await sync(session, instance, provider)
        session.add(FeatureSwitch(key="qradar_source_pause_enabled", enabled=db_enabled,
                                  updated_at=NOW, updated_by="fixture"))
        await session.commit()
    result = cli(db_schema, "accept", "--instance", name, FEATURE_SWITCHES_DB="true",
                 QRADAR_SOURCE_PAUSE_ENABLED=str(not db_enabled).lower())
    assert result.returncode == (0 if db_enabled else 2)
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == int(db_enabled)


@pytest.mark.parametrize("holder", ["engine", "daily"])
async def test_inventory_first_engine_and_daily_wait_then_observe_absent(rig, monkeypatch, holder):
    _, maker = rig
    iid = await race_seed(maker, 1)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", settings)
    original = log_source_policy.lock_sources
    entered = asyncio.Event()
    waiter_pid = None

    async def observe_lock(session, ids):
        nonlocal waiter_pid
        waiter_pid = await timeout(session)
        entered.set()
        return await original(session, ids)

    monkeypatch.setattr(log_source_policy, "lock_sources", observe_lock)
    async with maker() as session:
        blocker = await timeout(session)
        instance = await session.get(QRadarInstance, iid)
        provider = Inventory(2)
        provider.ids = [2]
        await sync(session, instance, provider)
        operation = asyncio.create_task(tasks.evaluate_anomalies() if holder == "engine"
                                        else tasks.rebuild_baselines())
        try:
            await asyncio.wait_for(entered.wait(), 4)
            await wait_blocked(maker, waiter_pid, blocker, operation)
            await session.commit()
            result = await asyncio.wait_for(operation, 7)
            assert result.get("failed", 0) == 0
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
    async with maker() as observer:
        assert len(await spans(observer)) == 1
        assert await observer.scalar(select(func.count()).select_from(LogSourceAnomaly).where(
            LogSourceAnomaly.log_source_id == uuid.UUID(int=1))) == 0
