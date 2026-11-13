"""Migration 0066: notification routing rules round trip, checks and drift."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.schema_reset import reset_public_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

COLUMNS = {
    "id", "name", "enabled", "priority", "min_severity", "families", "owners",
    "source_classes", "group_ids", "window_start_minute", "window_end_minute",
    "destination_ids", "stop_default", "created_by", "created_at", "updated_by", "updated_at",
}  # fmt: skip

INSERT = text(
    "INSERT INTO notification_rule (id, name, enabled, priority, min_severity, "
    "window_start_minute, window_end_minute, destination_ids, stop_default, created_by, "
    "updated_by) VALUES (gen_random_uuid(), :name, true, 1, :sev, :ws, :we, "
    "ARRAY[gen_random_uuid()], false, 'x', 'x')"
)


def _sync_url() -> str:
    return os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")


def _config():  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _sync_url())
    return config


def test_0066_round_trip_checks_and_alembic_check() -> None:
    from alembic import command

    url = _sync_url()
    reset_public_schema(url)
    config = _config()
    engine = create_engine(url, future=True)
    try:
        command.upgrade(config, "0065")
        assert "notification_rule" not in inspect(engine).get_table_names()
        command.upgrade(config, "0066")
        assert {c["name"] for c in inspect(engine).get_columns("notification_rule")} == COLUMNS
        command.downgrade(config, "0065")
        assert "notification_rule" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        command.check(config)

        def insert(name: str, sev: str, ws: int | None, we: int | None) -> None:
            with engine.begin() as conn:
                conn.execute(INSERT, {"name": name, "sev": sev, "ws": ws, "we": we})

        insert("ok", "LOW", None, None)
        insert("ok-window", "HIGH", 1380, 360)
        for name, sev, ws, we in (
            ("info", "INFO", None, None),
            ("equal", "LOW", 5, 5),
            ("half", "LOW", 5, None),
            ("range", "LOW", 0, 1440),
            ("ok", "LOW", None, None),
        ):
            with pytest.raises(IntegrityError):
                insert(name, sev, ws, we)
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO notification_rule (id, name, enabled, priority, min_severity, "
                    "destination_ids, stop_default, created_by, updated_by) VALUES "
                    "(gen_random_uuid(), 'none', true, 1, 'LOW', '{}', false, 'x', 'x')"
                )
            )
    finally:
        engine.dispose()
