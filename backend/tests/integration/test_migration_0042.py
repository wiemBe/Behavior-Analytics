"""Migration 0042: a search run's message reference and a clean round trip."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
SEARCH = uuid.UUID("11111111-1111-1111-1111-111111111111")
EXECUTION = uuid.UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 10, 29, 9, tzinfo=UTC)


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
    return {column["name"]: column for column in inspect(engine).get_columns("search_execution")}


def _seed_old_execution(engine) -> None:  # type: ignore[no-untyped-def]
    from app.models.search import ScheduledSearch, SearchExecution

    with engine.begin() as connection:
        connection.execute(
            ScheduledSearch.__table__.insert().values(
                id=SEARCH,
                name="migration search",
                aql_query="SELECT sourceip FROM events LAST 1 HOURS",
                schedule_cron="0 * * * *",
                schedule_timezone="UTC",
                query_version=1,
                severity="MEDIUM",
                timeout_seconds=300,
                max_time_range_hours=24,
                max_result_rows=1000,
                visualization_type="TABLE",
                enabled=False,
                consecutive_failures=0,
            )
        )
        connection.execute(
            SearchExecution.__table__.insert().values(
                id=EXECUTION,
                search_id=SEARCH,
                run_key="old",
                trigger="SCHEDULED",
                query_version=1,
                status="TIMEOUT",
                started_at=NOW,
                error_type="TIMEOUT",
                error_message="search exceeded timeout of 300s",
                truncated=False,
                retry_count=0,
                threshold_breached=False,
            )
        )


def _stored(engine) -> object:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        return connection.execute(
            text("SELECT error_message_i18n FROM search_execution WHERE id = :id"),
            {"id": EXECUTION},
        ).scalar_one()


def test_0042_adds_nullable_i18n_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    revision = ScriptDirectory.from_config(config).get_revision("0042")
    assert revision is not None and revision.down_revision == "0037"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0037")
        assert "error_message_i18n" not in _columns(engine)
        _seed_old_execution(engine)

        command.upgrade(config, "0042")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0042"
        assert _columns(engine)["error_message_i18n"]["nullable"] is True
        assert _stored(engine) is None, "an old row stays NULL: no backfill"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE search_execution SET error_message_i18n = CAST(:v AS jsonb) "
                    "WHERE id = :id"
                ),
                {"id": EXECUTION, "v": '{"code":"search.run.timeout","params":{"timeout":300}}'},
            )

        command.downgrade(config, "0037")
        assert "error_message_i18n" not in _columns(engine)
        with engine.begin() as connection:
            old = connection.execute(
                text("SELECT error_message FROM search_execution WHERE id = :id"),
                {"id": EXECUTION},
            ).scalar_one()
        assert old == "search exceeded timeout of 300s", "the text survives the downgrade"

        command.upgrade(config, "0042")
        assert _stored(engine) is None
        # Drift is checked at head, so a later migration cannot hide a model gap.
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
