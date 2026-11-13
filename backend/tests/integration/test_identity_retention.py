"""Identity retention deletes aged buckets and the rows that share their start."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.identity.retention import IdentityRetention
from app.models.enums import IDENTITY_CATALOG_VERSION, IdentityMeasurement
from app.models.identity_detection import (
    IdentityBucket,
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
EXACT = IdentityMeasurement.EXACT.value


async def _stamp(session, source, start: datetime, username: str | None = "auditor") -> None:
    session.add(
        IdentityBucket(
            log_source_id=source.id,
            bucket_start=start,
            bucket_seconds=300,
            catalog_version=IDENTITY_CATALOG_VERSION,
            measurement=EXACT,
            population=1,
            admission_truncated=False,
            query_provenance={},
            collected_at=NOW,
        )
    )
    session.add(
        IdentityEventCount(
            log_source_id=source.id,
            event_id=1102,
            bucket_start=start,
            event_count=1,
            measurement=EXACT,
            names_measurement=EXACT,
            admission_truncated=False,
        )
    )
    if username is not None:
        session.add(
            IdentityTopUsername(
                log_source_id=source.id,
                event_id=1102,
                bucket_start=start,
                rank=1,
                username=username,
                truncated=False,
                event_count=1,
            )
        )
    await session.flush()


async def _starts(session, model) -> set[datetime]:
    rows = (await session.scalars(select(model.bucket_start))).all()
    return set(rows)


async def test_old_rows_go_and_the_cutoff_row_stays(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    profile = IdentitySourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=False,
        profile_version=1,
        event_id_property="EventID",
        username_property="username",
        enabled_event_ids=[1102],
        validation_status="UNVALIDATED",
        validation_report={},
    )
    db_session.add(profile)
    old = NOW - timedelta(days=36)
    cutoff = NOW - timedelta(days=35)
    fresh = NOW - timedelta(days=1)
    await _stamp(db_session, source, old, "old-name")
    await _stamp(db_session, source, cutoff, "kept-name")
    await _stamp(db_session, source, fresh, "fresh-name")
    report = await IdentityRetention(db_session, settings=Settings(), clock=lambda: NOW).run()

    assert report.skipped_reason is None
    assert report.incomplete == []
    assert report.deleted["identity_bucket"] == 1
    assert report.deleted["identity_event_count"] == 1
    assert report.deleted["identity_top_username"] == 1
    for model in (IdentityBucket, IdentityEventCount, IdentityTopUsername):
        assert old not in await _starts(db_session, model)
        assert cutoff in await _starts(db_session, model)
        assert fresh in await _starts(db_session, model)
    assert await db_session.scalar(select(func.count()).select_from(IdentitySourceProfile)) == 1


async def test_a_disabled_switch_deletes_nothing(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    old = NOW - timedelta(days=36)
    await _stamp(db_session, source, old)
    settings = Settings(identity_retention_enabled=False)
    report = await IdentityRetention(db_session, settings=settings, clock=lambda: NOW).run()
    assert report.skipped_reason == "identity retention is disabled"
    assert old in await _starts(db_session, IdentityBucket)
    assert old in await _starts(db_session, IdentityEventCount)
    assert old in await _starts(db_session, IdentityTopUsername)
