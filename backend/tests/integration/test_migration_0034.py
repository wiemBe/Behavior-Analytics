"""Migration 0034: explanation-detail message references and clean round trip."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOURCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
ANOMALY = uuid.UUID("33333333-3333-3333-3333-333333333333")
PACKAGE = uuid.UUID("44444444-4444-4444-4444-444444444444")
DIMENSION = uuid.UUID("55555555-5555-5555-5555-555555555555")
NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)


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


def _columns(engine) -> dict[str, dict[str, object]]:  # type: ignore[no-untyped-def]
    return {
        column["name"]: column
        for column in inspect(engine).get_columns("anomaly_explanation_dimension")
    }


def _seed_old_dimension(engine) -> None:  # type: ignore[no-untyped-def]
    from app.models.explanation import AnomalyExplanation, AnomalyExplanationDimension
    from app.models.log_source import LogSource, LogSourceAnomaly

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
        connection.execute(
            LogSource.__table__.insert().values(
                id=SOURCE,
                instance_id=INSTANCE,
                qradar_id=1,
                name="migration source",
            )
        )
        connection.execute(
            LogSourceAnomaly.__table__.insert().values(
                id=ANOMALY,
                log_source_id=SOURCE,
                anomaly_type="VOLUME_SPIKE",
                severity="MEDIUM",
                detected_at=NOW,
                details={},
            )
        )
        connection.execute(
            AnomalyExplanation.__table__.insert().values(
                id=PACKAGE,
                anomaly_id=ANOMALY,
                status="PARTIAL",
                anomaly_window_start=NOW,
                anomaly_window_end=NOW + timedelta(minutes=5),
                baseline_window_start=NOW - timedelta(minutes=15),
                baseline_window_end=NOW,
                comparison_strategy="recent_normal_window",
                anomaly_total_events=10,
                baseline_total_events=5,
                query_provenance={},
            )
        )
        connection.execute(
            AnomalyExplanationDimension.__table__.insert().values(
                id=DIMENSION,
                explanation_id=PACKAGE,
                dimension="username",
                availability="UNAVAILABLE",
                detail="field is not populated for this log source",
            )
        )


def test_0034_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0034")
    assert revision is not None and revision.down_revision == "0033"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0033")
        assert "detail_i18n" not in _columns(engine)
        _seed_old_dimension(engine)

        command.upgrade(config, "0034")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0034"
            column = _columns(engine)["detail_i18n"]
            assert column["nullable"] is True
            assert connection.execute(
                text(
                    "SELECT detail_i18n FROM anomaly_explanation_dimension "
                    "WHERE id = :dimension"
                ),
                {"dimension": DIMENSION},
            ).scalar_one() is None
            connection.execute(
                text(
                    "UPDATE anomaly_explanation_dimension SET detail_i18n = "
                    "CAST(:value AS jsonb) WHERE id = :dimension"
                ),
                {
                    "dimension": DIMENSION,
                    "value": (
                        '{"code":"explanation.dimension.unpopulated","params":{}}'
                    ),
                },
            )

        command.downgrade(config, "0033")
        assert "detail_i18n" not in _columns(engine)

        command.upgrade(config, "0034")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0034"
            assert connection.execute(
                text(
                    "SELECT detail_i18n FROM anomaly_explanation_dimension "
                    "WHERE id = :dimension"
                ),
                {"dimension": DIMENSION},
            ).scalar_one() is None
        command.check(config)
    finally:
        engine.dispose()
