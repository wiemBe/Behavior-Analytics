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
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import (
    AnomalyEvaluationFault,
    AnomalyEvaluationWatermark,
    CollectionWatermark,
)
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


# ---------------------------------------------- anomaly evaluation check
# `AnomalyEvaluationWatermark` is not a `CollectionWatermark`, so the watermarks
# check cannot see it. The `anomaly_evaluation` check owns that question.

async def _fault_doctor_fixture(maker, now):
    """One monitored meter, its only bucket and the watermark that judged it."""
    instance = QRadarInstance(
        id=uuid.uuid4(),
        name="diagnosed",
        console_host="https://fixture.invalid",
        provider_kind="mock",
    )
    bucket = now - timedelta(minutes=5)
    async with maker() as session:
        session.add(instance)
        await session.flush()
        source = LogSource(
            instance_id=instance.id,
            qradar_id=1,
            name="diagnosed-source",
            monitoring_enabled=True,
        )
        session.add(source)
        await session.flush()
        session.add(LogSourceMetric(log_source_id=source.id, bucket_start=bucket))
        session.add(
            AnomalyEvaluationWatermark(
                log_source_id=source.id, evaluated_through=bucket, updated_at=now
            )
        )
        await session.commit()
    return source.id, bucket


async def test_anomaly_evaluation_check_passes_when_every_bucket_is_judged() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        await _fault_doctor_fixture(maker, now)
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.PASS
        assert result.runbook is None
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_warns_while_a_fault_is_retried() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        source_id, bucket = await _fault_doctor_fixture(maker, now)
        async with maker() as session:
            session.add(
                AnomalyEvaluationFault(
                    log_source_id=source_id,
                    bucket_start=bucket,
                    attempts=1,
                    first_failed_at=now - timedelta(minutes=3),
                    last_failed_at=now - timedelta(minutes=2),
                    error_class="RuntimeError",
                )
            )
            await session.commit()
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.WARN
        assert result.runbook == "OPS-R61"
        assert "failing evaluation" in result.message
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_fails_on_a_bucket_given_up_in_24h() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        source_id, bucket = await _fault_doctor_fixture(maker, now)
        async with maker() as session:
            session.add(
                AnomalyEvaluationFault(
                    log_source_id=source_id,
                    bucket_start=bucket,
                    attempts=3,
                    first_failed_at=now - timedelta(minutes=5),
                    last_failed_at=now - timedelta(minutes=3),
                    error_class="RuntimeError",
                    given_up_at=now - timedelta(minutes=3),
                )
            )
            await session.commit()
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.FAIL
        assert result.runbook == "OPS-R61"
        assert "1 anomaly bucket(s) were not judged" in result.message
        assert "RuntimeError" in result.message
        assert "2026-10-16 11:57 UTC" in result.message
    finally:
        await engine.dispose()


async def _trailing_source(maker, *, bucket_start, evaluated_through, bucket_seconds=300):
    """One monitored source, one live bucket, and an optional watermark."""
    instance = QRadarInstance(
        id=uuid.uuid4(),
        name="diagnosed",
        console_host="https://fixture.invalid",
        provider_kind="mock",
    )
    async with maker() as session:
        session.add(instance)
        await session.flush()
        source = LogSource(
            instance_id=instance.id,
            qradar_id=2,
            name="trailing-source",
            monitoring_enabled=True,
        )
        session.add(source)
        await session.flush()
        session.add(
            LogSourceMetric(
                log_source_id=source.id,
                bucket_start=bucket_start,
                bucket_seconds=bucket_seconds,
            )
        )
        if evaluated_through is not None:
            session.add(
                AnomalyEvaluationWatermark(
                    log_source_id=source.id,
                    evaluated_through=evaluated_through,
                    updated_at=bucket_start,
                )
            )
        await session.commit()
    return source.id


async def test_anomaly_evaluation_check_passes_for_a_just_collected_pending_bucket() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        # The watermark is one 300-s bucket behind the newest one: the naive
        # "difference > 2 ticks" rule WARNed here, but the bucket was written
        # minutes ago and its judging allowance has not run out.
        bucket = now - timedelta(minutes=5)
        await _trailing_source(
            maker, bucket_start=bucket, evaluated_through=bucket - timedelta(minutes=5)
        )
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.PASS
        assert result.runbook is None
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_warns_when_a_source_trails() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        # Same 300-s pending gap, but old enough that every allowance has
        # elapsed: bucket end + settle + one beat + two evaluation ticks.
        bucket = now - timedelta(minutes=20)
        await _trailing_source(
            maker, bucket_start=bucket, evaluated_through=bucket - timedelta(minutes=5)
        )
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.WARN
        assert result.runbook == "OPS-R61"
        assert "should already have been judged" in result.message
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_passes_for_a_new_sources_just_collected_bucket() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        # No watermark yet. The bucket's start is already five minutes old, but
        # the first evaluation tick has not had its allowance yet.
        await _trailing_source(
            maker, bucket_start=now - timedelta(minutes=5), evaluated_through=None
        )
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.PASS
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_warns_for_a_new_sources_stale_bucket() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        await _trailing_source(
            maker, bucket_start=now - timedelta(minutes=20), evaluated_through=None
        )
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.WARN
        assert result.runbook == "OPS-R61"
        assert "should already have been judged" in result.message
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_uses_each_rows_own_bucket_width() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        # An 900-s bucket collected 11 min ago: with the row's own width its
        # allowance runs out at now + 9 min, so it is not late. A fixed 300-s
        # width would call it late (now - 1 min) and WARN.
        await _trailing_source(
            maker,
            bucket_start=now - timedelta(minutes=11),
            evaluated_through=None,
            bucket_seconds=900,
        )
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.PASS
    finally:
        await engine.dispose()


async def test_anomaly_evaluation_check_fails_with_the_class_when_unreadable() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    try:
        from sqlalchemy import text

        async with maker() as session:
            await session.execute(text("DROP TABLE anomaly_evaluation_fault"))
            await session.commit()
        doctor = Doctor(settings=_settings(database_url), sessionmaker=maker, now=lambda: now)
        result = await doctor.check_anomaly_evaluation()
        assert result.status is Status.FAIL
        assert result.runbook == "OPS-R61"
        assert result.message.startswith("anomaly evaluation state could not be read (")
        # The class only: no driver message, no SQL, no data value.
        assert result.message.endswith(")")
    finally:
        await engine.dispose()
