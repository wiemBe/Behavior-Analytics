"""P9b1: daily training owns ordered source-lock batches, before observation reads."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import log_source_policy
from app.anomaly import baseline
from app.anomaly.baseline import BaselineBuilder
from app.core.config import Settings
from app.log_source_policy import rebuild_baselines_locked
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource, LogSourceBaseline, LogSourceMetric
from app.services.feature_switches import UNREADABLE_REASON
from app.services.source_pause_spans import close_pause, open_pause
from app.workers import tasks
from tests.integration.factories import make_instance
from tests.integration.test_source_pause_races import _locks, _record, _timeout, _wait_blocked

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
BASE = NOW - timedelta(weeks=7)
STEP = timedelta(minutes=5)
REASON = "QRADAR_DISABLED"
REBUILD_STATEMENTS = 3


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _settings(**kwargs):
    return Settings(
        _env_file=None, baseline_min_samples=3, baseline_lookback_days=120, **kwargs,
    )


@pytest.fixture
async def rig(db_schema, monkeypatch):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", _settings)
    monkeypatch.setattr(tasks, "datetime", FrozenDatetime)
    # The switch-off path builds `BaselineBuilder(session)` with no settings, so the
    # builder reads get_settings() from its own module, as it did before P9b.
    monkeypatch.setattr(baseline, "get_settings", _settings)
    monkeypatch.setattr(baseline, "datetime", FrozenDatetime)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


async def _seed(maker, count=1, *, hours=1):
    async with maker() as session:
        instance = await make_instance(session)
        sources = [
            LogSource(
                instance_id=instance.id, qradar_id=index, id=uuid.UUID(int=index),
                name=f"source-{index}", enabled=True, monitoring_enabled=True,
            )
            for index in range(count, 0, -1)
        ]
        session.add_all(sources)
        await session.flush()
        session.add_all([
            LogSourceMetric(
                log_source_id=source.id,
                bucket_start=BASE + timedelta(weeks=week, hours=hour),
                average_eps=50.0, event_count=15000, bucket_seconds=300,
            )
            for source in sources for week in range(6) for hour in range(hours)
        ])
        await session.commit()
    return sorted(source.id for source in sources)


async def _switch(maker, enabled):
    async with maker() as session:
        session.add(FeatureSwitch(
            key="qradar_source_pause_enabled", enabled=enabled,
            updated_at=NOW, updated_by="operator",
        ))
        await session.commit()


async def _cells(maker):
    async with maker() as session:
        return (await session.scalars(select(LogSourceBaseline))).all()


def _builder(session, policy=True):
    return BaselineBuilder(session, settings=_settings(), source_pause_policy=policy)


@pytest.mark.parametrize("policy", [True, False])
async def test_actual_daily_rebuild_uses_policy(rig, monkeypatch, policy):
    engine, maker = rig
    ids = await _seed(maker, 3)
    async with maker() as session:
        disabled = await session.get(LogSource, ids[1])
        disabled.enabled = False
        unmonitored = await session.get(LogSource, ids[2])
        unmonitored.monitoring_enabled = False
        await open_pause(
            session, [ids[0]], reason=REASON, starts_at=BASE + timedelta(weeks=5), now=NOW,
        )
        await session.commit()
    # Database rows override an opposite environment value in both directions.
    monkeypatch.setattr(
        tasks, "get_settings", lambda: _settings(qradar_source_pause_enabled=not policy),
    )
    await _switch(maker, policy)
    original = tasks.BaselineBuilder
    seen = []

    def builder(session, **kwargs):
        seen.append(kwargs)
        return original(session, **kwargs)

    monkeypatch.setattr(tasks, "BaselineBuilder", builder)
    with _record(engine) as operations:
        result = await tasks.rebuild_baselines()
    assert result == {"sources": 1 if policy else 2, "cells": 2 if policy else 4}
    assert len(seen) == 1
    if policy:
        assert seen[0]["settings"].qradar_source_pause_enabled is policy
        assert seen[0]["settings"].baseline_lookback_days == 120
        assert seen[0]["source_pause_policy"] is True
    else:
        # Switch off is today's code: no settings= or source_pause_policy= kwarg.
        assert seen[0] == {}
    assert sum("FROM feature_switch" in sql for sql, _ in operations) == 1
    assert len(_locks(operations)) == int(policy)
    assert sum(sql == "COMMIT" for sql, _ in operations) == 1
    cells = await _cells(maker)
    assert {cell.log_source_id for cell in cells} == set(ids[:1 if policy else 2])
    first = next(c for c in cells if c.log_source_id == ids[0])
    assert first.sample_count == (5 if policy else 6)
    assert first.exclusion_counts.get("qradar_paused", 0) == int(policy)


@pytest.mark.parametrize("hours", [1, 10])
@pytest.mark.parametrize("policy", [True, False])
async def test_one_lock_select_per_batch_plus_three_per_source(rig, policy, hours):
    engine, maker = rig
    ids = await _seed(maker, 150, hours=hours)
    await _switch(maker, policy)
    with _record(engine) as operations:
        result = await tasks.rebuild_baselines()
    assert result == {"sources": 150, "cells": 150 * hours * 2}
    locks = _locks(operations)
    assert len(locks) == (2 if policy else 0)
    if policy:
        assert [len(params) for _, params in locks] == [100, 50]
        assert all("ORDER BY log_source.id" in sql for sql, _ in locks)
        assert [value for _, params in locks for value in params] == ids
    # Feature snapshot + candidate selection + three per source + bulk span reads + locks.
    batches = 2 if policy else 1
    sql = [s for s, _ in operations if s != "COMMIT"]
    assert len(sql) == 2 + REBUILD_STATEMENTS * 150 + batches + len(locks)
    assert sum("FROM baseline_training_exclusion" in s for s in sql) == batches
    assert sum(s == "COMMIT" for s, _ in operations) == batches
    if policy:
        # Each batch's reads and writes end before the next lock starts.
        boundaries = ["lock" if "FOR UPDATE" in s else "commit"
                      for s, _ in operations if "FOR UPDATE" in s or s == "COMMIT"]
        assert boundaries == ["lock", "commit", "lock", "commit"]


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("policy", [True, False])
async def test_direct_rebuild_wrapper_locked(rig, enabled, policy):
    engine, maker = rig
    [source_id] = await _seed(maker)
    async with maker() as session:
        source = await session.get(LogSource, source_id)
        source.enabled = enabled
        await session.commit()
        with _record(engine) as operations:
            cells = await rebuild_baselines_locked(
                session, _builder(session, policy), [source_id, source_id],
                now=NOW, source_pause_policy=policy,
            )
    rebuilt = enabled or not policy
    assert cells == (2 if rebuilt else 0)
    assert len(_locks(operations)) == 1
    assert "FOR UPDATE" in operations[0][0]
    assert sum(s == "COMMIT" for s, _ in operations) == 1
    assert len(await _cells(maker)) == cells


async def test_two_sessions_rebuild_waits_then_skips_disabled(rig):
    engine, maker = rig
    [source_id] = await _seed(maker)
    async with maker() as inventory, maker() as rebuild:
        await _timeout(inventory)
        pid = await _timeout(rebuild)
        # Keep a stale enabled object in the rebuild identity map before disable.
        stale = await rebuild.get(LogSource, source_id)
        source = await inventory.scalar(
            select(LogSource).where(LogSource.id == source_id).with_for_update()
        )
        source.enabled = False
        await open_pause(inventory, [source_id], reason=REASON, starts_at=BASE, now=NOW)
        await inventory.flush()
        with _record(engine) as operations:
            operation = asyncio.create_task(rebuild_baselines_locked(
                rebuild, _builder(rebuild), [source_id], now=NOW, source_pause_policy=True,
            ))
            try:
                await _wait_blocked(maker, pid, operation)
                # No observation or baseline write is allowed before the source lock.
                assert not any("FROM log_source_metric" in sql for sql, _ in operations)
                await inventory.commit()
                assert await asyncio.wait_for(operation, 6) == 0
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        assert stale.enabled is False
    assert await _cells(maker) == []


async def test_reenable_rebuild_excludes_racing_tail(rig):
    _, maker = rig
    [source_id] = await _seed(maker)
    tail = BASE + timedelta(weeks=6)
    async with maker() as session:
        source = await session.get(LogSource, source_id)
        source.enabled = False
        await open_pause(session, [source_id], reason=REASON, starts_at=tail, now=NOW)
        session.add_all([
            LogSourceMetric(
                log_source_id=source_id, bucket_start=tail + index * STEP,
                bucket_seconds=300, average_eps=eps, event_count=int(eps * 300),
            )
            for index, eps in enumerate([0.0, 900.0])
        ])
        await session.commit()
    async with maker() as rebuild, maker() as inventory:
        stale = await rebuild.get(LogSource, source_id)
        assert stale.enabled is False
        source = await inventory.get(LogSource, source_id)
        source.enabled = True
        # Rounds up to 12:10, conservatively excluding the positive racing tail.
        await close_pause(
            inventory, [source_id], reason=REASON,
            observed_at=tail + STEP + timedelta(seconds=1), bucket_seconds=300,
        )
        await inventory.commit()
        assert await rebuild_baselines_locked(
            rebuild, _builder(rebuild), [source_id], now=NOW, source_pause_policy=True,
        ) == 2
        assert stale.enabled is True
    for cell in await _cells(maker):
        assert cell.sample_count == 6
        assert cell.is_reliable is True
        assert cell.exclusion_counts["qradar_paused"] == 2
        assert cell.excluded_sample_count == 2
        assert cell.observations == [50.0 if cell.metric_name == "average_eps" else 15000.0] * 6


async def test_deleted_id_is_skipped(rig):
    engine, maker = rig
    ids = await _seed(maker, 2)
    async with maker() as session:
        await session.execute(delete(LogSource).where(LogSource.id == ids[0]))
        await session.commit()
        with _record(engine) as operations:
            assert await rebuild_baselines_locked(
                session, _builder(session), ids, now=NOW, source_pause_policy=True,
            ) == 2
    assert len(_locks(operations)) == 1
    assert {cell.log_source_id for cell in await _cells(maker)} == {ids[1]}


async def test_unreadable_switch_table_retains_legacy_behavior(rig, monkeypatch, caplog):
    engine, maker = rig
    [source_id] = await _seed(maker)
    async with maker() as session:
        source = await session.get(LogSource, source_id)
        source.enabled = False
        await session.execute(text("DROP TABLE feature_switch"))
        await session.commit()
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(qradar_source_pause_enabled=True))
    with _record(engine) as operations, caplog.at_level("WARNING", logger="app.tasks"):
        assert await tasks.rebuild_baselines() == {"sources": 1, "cells": 2}
    assert not _locks(operations)
    assert sum(s == "COMMIT" for s, _ in operations) == 1
    assert len(await _cells(maker)) == 2
    assert [r.reason for r in caplog.records if r.name == "app.tasks"] == [UNREADABLE_REASON]


async def test_empty_wrapper_does_no_work(rig):
    engine, maker = rig
    async with maker() as session:
        builder = _builder(session)
        with _record(engine) as operations:
            assert await rebuild_baselines_locked(
                session, builder, [], now=NOW, source_pause_policy=True,
            ) == 0
    assert operations == []


@pytest.mark.parametrize("policy", [True, False])
async def test_empty_daily_keeps_commit_semantics(rig, policy):
    engine, maker = rig
    await _switch(maker, policy)
    with _record(engine) as operations:
        assert await tasks.rebuild_baselines() == {"sources": 0, "cells": 0}
    assert not _locks(operations)
    assert sum(s == "COMMIT" for s, _ in operations) == int(not policy)


@pytest.mark.parametrize("failure", ["lock", "builder", "commit"])
async def test_failed_batch_preserves_only_prior_commits(rig, monkeypatch, failure):
    _, maker = rig
    ids = await _seed(maker, 101)
    async with maker() as session:
        builder = _builder(session)
        owner, attribute = {
            "lock": (log_source_policy, "lock_sources"),
            "builder": (builder, "rebuild_many"),
            "commit": (session, "commit"),
        }[failure]
        original = getattr(owner, attribute)
        calls = 0

        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second batch failed")
            return await original(*args, **kwargs)

        monkeypatch.setattr(owner, attribute, fail_second)
        with pytest.raises(RuntimeError, match="second batch failed"):
            await rebuild_baselines_locked(
                session, builder, ids, now=NOW, source_pause_policy=True,
            )
        await session.rollback()
    assert {c.log_source_id for c in await _cells(maker)} == set(ids[:100])


async def test_off_path_builds_legacy_builder_and_issues_todays_statements(rig, monkeypatch):
    """Switch off is today's code: BaselineBuilder(session), one rebuild_many, one commit."""
    engine, maker = rig
    await _seed(maker, 2)
    await _switch(maker, False)
    # Capture today's daily body on the same data as the statement reference.
    async with maker() as session:
        with _record(engine) as legacy:
            sources = list((await session.scalars(select(LogSource).where(
                LogSource.monitoring_enabled.is_(True),
            ))).all())
            await BaselineBuilder(session).rebuild_many(sources)
        await session.rollback()

    original = tasks.BaselineBuilder
    seen_kwargs = []

    def builder(session, **kwargs):
        seen_kwargs.append(kwargs)
        return original(session, **kwargs)

    monkeypatch.setattr(tasks, "BaselineBuilder", builder)
    wrapper = AsyncMock(side_effect=AssertionError("off path called the locked wrapper"))
    monkeypatch.setattr(log_source_policy, "rebuild_baselines_locked", wrapper)
    with _record(engine) as actual:
        result = await tasks.rebuild_baselines()

    assert result == {"sources": 2, "cells": 4}
    assert seen_kwargs == [{}]  # today's BaselineBuilder(session): no settings= kwarg.
    assert not wrapper.called
    assert not _locks(actual)
    # The effective-switch snapshot is the sole extra read; the old body is unchanged.
    body = [s for s, _ in actual if "FROM feature_switch" not in s and s != "COMMIT"]
    assert body == [s for s, _ in legacy]
    assert sum(s == "COMMIT" for s, _ in actual) == 1


async def test_off_path_matches_legacy_statements_and_documents(rig, monkeypatch):
    engine, maker = rig
    ids = await _seed(maker, 2)
    async with maker() as session:
        source = await session.get(LogSource, ids[1])
        source.enabled = False
        await session.commit()
        # Run the former daily body on the same data, then roll its writes back.
        with _record(engine) as legacy:
            sources = list((await session.scalars(select(LogSource).where(
                LogSource.monitoring_enabled.is_(True),
            ))).all())
            expected = await _builder(session, False).rebuild_many(sources)
        documents = (await session.scalars(select(LogSourceBaseline))).all()
        wanted = sorted((c.log_source_id, c.metric_name, c.observations, c.exclusion_counts)
                        for c in documents)
        await session.rollback()
    wrapper = AsyncMock(side_effect=AssertionError("off path called the locked wrapper"))
    monkeypatch.setattr(log_source_policy, "rebuild_baselines_locked", wrapper)
    with _record(engine) as actual:
        assert await tasks.rebuild_baselines() == {"sources": 2, "cells": expected}
    assert not wrapper.called
    # The effective-switch snapshot is the sole extra read; the old body is unchanged.
    body = [s for s, _ in actual if "FROM feature_switch" not in s and s != "COMMIT"]
    assert body == [s for s, _ in legacy]
    assert sum(s == "COMMIT" for s, _ in actual) == 1
    assert sorted((c.log_source_id, c.metric_name, c.observations, c.exclusion_counts)
                  for c in await _cells(maker)) == wanted


async def test_daily_off_failure_propagates_without_commit(rig, monkeypatch):
    engine, maker = rig
    await _seed(maker)
    monkeypatch.setattr(
        BaselineBuilder, "rebuild_many", AsyncMock(side_effect=RuntimeError("failed")),
    )
    with _record(engine) as operations, pytest.raises(RuntimeError, match="failed"):
        await tasks.rebuild_baselines()
    assert not any(s == "COMMIT" for s, _ in operations)
    assert not _locks(operations)
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(LogSourceBaseline)) == 0
