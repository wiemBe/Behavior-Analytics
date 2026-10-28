"""Migration 0035: coverage reason message references and clean round trip."""

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
NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)
TABLES = ("detection_coverage", "detection_coverage_snapshot")


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
                "INSERT INTO detection_coverage (id, instance_id, technique_id, status, "
                "mapped_rule_count, enabled_rule_count, firing_rule_count, inferred_rule_count, "
                "degraded_rule_count, coverage_score, confidence, logic_version, reason, "
                "evidence, created_at, updated_at) VALUES "
                "(:id, :instance, 'T1059', 'MISSING', 0, 0, 0, 0, 0, 0, 1, 1, "
                "'No rule is mapped to this technique.', '{}'::jsonb, :now, :now)"
            ),
            {"id": uuid.uuid4(), "instance": INSTANCE, "now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO detection_coverage_snapshot (instance_id, technique_id, "
                "captured_at, status, coverage_score, confidence, mapped_rule_count, "
                "enabled_rule_count, firing_rule_count, degraded_rule_count, logic_version, "
                "reason) VALUES (:instance, 'T1059', :now, 'MISSING', 0, 1, 0, 0, 0, 0, 1, "
                "'No rule is mapped to this technique.')"
            ),
            {"instance": INSTANCE, "now": NOW},
        )


def _reasons(engine) -> list[object]:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        return [
            connection.execute(text("SELECT reason_i18n FROM detection_coverage")).scalar_one(),
            connection.execute(
                text("SELECT reason_i18n FROM detection_coverage_snapshot")
            ).scalar_one(),
        ]


def test_0035_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0035")
    assert revision is not None and revision.down_revision == "0034"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0034")
        for table in TABLES:
            assert "reason_i18n" not in _column_names(engine, table)
        _seed_old_rows(engine)

        command.upgrade(config, "0035")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0035"
        for table in TABLES:
            column = next(
                c for c in inspect(engine).get_columns(table) if c["name"] == "reason_i18n"
            )
            assert column["nullable"] is True
        assert _reasons(engine) == [None, None]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE detection_coverage SET reason_i18n = "
                    'CAST(\'{"code":"coverage.rollup.unmapped","params":{}}\' AS jsonb)'
                )
            )

        command.downgrade(config, "0034")
        for table in TABLES:
            assert "reason_i18n" not in _column_names(engine, table)
        with engine.begin() as connection:
            kept = connection.execute(
                text("SELECT reason FROM detection_coverage")
            ).scalar_one()
        assert kept == "No rule is mapped to this technique."

        command.upgrade(config, "0035")
        assert _reasons(engine) == [None, None]
        # Drift is checked at head, so a later migration cannot hide a model gap.
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
