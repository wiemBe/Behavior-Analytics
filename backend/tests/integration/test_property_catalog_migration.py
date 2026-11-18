"""0073 is additive and reversible, with no model drift."""

from __future__ import annotations

import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration


def test_property_catalog_migration_round_trip() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    sync_url = url.replace("+asyncpg", "+psycopg")
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    reset_public_schema(sync_url)
    engine = create_engine(sync_url)
    try:
        command.upgrade(cfg, "head")
        assert "property_catalog_entry" in inspect(engine).get_table_names()
        command.downgrade(cfg, "0071")
        assert "property_catalog_entry" not in inspect(engine).get_table_names()
        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        engine.dispose()
        reset_public_schema(sync_url)
