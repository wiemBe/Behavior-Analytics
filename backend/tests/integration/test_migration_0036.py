"""Migration 0036: the rule-health snapshot reason reference and a clean round trip."""

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
RULE = uuid.UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)
TABLE = "rule_health_snapshot"
REASON = "The rule is disabled in QRadar, so it cannot produce detections."


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
                "INSERT INTO analytics_rule "
                "(id, instance_id, qradar_id, name, rule_type, enabled, "
                "is_building_block, origin, mitre_techniques, categories, "
                "response_actions, health_monitoring_enabled, "
                "event_contribution_count, offense_contribution_count, health_status) "
                "VALUES (:rule, :instance, 4821, 'Excessive Failed Logins', 'EVENT', true, "
                "false, 'USER', '[]', '[]', '[]', true, 0, 0, 'UNKNOWN')"
            ),
            {"rule": RULE, "instance": INSTANCE},
        )
        connection.execute(
            text(
                "INSERT INTO rule_health_snapshot "
                "(rule_id, evaluated_at, status, logic_version, confidence, reason, "
                "trigger_count, offense_contribution_count, enabled, "
                "missing_dependencies, evidence) VALUES "
                "(:rule, :now, 'DISABLED', 1, 1.0, :reason, 0, 0, false, '[]', '{}')"
            ),
            {"rule": RULE, "now": NOW, "reason": REASON},
        )


def _reason_i18n(engine) -> object:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        return connection.execute(
            text("SELECT reason_i18n FROM rule_health_snapshot")
        ).scalar_one()


def test_0036_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0036")
    assert revision is not None and revision.down_revision == "0035"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0035")
        assert "reason_i18n" not in {
            column["name"] for column in inspect(engine).get_columns(TABLE)
        }
        _seed_old_row(engine)

        command.upgrade(config, "0036")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0036"
        column = next(
            c for c in inspect(engine).get_columns(TABLE) if c["name"] == "reason_i18n"
        )
        assert column["nullable"] is True
        assert _reason_i18n(engine) is None
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE rule_health_snapshot SET reason_i18n = "
                    'CAST(\'{"code":"rule_health.disabled","params":{}}\' AS jsonb)'
                )
            )

        command.downgrade(config, "0035")
        assert "reason_i18n" not in {
            column["name"] for column in inspect(engine).get_columns(TABLE)
        }
        with engine.begin() as connection:
            kept = connection.execute(text("SELECT reason FROM rule_health_snapshot")).scalar_one()
        assert kept == REASON

        command.upgrade(config, "0036")
        assert _reason_i18n(engine) is None
        # Drift is checked at head, so a later migration cannot hide a model gap.
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
