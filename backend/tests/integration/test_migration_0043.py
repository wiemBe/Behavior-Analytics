"""Migration 0043: the message reference beside the history-backfill scope reason.

MSG-5, MSG-6 and MSG-7 add 0038-0040 in parallel and the merge only changes this migration's
``down_revision``, so nothing here names a neighbour revision, and nothing assumes this one is
the head: it upgrades to its own revision, downgrades to its ``down_revision`` and checks drift
at head.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
TABLE = "history_backfill_scope"
REASON = "the scope reads its next step"


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


def _columns(engine) -> dict[str, dict]:  # type: ignore[no-untyped-def,type-arg]
    return {column["name"]: column for column in inspect(engine).get_columns(TABLE)}


def test_0043_adds_a_nullable_reason_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0043")
    assert revision is not None
    down = revision.down_revision
    assert isinstance(down, str)
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, down)
        assert "reason_i18n" not in _columns(engine)
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
                    f"INSERT INTO {TABLE} "  # noqa: S608 - fixed name
                    "(instance_id, family, scope_key, state, reason, progress) "
                    "VALUES (:instance, 'PHASE_A', '', 'READING', :reason, CAST('{}' AS jsonb))"
                ),
                {"instance": INSTANCE, "reason": REASON},
            )

        command.upgrade(config, "0043")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0043"
        column = _columns(engine)["reason_i18n"]
        assert column["nullable"] is True
        assert str(column["type"]).upper().startswith("JSONB"), column["type"]
        with engine.begin() as connection:
            # An old row stays NULL: no backfill, and its English text is untouched.
            assert connection.execute(
                text(f"SELECT reason, reason_i18n FROM {TABLE}")  # noqa: S608
            ).one() == (REASON, None)
            connection.execute(
                text(
                    f"UPDATE {TABLE} SET reason_i18n = CAST("  # noqa: S608
                    '\'{"code":"history.settle.budget_ended","params":{}}\' AS jsonb)'
                )
            )

        command.downgrade(config, down)
        assert "reason_i18n" not in _columns(engine)
        with engine.begin() as connection:
            assert (
                connection.execute(text(f"SELECT reason FROM {TABLE}")).scalar_one()  # noqa: S608
                == REASON
            )

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(
                text(f"SELECT reason_i18n FROM {TABLE}")  # noqa: S608
            ).scalar_one() is None
        # Drift is checked at head, so the models and the migrations agree.
        command.check(config)
    finally:
        engine.dispose()
