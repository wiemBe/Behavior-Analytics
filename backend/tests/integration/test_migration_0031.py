"""Migration 0031: nullable history-step error columns and a clean round trip."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")


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
                "WHERE table_schema = 'public' AND table_name = 'history_backfill_scope'"
            )
        ).scalars()
    )


def test_0031_adds_clear_error_columns_and_downgrade_drops_them(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0031")
    assert revision is not None and revision.down_revision == "0030"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0030")
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
                    "INSERT INTO history_backfill_scope "
                    "(instance_id, family, scope_key, state, progress) "
                    "VALUES (:instance, 'PHASE_A', '', 'READING', CAST('{}' AS jsonb))"
                ),
                {"instance": INSTANCE},
            )
            assert "last_error_class" not in _columns(connection)
            assert "last_error_at" not in _columns(connection)

        command.upgrade(config, "0031")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0031"
            assert connection.execute(
                text(
                    "SELECT last_error_class, last_error_at FROM history_backfill_scope "
                    "WHERE instance_id = :instance"
                ),
                {"instance": INSTANCE},
            ).one() == (None, None)

        command.downgrade(config, "0030")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0030"
            assert "last_error_class" not in _columns(connection)
            assert "last_error_at" not in _columns(connection)
            assert connection.execute(
                text("SELECT count(*) FROM history_backfill_scope")
            ).scalar_one() == 1

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT last_error_class, last_error_at FROM history_backfill_scope")
            ).one() == (None, None)
        command.check(config)
    finally:
        engine.dispose()
