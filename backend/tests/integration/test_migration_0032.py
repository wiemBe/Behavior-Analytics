"""Migration 0032: the anomaly evaluation fault table and a clean round trip."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOURCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
BUCKET = datetime(2026, 10, 22, 10, tzinfo=UTC)


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _alembic_config(sync_url: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


@pytest.fixture
def clean_db() -> str:
    sync_url = _sync_url()
    reset_public_schema(sync_url)
    return sync_url


def _columns(connection) -> set[str]:  # type: ignore[no-untyped-def]
    return set(
        connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'anomaly_evaluation_fault'"
            )
        ).scalars()
    )


def _seed_source(engine) -> None:  # type: ignore[no-untyped-def]
    from app.models.log_source import LogSource

    with engine.begin() as connection:
        connection.execute(LogSource.__table__.insert().values(
            id=SOURCE, instance_id=INSTANCE, qradar_id=1, name="migration"
        ))


def _fault_insert(**overrides):  # type: ignore[no-untyped-def]
    from app.models.monitoring import AnomalyEvaluationFault

    values = {
        "log_source_id": SOURCE,
        "bucket_start": BUCKET,
        "attempts": 1,
        "first_failed_at": BUCKET,
        "last_failed_at": BUCKET,
        "error_class": "RuntimeError",
    }
    values.update(overrides)
    return AnomalyEvaluationFault.__table__.insert().values(values)


def test_0032_adds_the_fault_table_and_downgrade_removes_it(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0032")
    assert revision is not None and revision.down_revision == "0031"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0031")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO qradar_instance "
                    "(id, name, console_host, api_version, verify_ssl, provider_kind, "
                    "mcp_enabled, enabled, status, consecutive_failures) VALUES "
                    "(:instance, 'lab', 'qradar.lab.local', '20.0', true, 'REST', false, "
                    "true, 'HEALTHY', 0)"
                ),
                {"instance": INSTANCE},
            )
            assert "anomaly_evaluation_fault" not in set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                ).scalars()
            )

        command.upgrade(config, "0032")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0032"
            assert _columns(connection) == {
                "id",
                "log_source_id",
                "bucket_start",
                "attempts",
                "first_failed_at",
                "last_failed_at",
                "error_class",
                "given_up_at",
                "resolved_at",
            }

        _seed_source(engine)
        with engine.begin() as connection:
            connection.execute(_fault_insert())

        # One row per (source, bucket).
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(_fault_insert())
        # attempts counts real ticks, so zero is refused.
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                _fault_insert(bucket_start=BUCKET.replace(minute=5), attempts=0)
            )
        # The fault goes with its source.
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM log_source WHERE id = :source"), {"source": SOURCE}
            )
            assert connection.execute(
                text("SELECT count(*) FROM anomaly_evaluation_fault")
            ).scalar_one() == 0

        command.downgrade(config, "0031")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0031"
            assert _columns(connection) == set()

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert _columns(connection) == {
                "id",
                "log_source_id",
                "bucket_start",
                "attempts",
                "first_failed_at",
                "last_failed_at",
                "error_class",
                "given_up_at",
                "resolved_at",
            }
            assert connection.execute(
                text("SELECT count(*) FROM anomaly_evaluation_fault")
            ).scalar_one() == 0
        command.check(config)
    finally:
        engine.dispose()
