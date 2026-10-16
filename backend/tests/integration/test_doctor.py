from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.cli.doctor import Doctor, Status
from app.core.config import Settings
from app.models.enums import SearchFamily
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.search_cadence import SearchCadenceSetting
from tests.integration.schema_reset import reset_public_schema

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value


def _settings(database_url: str) -> Settings:
    parsed = make_url(database_url)
    return Settings(
        postgres_user=parsed.username or "",
        postgres_password=parsed.password or "",
        postgres_db=parsed.database or "",
        postgres_host=parsed.host or "localhost",
        postgres_port=parsed.port or 5432,
        encryption_key=os.environ["ENCRYPTION_KEY"],
    )


def _migrate(database_url: str) -> None:
    sync_url = database_url.replace("+asyncpg", "+psycopg")
    reset_public_schema(sync_url)
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(config, "head")


async def test_database_backed_doctor_checks_with_real_rows() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        async with maker() as session:
            instance = QRadarInstance(
                id=uuid.uuid4(),
                name="doctor-fixture",
                console_host="https://fixture.invalid",
                provider_kind="mock",
            )
            session.add(instance)
            await session.flush()
            session.add(
                CollectionWatermark(
                    instance_id=instance.id,
                    collector="log_source_metric",
                    watermark_at=now - timedelta(minutes=5),
                    last_run_at=now,
                    lag_seconds=10,
                    intervals_collected=1,
                    consecutive_failures=0,
                    collection_metadata={},
                )
            )
            await session.execute(update(SearchCadenceSetting).values(last_run_at=now))
            await session.commit()

        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        assert (await doctor.check_db_connect()).status is Status.PASS
        assert (await doctor.check_migration_head()).status is Status.PASS
        assert (await doctor.check_timescale()).status is Status.PASS
        assert (await doctor.check_watermarks()).status is Status.PASS
        assert (await doctor.check_cadence()).status is Status.PASS

        mismatch = Doctor(
            settings=_settings(database_url),
            sessionmaker=maker,
            now=lambda: now,
            migration_head=lambda: "forced-mismatch",
        )
        assert (await mismatch.check_migration_head()).status is Status.FAIL

        async with maker() as session:
            watermark = await session.scalar(
                update(CollectionWatermark)
                .values(lag_seconds=10_000)
                .returning(CollectionWatermark.id)
            )
            assert watermark is not None
            await session.execute(
                update(SearchCadenceSetting)
                .where(SearchCadenceSetting.family == SearchFamily.PHASE_A_VOLUME.value)
                .values(last_run_at=now - timedelta(hours=2))
            )
            await session.commit()
        assert (await doctor.check_watermarks()).status is Status.WARN
        assert (await doctor.check_cadence()).status is Status.WARN
    finally:
        await engine.dispose()
