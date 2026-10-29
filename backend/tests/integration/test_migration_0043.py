"""Migration 0043: the message references beside the stored IPS anomaly sentences.

MSG-8a adds 0041 in parallel and the merge only changes this migration's ``down_revision``, so
nothing here names a neighbour revision: the migration is upgraded to the revision it follows,
then to itself, downgraded to it again, and upgraded once more.
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
ANOMALY = uuid.UUID("44444444-4444-4444-4444-444444444444")
NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)
TABLE = "ips_anomaly"
COLUMNS = ("explanation_i18n", "suppression_reason_i18n")


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


def _column_names(engine) -> set[str]:  # type: ignore[no-untyped-def]
    return {column["name"] for column in inspect(engine).get_columns(TABLE)}


def _seed_old_row(engine) -> None:  # type: ignore[no-untyped-def]
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
                "INSERT INTO log_source (id, instance_id, qradar_id, name, enabled, "
                "criticality, monitoring_enabled, maintenance_mode, business_hours_only, "
                "business_hours_start, business_hours_end, business_days, timezone_name, "
                "custom_thresholds, first_seen_at) VALUES (:source, :instance, 77, "
                "'ips-old', true, 'MEDIUM', true, false, false, 8, 18, "
                "CAST('[1,2,3,4,5]' AS jsonb), 'UTC', CAST('{}' AS jsonb), :now)"
            ),
            {"source": SOURCE, "instance": INSTANCE, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO ips_anomaly (id, subject_kind, log_source_id, detector_type, "
                "fingerprint, severity, state, detected_at, consecutive_buckets, "
                "policy_version, profile_version, normalizer_version, explanation, details, "
                "evidence_status, suppressed, suppression_reason, observed_measurement, "
                "created_at, updated_at) VALUES "
                "(:id, 'IPS_SOURCE', :source, 'IPS_BLOCK_RATIO_SHIFT', 'old-fingerprint', "
                "'MEDIUM', 'OPEN', :now, 2, 1, 1, 1, 'the stored explanation', '{}'::jsonb, "
                "'NOT_REQUESTED', true, 'maintenance window', 'EXACT', :now, :now)"
            ),
            {"id": ANOMALY, "source": SOURCE, "now": NOW},
        )


def _stored(engine) -> list[object]:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        return [
            connection.execute(
                text(f"SELECT {column} FROM {TABLE}")  # noqa: S608 - fixed names
            ).scalar_one()
            for column in COLUMNS
        ]


def test_0043_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0043")
    # The neighbour is looked up, not named: parallel tasks re-chain this migration at their merge.
    assert revision is not None and isinstance(revision.down_revision, str)
    previous = revision.down_revision
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, previous)
        assert not set(COLUMNS) & _column_names(engine)
        _seed_old_row(engine)

        command.upgrade(config, "0043")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0043"
        for name in COLUMNS:
            found = next(c for c in inspect(engine).get_columns(TABLE) if c["name"] == name)
            assert found["nullable"] is True
            assert str(found["type"]).upper().startswith("JSONB"), found["type"]
        # Old rows stay NULL: no backfill, and their English text is untouched.
        assert _stored(engine) == [None, None]
        with engine.begin() as connection:
            row = connection.execute(
                text("SELECT explanation, suppression_reason FROM ips_anomaly")
            ).one()
            assert tuple(row) == ("the stored explanation", "maintenance window")
            connection.execute(
                text(
                    "UPDATE ips_anomaly SET explanation_i18n = CAST("
                    '\'{"code":"ips.block.fired","params":{}}\' AS jsonb)'
                )
            )

        command.downgrade(config, previous)
        assert not set(COLUMNS) & _column_names(engine)
        with engine.begin() as connection:
            row = connection.execute(
                text("SELECT explanation, suppression_reason FROM ips_anomaly")
            ).one()
            assert tuple(row) == ("the stored explanation", "maintenance window")

        command.upgrade(config, "0043")
        assert _stored(engine) == [None, None]
        # Drift is checked at head, so a later migration cannot hide a model gap.
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
