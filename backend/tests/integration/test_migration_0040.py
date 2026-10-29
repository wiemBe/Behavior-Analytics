"""Migration 0040 adds nullable message metadata without rewriting English text."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
COLUMNS = (
    ("alert", "title_i18n"),
    ("alert", "description_i18n"),
    ("alert", "resolution_reason_i18n"),
    ("custom_rule", "last_status_reason_i18n"),
)


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _config(url: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _names(engine, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _seed_old_rows(engine) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    alert_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO alert (id, dedup_key, fingerprint, title, description, severity, "
                "status, source_type, context, opened_at, first_seen_at, occurrence_count, "
                "evidence_snapshot, source_anomaly_ids, created_at, updated_at) VALUES "
                "(:id, 'old-alert', 'old-alert', 'stored title', 'stored description', 'HIGH', "
                "'OPEN', 'test', '{}'::jsonb, now(), now(), 1, '{}'::jsonb, '[]'::jsonb, "
                "now(), now())"
            ),
            {"id": alert_id},
        )
        connection.execute(
            text(
                "INSERT INTO custom_rule (id, name, subject_kind, source_class, metric, "
                "operator, threshold_value, sustain_buckets, recovery_buckets, severity, "
                "enabled, consecutive_breaching, consecutive_under, last_status, "
                "last_status_reason, created_at, updated_at) VALUES "
                "(:id, 'old rule', 'FLEET_CLASS', 'OTHER', 'EPS', 'GT', 1, 1, 1, 'HIGH', "
                "false, 0, 0, 'DISABLED', 'stored reason', now(), now())"
            ),
            {"id": rule_id},
        )
    return alert_id, rule_id


def test_0040_round_trip_and_drift() -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    url = _sync_url()
    reset_public_schema(url)
    config = _config(url)
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0040")
    assert revision is not None
    engine = create_engine(url, future=True)
    try:
        command.upgrade(config, revision.down_revision or "base")
        for table, column in COLUMNS:
            assert column not in _names(engine, table)
        alert_id, rule_id = _seed_old_rows(engine)

        command.upgrade(config, "0040")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0040"
            alert = connection.execute(
                text(
                    "SELECT title, description, title_i18n, description_i18n, "
                    "resolution_reason_i18n FROM alert WHERE id = :id"
                ),
                {"id": alert_id},
            ).one()
            rule = connection.execute(
                text(
                    "SELECT last_status_reason, last_status_reason_i18n "
                    "FROM custom_rule WHERE id = :id"
                ),
                {"id": rule_id},
            ).one()
        assert tuple(alert) == (
            "stored title", "stored description", None, None, None
        )
        assert tuple(rule) == ("stored reason", None)

        command.downgrade(config, revision.down_revision or "base")
        for table, column in COLUMNS:
            assert column not in _names(engine, table)
        command.upgrade(config, "0040")
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
