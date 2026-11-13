"""Migration 0067: notification delivery attempts round trip, checks, cascade and drift."""

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

COLUMNS = {"id", "notification_id", "attempted_at", "http_status", "error_class"}


def _sync_url() -> str:
    return os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")


def _config():  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _sync_url())
    return config


def test_0067_round_trip_checks_and_alembic_check() -> None:
    from alembic import command

    url = _sync_url()
    reset_public_schema(url)
    config = _config()
    engine = create_engine(url, future=True)
    try:
        command.upgrade(config, "0066")
        assert "notification_attempt" not in inspect(engine).get_table_names()
        command.upgrade(config, "0067")
        inspector = inspect(engine)
        assert {c["name"] for c in inspector.get_columns("notification_attempt")} == COLUMNS
        assert any(
            ix["column_names"] == ["notification_id", "attempted_at"]
            for ix in inspector.get_indexes("notification_attempt")
        )
        command.downgrade(config, "0066")
        assert "notification_attempt" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        command.check(config)

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO alert (id, fingerprint, dedup_key, title, severity, status, "
                    "source_type, source_id, occurrence_count, first_seen_at, last_seen_at, "
                    "opened_at, context, evidence_snapshot, source_anomaly_ids, created_at, "
                    "updated_at) VALUES ('00000000-0000-0000-0000-0000000000a1', 'f', 'd', 't', "
                    "'HIGH', 'OPEN', 'log_source', gen_random_uuid(), 1, now(), now(), now(), "
                    "'{}', '{}', '[]', now(), now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO alert_notification (id, alert_id, channel, target, transition, "
                    "status, attempts, max_attempts, payload, created_at, updated_at) VALUES "
                    "('00000000-0000-0000-0000-0000000000b1', "
                    "'00000000-0000-0000-0000-0000000000a1', 'SLACK', 't', 'OPENED', 'PENDING', "
                    "0, 3, '{}', now(), now())"
                )
            )
        insert = text(
            "INSERT INTO notification_attempt (id, notification_id, attempted_at, http_status, "
            "error_class) VALUES (gen_random_uuid(), "
            "'00000000-0000-0000-0000-0000000000b1', now(), :status, :cls)"
        )
        with engine.begin() as conn:
            conn.execute(insert, {"status": 503, "cls": "http_status"})
            conn.execute(insert, {"status": None, "cls": "payload_invalid"})
        for status, cls in ((503, "bogus"), (99, "ok"), (600, "ok")):
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(insert, {"status": status, "cls": cls})
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM alert_notification"))
            count = conn.execute(text("SELECT count(*) FROM notification_attempt")).scalar()
        assert count == 0  # ON DELETE CASCADE
    finally:
        engine.dispose()
