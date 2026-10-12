"""Persistence, audit and atomic-claim behavior for search cadence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.enums import SearchCadence, SearchFamily
from app.models.identity import AuditLog
from app.models.search_cadence import SearchCadenceSetting
from app.services.search_cadence import (
    InvalidSearchCadenceError,
    SearchCadenceService,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 12, 12, 2, tzinfo=UTC)


def _row(
    family: SearchFamily = SearchFamily.ANOMALY_EXPLANATION,
    cadence: SearchCadence = SearchCadence.ONE_HOUR,
) -> SearchCadenceSetting:
    return SearchCadenceSetting(
        family=family.value,
        cadence_seconds=int(cadence),
        updated_at=T0,
    )


async def _audit_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0)


async def test_claim_is_due_once_per_slot_and_again_in_the_next_slot(db_session) -> None:
    db_session.add(_row())
    await db_session.commit()
    now = [T0]
    service = SearchCadenceService(
        db_session, Settings(search_cadence_settle_seconds=120), lambda: now[0]
    )

    assert await service.claim_slot(SearchFamily.ANOMALY_EXPLANATION) == T0.replace(
        minute=0
    )
    assert await service.claim_slot(SearchFamily.ANOMALY_EXPLANATION) is None
    now[0] += timedelta(hours=1)
    assert await service.claim_slot(SearchFamily.ANOMALY_EXPLANATION) == T0.replace(
        hour=13, minute=0
    )


async def test_claim_restores_a_missing_seed_row_at_the_default(db_session) -> None:
    service = SearchCadenceService(
        db_session, Settings(search_cadence_settle_seconds=120), lambda: T0
    )
    assert await service.claim_slot(SearchFamily.RULE_MATCH) == T0.replace(minute=0)
    await db_session.commit()
    row = await db_session.get(SearchCadenceSetting, SearchFamily.RULE_MATCH.value)
    assert row is not None
    assert row.cadence_seconds == SearchCadence.ONE_HOUR


async def test_get_and_list_treat_missing_seed_rows_as_one_hour(db_session) -> None:
    service = SearchCadenceService(db_session, Settings(), lambda: T0)
    missing = await service.get(SearchFamily.WAF_COLLECTION)
    assert missing.cadence_seconds == SearchCadence.ONE_HOUR
    rows = await service.list()
    assert [row.family for row in rows] == [family.value for family in SearchFamily]
    assert all(row.cadence_seconds == SearchCadence.ONE_HOUR for row in rows)


async def test_exactly_one_concurrent_session_claims_a_slot(db_schema) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(_row())
        await session.commit()

    async def claim() -> datetime | None:
        async with maker() as session:
            claimed = await SearchCadenceService(
                session, Settings(search_cadence_settle_seconds=120), lambda: T0
            ).claim_slot(SearchFamily.ANOMALY_EXPLANATION)
            await session.commit()
            return claimed

    try:
        claimed = await asyncio.gather(claim(), claim())
        assert sum(value is not None for value in claimed) == 1
    finally:
        await engine.dispose()


async def test_cadence_change_moves_the_next_claim_to_the_four_hour_grid(db_session) -> None:
    first = datetime(2026, 10, 12, 5, 2, tzinfo=UTC)
    now = [first]
    db_session.add(_row())
    await db_session.commit()
    service = SearchCadenceService(
        db_session, Settings(search_cadence_settle_seconds=120), lambda: now[0]
    )
    assert await service.claim_slot(SearchFamily.ANOMALY_EXPLANATION) == first.replace(
        minute=0
    )
    await service.set_cadence(
        SearchFamily.ANOMALY_EXPLANATION,
        SearchCadence.FOUR_HOURS,
        actor="operator",
        source_ip="127.0.0.1",
    )
    assert await service.claim_slot(SearchFamily.ANOMALY_EXPLANATION) is None
    now[0] = datetime(2026, 10, 12, 8, 2, tzinfo=UTC)
    assert await service.claim_slot(SearchFamily.ANOMALY_EXPLANATION) == now[0].replace(
        minute=0
    )


async def test_set_cadence_rejects_invalid_and_does_not_audit_a_noop(db_session) -> None:
    row = _row()
    db_session.add(row)
    await db_session.commit()
    service = SearchCadenceService(db_session, Settings(), lambda: T0)

    with pytest.raises(InvalidSearchCadenceError, match="unsupported"):
        await service.set_cadence(
            SearchFamily.ANOMALY_EXPLANATION,
            600,
            actor="operator",
            source_ip=None,
        )
    unchanged = await service.set_cadence(
        SearchFamily.ANOMALY_EXPLANATION,
        SearchCadence.ONE_HOUR,
        actor="operator",
        source_ip=None,
    )
    assert unchanged is row
    assert await _audit_count(db_session) == 0


async def test_set_cadence_writes_one_audit_row_with_old_and_new(db_session) -> None:
    db_session.add(_row())
    await db_session.commit()
    service = SearchCadenceService(db_session, Settings(), lambda: T0 + timedelta(minutes=1))
    changed = await service.set_cadence(
        SearchFamily.ANOMALY_EXPLANATION,
        SearchCadence.FOUR_HOURS,
        actor="operator",
        source_ip="192.0.2.1",
    )
    audit = await db_session.scalar(select(AuditLog))
    assert changed.cadence_seconds == SearchCadence.FOUR_HOURS
    assert changed.updated_by == "operator"
    assert audit is not None
    assert audit.action == "search_cadence.update"
    assert audit.object_id == SearchFamily.ANOMALY_EXPLANATION.value
    assert audit.detail == {"old": 3600, "new": 14400}
    assert audit.source_ip == "192.0.2.1"
    assert await _audit_count(db_session) == 1


async def test_record_run_preserves_unknown_counts_as_null(db_session) -> None:
    db_session.add(_row())
    await db_session.commit()
    service = SearchCadenceService(db_session, Settings(), lambda: T0)
    await service.record_run(
        SearchFamily.ANOMALY_EXPLANATION,
        outcome="ok",
        searches=None,
        buckets=None,
        splits=None,
    )
    await db_session.flush()
    row = await db_session.get(SearchCadenceSetting, SearchFamily.ANOMALY_EXPLANATION.value)
    assert row is not None
    assert row.last_run_at == T0
    assert row.last_run_searches is None
    assert row.last_run_buckets is None
    assert row.last_run_splits is None
