"""The property-watch dispatch task: the slot gate, the family switch and the beat entry."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.property_watch import PropertyWatch, PwBucket
from app.models.search_cadence import SearchCadenceSetting
from app.workers import tasks
from tests.integration.factories import make_instance
from tests.integration.test_pw_collection import add_watch, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SLOT = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


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
