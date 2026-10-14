"""The IPS cadence gate has one winner for each settled slot."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.enums import SearchFamily
from app.models.search_cadence import SearchCadenceSetting
from app.services.search_cadence import SearchCadenceService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 13, 12, 2, tzinfo=UTC)


async def test_exactly_one_ips_dispatcher_claims_a_slot(db_schema) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            SearchCadenceSetting(
                family=SearchFamily.IPS_COLLECTION.value,
                cadence_seconds=3600,
                updated_at=NOW,
            )
        )
        await session.commit()

    async def claim():  # type: ignore[no-untyped-def]
        async with maker() as session:
            slot = await SearchCadenceService(
                session,
                Settings(search_cadence_settle_seconds=120),
                lambda: NOW,
            ).claim_slot(SearchFamily.IPS_COLLECTION)
            await session.commit()
            return slot

    try:
        claims = await asyncio.gather(claim(), claim())
        assert sum(slot is not None for slot in claims) == 1
    finally:
        await engine.dispose()
