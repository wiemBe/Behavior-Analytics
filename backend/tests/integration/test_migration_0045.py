"""Migration 0045: the index behind the rule-match window retention delete.

``rule_match_window`` is a plain table. Its primary key leads with
``instance_id``, so ``WHERE window_start < :cutoff`` cannot use it. Upgrade
creates ``ix_rule_match_window_window_start``, downgrade to ``0044`` drops it,
and a second upgrade brings it back. Drift is checked at head.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
TABLE = "rule_match_window"
INDEX = "ix_rule_match_window_window_start"


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


def _window_index(engine: object) -> dict[str, object] | None:
    found = [
        index
        for index in inspect(engine).get_indexes(TABLE)
        if index["name"] == INDEX
    ]
    assert len(found) <= 1
    return found[0] if found else None


def test_0045_indexes_window_start_and_round_trips(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0045")
    assert revision is not None and revision.down_revision == "0044"
    heads = script.get_heads()
    assert len(heads) == 1 and "0045" in {
        item.revision for item in script.walk_revisions("base", heads[0])
    }
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == heads[0]
        created = _window_index(engine)
        assert created is not None
        assert created["column_names"] == ["window_start"]
        assert created["unique"] is False

        command.downgrade(config, "0044")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0044"
        assert _window_index(engine) is None

        command.upgrade(config, "head")
        restored = _window_index(engine)
        assert restored is not None
        assert restored["column_names"] == ["window_start"]
        command.check(config)
    finally:
        engine.dispose()
