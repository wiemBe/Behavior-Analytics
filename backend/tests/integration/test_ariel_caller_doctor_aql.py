"""AQL-1: migration 0053 widens `caller_known` by `DOCTOR_AQL`, and a doctor row inserts."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.models.ariel_search import CALLERS
from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

INSERT = text(
    "INSERT INTO ariel_search_run (id, caller, submitted_at, finished_at, polls, outcome) "
    "VALUES (:id, :caller, :at, :at, 1, 'COMPLETED')"
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


def test_0053_round_trips_with_a_doctor_row_and_checks_clean() -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    sync_url = _sync_url()
    reset_public_schema(sync_url)
    config = _alembic_config(sync_url)
    revision = ScriptDirectory.from_config(config).get_revision("0053")
    assert revision is not None and revision.down_revision == "0052"
    assert "DOCTOR_AQL" in CALLERS
    engine = create_engine(sync_url, future=True)
    try:
        command.upgrade(config, "0053")
        doctor, kept = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0053"
            connection.execute(INSERT, {"id": doctor, "caller": "DOCTOR_AQL", "at": NOW})
            connection.execute(INSERT, {"id": kept, "caller": "RULE_MATCH", "at": NOW})

        command.downgrade(config, "0052")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0052"
            ids = set(connection.execute(text("SELECT id FROM ariel_search_run")).scalars())
        # The older check would refuse the doctor row, so it goes; the family row stays.
        assert ids == {kept}
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(INSERT, {"id": uuid.uuid4(), "caller": "DOCTOR_AQL", "at": NOW})

        command.upgrade(config, "0053")
        with engine.begin() as connection:
            connection.execute(INSERT, {"id": uuid.uuid4(), "caller": "DOCTOR_AQL", "at": NOW})
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(INSERT, {"id": uuid.uuid4(), "caller": "OTHER", "at": NOW})
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()


def test_the_orm_inserts_a_doctor_aql_row() -> None:
    from sqlalchemy.orm import Session

    from alembic import command
    from app.models.ariel_search import ArielSearchRun

    sync_url = _sync_url()
    reset_public_schema(sync_url)
    command.upgrade(_alembic_config(sync_url), "head")
    engine = create_engine(sync_url, future=True)
    try:
        with Session(engine) as session:
            session.add(
                ArielSearchRun(
                    id=uuid.uuid4(),
                    caller="DOCTOR_AQL",
                    submitted_at=NOW,
                    finished_at=NOW,
                    wall_ms=120,
                    polls=1,
                    outcome="COMPLETED",
                )
            )
            session.commit()
            callers = session.execute(text("SELECT caller FROM ariel_search_run")).scalars().all()
        assert callers == ["DOCTOR_AQL"]
    finally:
        engine.dispose()
