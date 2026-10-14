"""IPS retention is opt-out, bounded and removes aged collection facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.ips_collector import IpsCollector
from app.models.enums import IpsDimension
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket
from app.providers.mock import MockQRadarProvider
from app.services.ips_retention import IpsRetention
from tests.integration.factories import make_instance
from tests.integration.test_ips_collection import add_source, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)


async def count(session, model) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_retention_deletes_old_metrics_buckets_then_unreferenced_retired_entities(
    db_session,
) -> None:
    old = NOW - timedelta(days=40)
    instance = await make_instance(db_session)
    _, source = await add_source(db_session, instance, 412)
    settings = cfg(ips_metric_retention_days=35, ips_retention_batch_size=100)
    await IpsCollector(
        db_session, MockQRadarProvider(now=old), settings=settings, clock=lambda: old
    ).collect(instance)
    entities = (await db_session.scalars(select(IpsEntity))).all()
    if not entities:
        bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
        entity = IpsEntity(
            instance_id=instance.id,
            log_source_id=source.id,
            dimension=IpsDimension.SIGNATURE,
            value_key="qid:5000001",
            first_seen_at=bucket.bucket_start,
            last_seen_at=bucket.bucket_start,
            active=True,
            normalizer_version=1,
        )
        db_session.add(entity)
        await db_session.flush()
        db_session.add(
            IpsEntityMetric(
                ips_entity_id=entity.id,
                bucket_start=bucket.bucket_start,
                events=10,
                bucket_seconds=300,
            )
        )
        entities = [entity]
    for entity in entities:
        entity.active = False
        entity.retired_at = old
    await db_session.commit()

    assert await count(db_session, IpsSourceBucket) == 1
    assert await count(db_session, IpsEntityMetric) > 0
    report = await IpsRetention(db_session, settings=settings, clock=lambda: NOW).run()
    assert report.deleted["ips_source_bucket"] == 1
    assert report.deleted["ips_entity_metric"] > 0
    assert report.deleted["ips_entity"] == len(entities)
    assert report.incomplete == []


async def test_disabled_retention_does_not_delete_rows(db_session) -> None:
    report = await IpsRetention(
        db_session,
        settings=cfg(ips_retention_enabled=False),
        clock=lambda: NOW,
    ).run()

    assert report.skipped_reason == "IPS retention is disabled"
    assert report.deleted == {}
