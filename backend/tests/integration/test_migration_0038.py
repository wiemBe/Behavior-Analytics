"""Migration 0038: the message references beside the Phase A sentences and every transition reason.

The migration is upgraded to its own revision and downgraded to its ``down_revision``; nothing
here assumes it is the head (MSG-3's 0036 merges below it, and later migrations sit above it).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOURCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
ANOMALY = uuid.UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
COLUMNS: tuple[tuple[str, str], ...] = (
    ("log_source_anomaly", "explanation_i18n"),
    ("log_source_anomaly", "suppression_reason_i18n"),
    ("anomaly_state_transition", "reason_i18n"),
    ("waf_anomaly_transition", "reason_i18n"),
    ("ips_anomaly_transition", "reason_i18n"),
)


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


def _column_names(engine, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _seed_old_rows(engine) -> None:  # type: ignore[no-untyped-def]
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
            text(
                "INSERT INTO log_source (id, instance_id, qradar_id, name, enabled, criticality, "
                "monitoring_enabled, maintenance_mode, business_hours_only, "
                "business_hours_start, business_hours_end, business_days, timezone_name, "
                "custom_thresholds, first_seen_at, created_at, updated_at) VALUES "
                "(:id, :instance, 1, 'src', true, 'MEDIUM', true, false, false, 9, 17, "
                "'[1,2,3,4,5]'::jsonb, 'UTC', '{}'::jsonb, :now, :now, :now)"
            ),
            {"id": SOURCE, "instance": INSTANCE, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO log_source_anomaly (id, log_source_id, anomaly_type, severity, "
                "detected_at, explanation, details, suppressed, suppression_reason, state, "
                "consecutive_buckets, policy_version, evidence_status, created_at, updated_at) "
                "VALUES (:id, :source, 'COLLECTION_ERROR', 'HIGH', :now, "
                "'3 collection error(s) in the interval', '{}'::jsonb, true, 'maintenance window', "
                "'OPEN', 2, 1, 'NOT_REQUESTED', :now, :now)"
            ),
            {"id": ANOMALY, "source": SOURCE, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO anomaly_state_transition (id, anomaly_id, to_state, occurred_at, "
                "reason, actor, created_at, updated_at) VALUES "
                "(:id, :anomaly, 'OPEN', :now, 'first abnormal bucket', 'anomaly-engine', "
                ":now, :now)"
            ),
            {"id": uuid.uuid4(), "anomaly": ANOMALY, "now": NOW},
        )


def _stored(engine) -> list[object]:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        return [
            connection.execute(
                text(f"SELECT {column} FROM {table} LIMIT 1")  # noqa: S608 - fixed names
            ).scalar_one_or_none()
            for table, column in COLUMNS
        ]


def test_0038_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0038")
    assert revision is not None and revision.down_revision == "0037"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0037")
        for table, column in COLUMNS:
            assert column not in _column_names(engine, table)
        _seed_old_rows(engine)

        command.upgrade(config, "0038")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0038"
        for table, column in COLUMNS:
            found = next(c for c in inspect(engine).get_columns(table) if c["name"] == column)
            assert found["nullable"] is True
            assert str(found["type"]).upper().startswith("JSONB"), found["type"]
        # Old rows stay NULL: no backfill, and their English text is untouched.
        assert _stored(engine)[:3] == [None, None, None]
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT explanation FROM log_source_anomaly")
            ).scalar_one() == "3 collection error(s) in the interval"
            connection.execute(
                text(
                    "UPDATE anomaly_state_transition SET reason_i18n = CAST("
                    '\'{"code":"anomaly.lifecycle.first_abnormal","params":{}}\' AS jsonb)'
                )
            )

        command.downgrade(config, "0037")
        for table, column in COLUMNS:
            assert column not in _column_names(engine, table)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT reason FROM anomaly_state_transition")
            ).scalar_one() == "first abnormal bucket"
            assert connection.execute(
                text("SELECT suppression_reason FROM log_source_anomaly")
            ).scalar_one() == "maintenance window"

        command.upgrade(config, "0038")
        assert _stored(engine)[:3] == [None, None, None]
        # Drift is checked at head, so the models and every migration agree.
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
