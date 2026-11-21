"""D-98: pending history training respects pauses without owning source locks or commits."""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.anomaly.baseline import BaselineBuilder
from app.collectors import metric_collector as collector
from app.collectors.metric_collector import MetricCollector
from app.history import phase_a
from app.history.lifecycle import scope_row, step_row
from app.history.planner import ScopeProgress
from app.history.progress_codec import decode_progress, encode_progress
from app.history.source_floors import SourceFloors
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource, LogSourceBaseline, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.source_pause_spans import close_pause, open_pause
from app.workers import tasks
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    ScriptedMockProvider,
    history_rows,
    make_console,
    say_yes,
    scope_of,
    settings_for,
    tick,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@contextmanager
def _record(session):
    operations = []
    engine = session.bind.sync_engine

    def statement(conn, cursor, sql, params, *args):
        operations.append(re.sub(r"sa_savepoint_\d+", "sa_savepoint_N", sql))

    def commit(conn):
        operations.append("COMMIT")

    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "commit", commit)
    try:
        yield operations
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", commit)


async def _reading(session, *, pause=True, count=4):
    """Resume a small frozen top-up, as the existing history progress tests do."""
    console = await make_console(session, range(1, count + 1))
    await say_yes(session, console.instance)
    settings = settings_for(
        _env_file=None, qradar_source_pause_enabled=pause,
        history_backfill_baseline_sources_per_tick=1, baseline_min_samples=3,
    )
    provider = ScriptedMockProvider(console.sources)
    await tick(session, console.instance, provider, settings, FIRST_TICK)
    scope = await scope_of(session, console.instance)
    assert scope.state == "READING"
    start = ANCHOR - 2 * STEP
    scope.target_start = start
    scope.progress = encode_progress(
        ScopeProgress(
            target_start=start, started_at=scope.started_at, floor=start,
            chunk_buckets=2, density_checked=True,
        ),
        {
            **SourceFloors({str(q): start - timedelta(hours=1) for q in console.sources}).encode(),
            phase_a.TOPUP_SOURCES_KEY: list(map(str, console.sources)),
        },
    )
    await session.flush()
    return console, provider, settings


async def _pending(session, *, pause=True, count=4):
    console, provider, settings = await _reading(session, pause=pause, count=count)
    report = await tick(
        session, console.instance, provider, settings, FIRST_TICK + STEP,
    )
    assert report.history.error is None and report.history.rebuilt == 1
    scope = await scope_of(session, console.instance)
    assert scope.state == "COMPLETE"
    assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == [
        str(q) for q in range(2, count + 1)
    ]
    return console, provider, settings


async def _cells(session, source):
    rows = await session.scalars(
        select(LogSourceBaseline).where(LogSourceBaseline.log_source_id == source.id)
        .order_by(LogSourceBaseline.metric_name, LogSourceBaseline.weekday, LogSourceBaseline.hour)
        .execution_options(populate_existing=True)
    )
    # The daily run replaces the same cells and bumps their versions. Compare every
    # training/result field, omitting only identity, audit timestamps and version.
    omitted = {"id", "created_at", "updated_at", "baseline_version"}
    return [
        {column.name: copy.deepcopy(getattr(row, column.name))
         for column in LogSourceBaseline.__table__.columns if column.name not in omitted}
        for row in rows
    ]


async def _legacy_rebuild_pending(self, instance, current, values, now, report):
    """The pre-D-98 body: reference SQL/kwargs contract, reached through collect()."""
    decoded = collector.decode_scope(current)
    if decoded is None:
        return None
    pending = phase_a.pending_of(decoded.adapter)
    if not pending:
        return None
    batch, rest = phase_a.next_rebuild_batch(
        pending, self.settings.history_backfill_baseline_sources_per_tick
    )
    sources = (
        await self.session.scalars(
            select(LogSource).where(
                LogSource.instance_id == instance.id,
                LogSource.qradar_id.in_([int(key) for key in batch]),
            )
        )
    ).all()
    builder = collector.BaselineBuilder(self.session, settings=self.settings)
    await builder.rebuild_many(sources, now=now)
    report.rebuilt = len(sources)
    adapter = {**decoded.adapter, phase_a.REBUILD_PENDING_KEY: rest}
    base = (
        values if values is not None else scope_row(
            current, instance_id=instance.id, family=collector.HISTORY_FAMILY,
            scope_key=collector.HISTORY_SCOPE_KEY,
        )
    )
    return step_row(base, progress=encode_progress(decoded.progress, adapter))


async def test_switch_off_pending_rebuild_unchanged(db_session, monkeypatch):
    console, provider, settings = await _pending(db_session, pause=False)
    console.sources[2].enabled = False
    start = ANCHOR - 2 * STEP
    await open_pause(
        db_session, [console.sources[3].id], reason="QRADAR_DISABLED",
        starts_at=start, now=FIRST_TICK,
    )
    await db_session.flush()
    settings = settings.model_copy(update={"history_backfill_baseline_sources_per_tick": 3})
    constructors, batches = [], []
    original_many = BaselineBuilder.rebuild_many

    def builder(session, **kwargs):
        constructors.append(kwargs)
        assert session is db_session
        return BaselineBuilder(session, **kwargs)

    async def many(self, sources, **kwargs):
        batches.append(sorted(s.qradar_id for s in sources))
        return await original_many(self, sources, **kwargs)

    monkeypatch.setattr(collector, "BaselineBuilder", builder)
    monkeypatch.setattr(BaselineBuilder, "rebuild_many", many)

    async def run():
        savepoint = await db_session.begin_nested()
        try:
            with _record(db_session) as operations:
                report = await tick(
                    db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP,
                )
            assert report.history.error is None and report.history.rebuilt == 3
            scope = await scope_of(db_session, console.instance)
            assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == []
            cells = [await _cells(db_session, console.sources[q]) for q in (2, 3, 4)]
            assert all(cells)
            return operations, cells
        finally:
            await savepoint.rollback()

    actual, actual_cells = await run()
    monkeypatch.setattr(MetricCollector, "_rebuild_pending", _legacy_rebuild_pending)
    legacy, legacy_cells = await run()
    assert actual == legacy  # every SQL statement, in order, including the source SELECT
    assert len(actual) == len(legacy)
    assert actual_cells == legacy_cells
    assert constructors == [{"settings": settings}, {"settings": settings}]
    assert batches == [[2, 3, 4], [2, 3, 4]]  # one rebuild_many, including disabled
    assert sum("FROM baseline_training_exclusion" in sql for sql in actual) == 1
    assert sum("INSERT INTO log_source_baseline" in sql for sql in actual) == 3
    assert not any("log_source_pause" in sql or sql == "COMMIT" for sql in actual)


@pytest.mark.parametrize("all_disabled", [False, True])
async def test_switch_on_skips_stored_disabled(db_session, all_disabled):
    console, provider, settings = await _pending(db_session)
    for qid in ((2, 3) if all_disabled else (2,)):
        console.sources[qid].enabled = False
    # Monitoring is not a pending-rebuild guard; a previously read, enabled source
    # can still be rebuilt after the analyst stops monitoring it.
    console.sources[3].monitoring_enabled = False
    await db_session.flush()
    settings = settings.model_copy(update={"history_backfill_baseline_sources_per_tick": 2})
    report = await tick(
        db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP,
    )
    assert report.history.error is None
    assert report.history.rebuilt == (0 if all_disabled else 1)
    assert await _cells(db_session, console.sources[2]) == []
    assert bool(await _cells(db_session, console.sources[3])) is not all_disabled
    scope = await scope_of(db_session, console.instance)
    assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == ["4"]
    report = await tick(
        db_session, console.instance, provider, settings, FIRST_TICK + 3 * STEP,
    )
    assert report.history.error is None and report.history.rebuilt == 1
    scope = await scope_of(db_session, console.instance)
    assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == []
    assert await _cells(db_session, console.sources[2]) == []  # never retried


async def test_switch_on_excludes_paused_span(db_session, monkeypatch):
    console, provider, settings = await _pending(db_session, count=2)
    source = console.sources[2]
    paused = ANCHOR - STEP
    await open_pause(
        db_session, [source.id], reason="QRADAR_DISABLED", starts_at=paused, now=FIRST_TICK,
    )
    await close_pause(
        db_session, [source.id], reason="QRADAR_DISABLED",
        observed_at=ANCHOR, bucket_seconds=300,
    )
    at = FIRST_TICK + 2 * STEP
    report = await tick(db_session, console.instance, provider, settings, at)
    assert report.history.error is None and report.history.rebuilt == 1
    history = await _cells(db_session, source)
    assert len(history) == 4
    assert all(cell["exclusion_counts"]["qradar_paused"] == 1 for cell in history)
    event_cell = next(c for c in history if c["metric_name"] == "event_count" and c["hour"] == 9)
    expected = [provider.sample(2, bucket, 300).event_count for bucket in
                (ANCHOR - 2 * STEP, ANCHOR)]
    assert event_cell["observations"] == expected
    assert event_cell["sample_count"] == 2

    # Run the actual P9b1 daily entry on exactly the same committed metric rows.
    db_session.add(FeatureSwitch(
        key="qradar_source_pause_enabled", enabled=True, updated_at=at, updated_by="test",
    ))
    await db_session.commit()
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return at

    monkeypatch.setattr(tasks, "datetime", FrozenDatetime)
    result = await tasks.rebuild_baselines()
    assert result["sources"] == 2 and result["cells"] == 8
    assert await _cells(db_session, source) == history


async def test_history_fault_still_rolls_back_only_history(db_session, monkeypatch):
    console, provider, settings = await _reading(db_session)
    scope = await scope_of(db_session, console.instance)
    before = copy.deepcopy(scope.progress)
    original = HistoryBackfillRepository.write_scopes

    async def broken(self, rows):
        await original(self, rows)  # history rows, baseline, and pending removal all wrote
        assert len(await history_rows(db_session, console.instance)) == 8
        assert await _cells(db_session, console.sources[1])
        scope = await scope_of(db_session, console.instance)
        assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == [
            "2", "3", "4",
        ]
        raise RuntimeError("history scope write failed")

    with monkeypatch.context() as patch:
        patch.setattr(HistoryBackfillRepository, "write_scopes", broken)
        report = await tick(
            db_session, console.instance, provider, settings, FIRST_TICK + STEP,
        )
    assert report.history.error == "RuntimeError" and report.intervals_collected == 1
    await db_session.commit()  # the worker's outer commit keeps only the live phase
    watermark = await db_session.scalar(select(CollectionWatermark).where(
        CollectionWatermark.instance_id == console.instance.id,
    ))
    assert watermark.watermark_at == ANCHOR + STEP
    live = (await db_session.scalars(select(LogSourceMetric).where(
        LogSourceMetric.bucket_start == ANCHOR + STEP,
    ))).all()
    assert len(live) == 4 and all(row.collection_source == "mock" for row in live)
    assert await history_rows(db_session, console.instance) == []
    assert await _cells(db_session, console.sources[1]) == []
    scope = await scope_of(db_session, console.instance)
    assert scope.state == "READING" and scope.progress == before
    assert scope.last_error_class == "RuntimeError" and scope.last_error_at == FIRST_TICK + STEP
    retry = await tick(
        db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP,
    )
    assert retry.history.error is None and retry.history.rebuilt == 1
    assert len(await history_rows(db_session, console.instance)) == 8
    scope = await scope_of(db_session, console.instance)
    assert scope.state == "COMPLETE" and scope.last_error_class is None
    assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == ["2", "3", "4"]


@pytest.mark.parametrize("pause", [False, True])
async def test_no_source_lock_in_collector_txn(db_session, pause):
    console, provider, settings = await _pending(db_session, pause=pause)
    transaction = db_session.get_transaction()
    with _record(db_session) as operations:
        report = await tick(
            db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP,
        )
    assert report.history.error is None and report.history.rebuilt == 1
    assert db_session.get_transaction() is transaction
    assert "COMMIT" not in operations
    assert not any(re.search(r"\bFOR\s+(?:(?:NO\s+KEY|KEY)\s+)?(?:UPDATE|SHARE)\b", sql)
                   for sql in operations), operations
    assert sum(sql.startswith("SAVEPOINT ") for sql in operations) == 1
    assert sum(sql.startswith("RELEASE SAVEPOINT ") for sql in operations) == 1
    assert sum("INSERT INTO log_source_baseline" in sql for sql in operations) == 1
    assert sum("INSERT INTO history_backfill_scope" in sql for sql in operations) == 1
