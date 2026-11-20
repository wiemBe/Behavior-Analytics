"""Task timeout rollback and durable failure recording for sync_log_sources,
refresh_property_catalog, sync_rule_inventory and collect_offenses.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.property_catalog import PropertyCatalogState
from app.providers.mock import MockQRadarProvider
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name, collector, method", [
    ("sync_log_sources", "log_source_inventory", "walk_log_sources"),
    ("sync_rule_inventory", "rule_inventory", "list_rules"),
    ("collect_offenses", "offense_snapshot", "list_offenses"),
    ("refresh_property_catalog", "property_catalog", "read_property_catalog"),
    ("sync_instance_inventories", "log_source_inventory", "walk_log_sources"),
    ("sync_instance_inventories", "rule_inventory", "list_rules"),
])
@pytest.mark.parametrize("signal_outside_coroutine", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_walk_soft_limit_records_failure_without_advancing_watermark(
    db_schema, monkeypatch, name, collector, method, signal_outside_coroutine, existing,
):
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    before = datetime.now(UTC) - timedelta(days=1)

    async def prepare():
        async with maker() as session:
            instance = await make_instance(session)
            source = await make_log_source(
                session, instance, qradar_id=99999999, name="Keep stored data"
            )
            if existing and collector == "property_catalog":
                session.add(PropertyCatalogState(
                    instance_id=instance.id, status="COMPLETE", as_of=before,
                    applicability_complete=True, property_count=12000,
                ))
            elif existing:
                session.add(CollectionWatermark(
                    instance_id=instance.id, collector=collector, watermark_at=before,
                    intervals_collected=7, collection_metadata={"other": "keep"},
                ))
            await session.commit()
            return instance.id, source.id

    instance_id, source_id = asyncio.run(prepare())
    provider = MockQRadarProvider()

    async def interrupted(*args, **kwargs):
        if signal_outside_coroutine:
            # A Celery signal may unwind run_forever while it waits for I/O,
            # rather than being raised inside the awaited provider coroutine.
            selector = asyncio.get_running_loop()._selector
            original = selector.select

            def interrupt(timeout):
                monkeypatch.setattr(selector, "select", original)
                raise SoftTimeLimitExceeded()

            monkeypatch.setattr(selector, "select", interrupt)
            await asyncio.Event().wait()
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(provider, method, interrupted)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: provider)
    monkeypatch.setattr(tasks, "dispose_engine", AsyncMock())
    with pytest.raises(SoftTimeLimitExceeded):
        celery_app.tasks[name].apply(
            args=[str(instance_id)] if name in {
                "refresh_property_catalog", "sync_instance_inventories"
            } else [], throw=True,
        )

    async def verify():
        async with maker() as session:
            assert (await session.get(LogSource, source_id)).name == "Keep stored data"
            if collector == "property_catalog":
                state = await session.get(PropertyCatalogState, instance_id)
                assert state.status == "FAILED"
                assert state.failure["kind"] == "time_limit"
                assert state.last_completed_at is not None
                assert state.as_of == (before if existing else None)
                assert state.property_count == (12000 if existing else None)
            else:
                mark = await session.scalar(select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == instance_id,
                    CollectionWatermark.collector == collector,
                ))
                assert mark.watermark_at == (before if existing else None)
                assert mark.intervals_collected == (7 if existing else 0)
                assert mark.last_error == "time_limit" and mark.consecutive_failures == 1
                key = "offense_pass" if collector == "offense_snapshot" else "inventory_pass"
                assert mark.collection_metadata[key]["status"] == "FAILED"
                assert mark.collection_metadata[key]["failure_kind"] == "time_limit"
                if existing:
                    assert mark.collection_metadata["other"] == "keep"
                if name == "sync_instance_inventories" and collector == "rule_inventory":
                    first = await session.scalar(select(CollectionWatermark).where(
                        CollectionWatermark.instance_id == instance_id,
                        CollectionWatermark.collector == "log_source_inventory",
                    ))
                    assert first.collection_metadata["inventory_pass"]["status"] == "COMPLETE"
        await engine.dispose()

    asyncio.run(verify())
