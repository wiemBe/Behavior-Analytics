"""The additive notification-destination migration round trip and drift check."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect

from tests.integration.schema_reset import reset_public_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]


def _sync_url() -> str:
    return os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")


def _config():  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _sync_url())
    return config


def test_0065_upgrade_downgrade_upgrade_and_check() -> None:
    from alembic import command

    url = _sync_url()
    reset_public_schema(url)
    config = _config()
    engine = create_engine(url, future=True)
    try:
        command.upgrade(config, "0064")
        assert "notification_destination" not in inspect(engine).get_table_names()
        command.upgrade(config, "0065")
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("notification_destination")
        }
        assert {
            "id",
            "name",
            "enabled",
            "url",
            "allow_http",
            "allow_private",
            "hmac_secret",
            "headers",
            "last_test_at",
            "last_test_status",
            "last_test_class",
            "created_by",
            "created_at",
            "updated_by",
            "updated_at",
        } == columns
        command.downgrade(config, "0064")
        assert "notification_destination" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "notification_destination" in inspect(engine).get_table_names()
        command.check(config)
    finally:
        engine.dispose()
