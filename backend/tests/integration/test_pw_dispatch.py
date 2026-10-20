"""The property-watch dispatch task: the slot gate, the family switch and the beat entry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.property_watch import PropertyWatch, PwBucket
from app.models.search_cadence import SearchCadenceSetting
from app.services.locks import CollectorAdvisoryLock
from app.workers import tasks
from tests.integration.factories import make_instance
from tests.integration.test_pw_collection import add_watch, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SLOT = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
LOCK_NAME = "property_watch"


async def test_dispatch_claims_the_slot_once_collects_and_records_the_run(
    db_schema, monkeypatch
) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # One fixed slot, so a second dispatch in the same slot cannot race the hour boundary.
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: SLOT)
    monkeypatch.setattr(tasks, "get_settings", lambda: cfg())
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            watch = await add_watch(session, instance)
            await session.commit()
        first = await tasks.dispatch_property_watch_collection()
        [result] = first["results"]
        assert result["status"] == "ok"
        assert result["buckets_written"] == 1 and result["watches_due"] == 1
        assert result["searches_sent"] == 2
        assert result["pipeline"] == {"steps": ["collect"]}
        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(PwBucket).where(PwBucket.watch_id == watch.id)
            ) == 1
            stored = await session.get(PropertyWatch, watch.id)
            assert stored is not None and stored.collected_through is not None
            cadence = await session.get(SearchCadenceSetting, "PROPERTY_WATCH")
            assert cadence is not None and cadence.last_slot_start == SLOT
            assert cadence.last_run_outcome == "ok"
            assert cadence.last_run_searches == 2 and cadence.last_run_buckets == 1
        # the same slot again: not due, nothing is searched, nothing is written
        second = await tasks.dispatch_property_watch_collection()
        assert second == {"status": "not_due", "family": "PROPERTY_WATCH"}
    finally:
        await engine.dispose()


async def test_dispatch_is_a_no_op_while_collection_is_off(db_schema, monkeypatch) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_settings", lambda: cfg(pw_collection_enabled=False))
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            watch = await add_watch(session, instance)
            await session.commit()
        assert await tasks.dispatch_property_watch_collection() == {
            "status": "disabled",
            "instances": 0,
            "results": [],
        }
        async with maker() as session:
            assert await session.scalar(select(func.count()).select_from(PwBucket)) == 0
            cadence = await session.get(SearchCadenceSetting, "PROPERTY_WATCH")
            assert cadence is None or cadence.last_slot_start is None
            stored = await session.get(PropertyWatch, watch.id)
            assert stored is not None and stored.collected_through is None
    finally:
        await engine.dispose()


# ----------------------------------------------------------------- the instance lock (T-136)
async def test_a_run_that_loses_the_instance_lock_sends_no_search(
    db_schema, monkeypatch
) -> None:
    """The whole pipeline holds the lock; the run that loses writes nothing and skips."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    slots = iter([SLOT, SLOT + timedelta(hours=1)])
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: next(slots))
    monkeypatch.setattr(tasks, "get_settings", lambda: cfg())
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    built: list[object] = []
    real_build = tasks.build_provider_for_instance

    def capture(instance, audit_session=None):  # type: ignore[no-untyped-def]
        provider = real_build(instance, audit_session=audit_session)
        built.append(provider)
        return provider

    monkeypatch.setattr(tasks, "build_provider_for_instance", capture)
    blocker_engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            watch = await add_watch(session, instance)
            await session.commit()
        blocker = async_sessionmaker(blocker_engine, expire_on_commit=False)
        # the holder keeps its transaction open: a session-level advisory lock sits on the
        # connection, and ending the transaction would hand it back to the pool
        async with blocker() as holding, CollectorAdvisoryLock(
            holding, cfg(), instance.id, LOCK_NAME
        ) as held:
            assert held is True
            skipped = await tasks.dispatch_property_watch_collection()
        [result] = skipped["results"]
        assert result["status"] == "skipped_locked"
        assert result["searches_sent"] == 0 and result["buckets_written"] == 0
        assert result["watches_due"] == 0
        [provider] = built
        assert provider.pw_searches == [], "a run that loses the lock sends no search"
        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(PwBucket).where(PwBucket.watch_id == watch.id)
            ) == 0
            stored = await session.get(PropertyWatch, watch.id)
            assert stored is not None and stored.collected_through is None
            # a skipped run is not a failure, and it is not a silent ok either
            cadence = await session.get(SearchCadenceSetting, "PROPERTY_WATCH")
            assert cadence is not None and cadence.last_run_outcome == "partial"
            assert cadence.last_run_searches == 0 and cadence.last_run_buckets == 0
        # released: the next tick runs, and its outcome is recorded
        [ran] = (await tasks.dispatch_property_watch_collection())["results"]
        assert ran["status"] == "ok"
        assert ran["searches_sent"] == 2 and ran["buckets_written"] == 1
        async with maker() as session:
            cadence = await session.get(SearchCadenceSetting, "PROPERTY_WATCH")
            assert cadence is not None and cadence.last_run_outcome == "ok"
            assert cadence.last_run_searches == 2 and cadence.last_run_buckets == 1
    finally:
        await engine.dispose()
        await blocker_engine.dispose()


async def test_the_pipeline_reports_a_skip_without_touching_the_watch(
    db_schema, monkeypatch
) -> None:
    """The skipped run's payload carries the counts of a run that did nothing."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: SLOT)
    monkeypatch.setattr(tasks, "get_settings", lambda: cfg())
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    blocker_engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            await add_watch(session, instance)
            await session.commit()
        blocker = async_sessionmaker(blocker_engine, expire_on_commit=False)
        async with blocker() as holding, CollectorAdvisoryLock(
            holding, cfg(), instance.id, LOCK_NAME
        ):
            skipped = await tasks.dispatch_property_watch_collection()
        [result] = skipped["results"]
        # collection reported the skip; no step ran, whatever the legacy payload says
        assert result["status"] == "skipped_locked"
        assert result["values_written"] == 0 and result["watermarks_advanced"] == 0
        assert result["steps_run"] == []
        assert result["evaluation"] is None and result["training"] is None
    finally:
        await engine.dispose()
        await blocker_engine.dispose()
