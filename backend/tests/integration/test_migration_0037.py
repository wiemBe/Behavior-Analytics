"""Migration 0037: the message references beside the stored property-watch sentences.

MSG-3 adds 0036 in parallel and the merge only changes this migration's ``down_revision``, so
nothing here names a neighbour revision: the migration is upgraded to its own revision and
downgraded one step; the drift check runs at head (later migrations sit on top of it).
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
WATCH = uuid.UUID("22222222-2222-2222-2222-222222222222")
ANOMALY = uuid.UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)
COLUMNS = {
    "property_watch": "last_status_reason_i18n",
    "pw_anomaly": "explanation_i18n",
    "pw_anomaly_transition": "reason_i18n",
}
EXTRA = ("pw_anomaly", "suppression_reason_i18n")


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
                "INSERT INTO property_watch (id, instance_id, name, scope_kind, scope_type_id, "
                "properties, pinned_values, detectors, severity, watch_version, enabled, "
                "last_status, last_status_reason, history_state, history_progress, "
                "created_at, updated_at) "
                "VALUES (:id, :instance, 'old', 'LOG_SOURCE_TYPE', 212, "
                '\'[{"kind":"NORMALIZED","name":"username"}]\'::jsonb, \'[]\'::jsonb, '
                "'[]'::jsonb, 'MEDIUM', 1, true, 'STALE', 'the stored status', "
                "'NOT_REQUESTED', '{}'::jsonb, :now, :now)"
            ),
            {"id": WATCH, "instance": INSTANCE, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO pw_anomaly (id, watch_id, value_key, detector_type, fingerprint, "
                "severity, state, detected_at, consecutive_buckets, policy_version, "
                "watch_version, explanation, details, evidence_status, suppressed, "
                "observed_measurement, created_at, updated_at) VALUES "
                "(:id, :watch, 'alice', 'PW_VALUE_SPIKE', 'fp', 'MEDIUM', 'OPEN', :now, 2, 1, "
                "1, 'the stored explanation', '{}'::jsonb, 'NOT_REQUESTED', false, 'EXACT', "
                ":now, :now)"
            ),
            {"id": ANOMALY, "watch": WATCH, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO pw_anomaly_transition (id, pw_anomaly_id, to_state, occurred_at, "
                "reason, actor, observed_measurement, created_at, updated_at) VALUES "
                "(:id, :anomaly, 'RESOLVED', :now, 'condition recovered', 'pw-engine', "
                "'EXACT', :now, :now)"
            ),
            {"id": uuid.uuid4(), "anomaly": ANOMALY, "now": NOW},
        )


def _stored(engine) -> list[object]:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        return [
            connection.execute(
                text(f"SELECT {column} FROM {table}")  # noqa: S608 - fixed names
            ).scalar_one()
            for table, column in COLUMNS.items()
        ]


def test_0037_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0037")
    assert revision is not None
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, revision.down_revision or "base")
        for table, column in [*COLUMNS.items(), EXTRA]:
            assert column not in _column_names(engine, table)
        _seed_old_rows(engine)

        command.upgrade(config, "0037")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0037"
        for table, column in [*COLUMNS.items(), EXTRA]:
            found = next(c for c in inspect(engine).get_columns(table) if c["name"] == column)
            assert found["nullable"] is True
            assert str(found["type"]).upper().startswith("JSONB"), found["type"]
        # Old rows stay NULL: no backfill, and their English text is untouched.
        assert _stored(engine) == [None, None, None]
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT last_status_reason FROM property_watch")
            ).scalar_one() == "the stored status"
            connection.execute(
                text(
                    "UPDATE pw_anomaly_transition SET reason_i18n = CAST("
                    '\'{"code":"pw.transition.recovered","params":{}}\' AS jsonb)'
                )
            )

        command.downgrade(config, "-1")
        for table, column in [*COLUMNS.items(), EXTRA]:
            assert column not in _column_names(engine, table)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT reason FROM pw_anomaly_transition")
            ).scalar_one() == "condition recovered"
            assert connection.execute(
                text("SELECT explanation FROM pw_anomaly")
            ).scalar_one() == "the stored explanation"

        command.upgrade(config, "0037")
        assert _stored(engine) == [None, None, None]
        # Drift is checked at head, so the models and the migrations agree.
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
