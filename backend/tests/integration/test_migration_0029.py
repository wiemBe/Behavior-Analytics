"""Migration 0029: property-watch history state, bucket provenance and round trip."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.models.enums import PwBucketOrigin, PwHistoryState, PwWatchStatus
from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = "11111111-1111-1111-1111-111111111111"
SOURCE = "22222222-2222-2222-2222-222222222222"
WATCH = "33333333-3333-3333-3333-333333333333"
VALUE = "44444444-4444-4444-4444-444444444444"
LIVE_START = "2026-09-01 09:00+00"
HISTORY_START = "2026-09-01 08:55+00"


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _alembic_config(sync_url: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    # Deliberately no alembic.ini: fileConfig() would disable app.* loggers for
    # every integration test that follows this one in the same process.
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


@pytest.fixture
def clean_db() -> str:
    sync_url = _sync_url()
    reset_public_schema(sync_url)
    return sync_url


def _seed_0028(connection) -> None:  # type: ignore[no-untyped-def]
    connection.execute(
        text(
            "INSERT INTO qradar_instance (id, name, console_host, api_version, verify_ssl, "
            "provider_kind, mcp_enabled, enabled, status, consecutive_failures) "
            "VALUES (CAST(:instance AS uuid), 'lab', 'qradar.lab.local', '20.0', true, "
            "'REST', false, true, 'HEALTHY', 0)"
        ),
        {"instance": INSTANCE},
    )
    connection.execute(
        text(
            "INSERT INTO log_source (id, instance_id, qradar_id, name, enabled, criticality, "
            "monitoring_enabled, maintenance_mode, business_hours_only, business_hours_start, "
            "business_hours_end, business_days, timezone_name, custom_thresholds, first_seen_at) "
            "VALUES (CAST(:source AS uuid), CAST(:instance AS uuid), 17, 'edge-fw', true, "
            "'MEDIUM', true, false, false, 8, 18, CAST('[1,2,3,4,5]' AS jsonb), 'UTC', "
            "CAST('{}' AS jsonb), now())"
        ),
        {"instance": INSTANCE, "source": SOURCE},
    )
    connection.execute(
        text(
            "INSERT INTO property_watch (id, instance_id, name, scope_kind, log_source_id, "
            "properties, pinned_values, detectors, severity, watch_version, enabled) "
            "VALUES (CAST(:watch AS uuid), CAST(:instance AS uuid), 'watch', 'LOG_SOURCE', "
            "CAST(:source AS uuid), CAST(:properties AS jsonb), CAST('[]' AS jsonb), "
            "CAST('[]' AS jsonb), 'MEDIUM', 1, true)"
        ),
        {
            "watch": WATCH,
            "instance": INSTANCE,
            "source": SOURCE,
            "properties": json.dumps([{"kind": "NORMALIZED", "name": "username"}]),
        },
    )
    connection.execute(
        text(
            "INSERT INTO pw_value (id, watch_id, watch_version, value_key, truncated, pinned, "
            "active) VALUES (CAST(:value AS uuid), CAST(:watch AS uuid), 1, 'alice', false, "
            "true, true)"
        ),
        {"value": VALUE, "watch": WATCH},
    )
    _insert_bucket(connection, LIVE_START)
    connection.execute(
        text(
            "INSERT INTO pw_value_metric (pw_value_id, bucket_start, events, bucket_seconds) "
            "VALUES (CAST(:value AS uuid), CAST(:start AS timestamptz), 7, 300)"
        ),
        {"value": VALUE, "start": LIVE_START},
    )


def _insert_bucket(connection, start: str, *, origin: str | None = None) -> None:  # type: ignore[no-untyped-def]
    params: dict[str, object] = {"watch": WATCH, "start": start}
    statement = (
        text(
            "INSERT INTO pw_bucket (watch_id, bucket_start, bucket_seconds, watch_version, "
            "total_events, parsed_events, unparsed_events, untracked_events, values_measurement, "
            "pinned_measurement, totals_complete, observed_values, tracked_values_observed, "
            "admitted_values, below_admission_threshold, rejected_over_quota, "
            "admission_truncated, scope_changed, in_maintenance, query_provenance, collected_at) "
            "VALUES (CAST(:watch AS uuid), CAST(:start AS timestamptz), 300, 1, "
            "7, 7, 0, 0, 'EXACT', 'EXACT', true, 1, 1, 0, 0, 0, false, false, false, "
            "CAST('{}' AS jsonb), now())"
        )
        if origin is None
        else text(
            "INSERT INTO pw_bucket (watch_id, bucket_start, bucket_seconds, watch_version, "
            "total_events, parsed_events, unparsed_events, untracked_events, values_measurement, "
            "pinned_measurement, totals_complete, observed_values, tracked_values_observed, "
            "admitted_values, below_admission_threshold, rejected_over_quota, "
            "admission_truncated, scope_changed, in_maintenance, query_provenance, collected_at, "
            "origin) VALUES (CAST(:watch AS uuid), CAST(:start AS timestamptz), 300, 1, "
            "7, 7, 0, 0, 'EXACT', 'EXACT', true, 1, 1, 0, 0, 0, false, false, false, "
            "CAST('{}' AS jsonb), now(), :origin)"
        )
    )
    params["origin"] = origin
    connection.execute(statement, params)


def _rejects(engine, sql: str, params: Mapping[str, object], constraint: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match=constraint), engine.begin() as connection:
        connection.execute(text(sql), params)


def test_0029_round_trip_carries_live_data_deletes_history_and_has_no_drift(
    clean_db: str,
) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic.util.exc import AutogenerateDiffsDetected

    from alembic import command

    config = _alembic_config(clean_db)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert "0029" in {item.revision for item in script.walk_revisions("base", heads[0])}
    revision = script.get_revision("0029")
    assert revision is not None and revision.down_revision == "0028"

    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0028")
        with engine.begin() as connection:
            _seed_0028(connection)

        command.upgrade(config, "head")
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            assert context.get_current_revision() == heads[0]
            old = connection.execute(
                text(
                    "SELECT history_days, history_state, history_progress, origin "
                    "FROM property_watch JOIN pw_bucket ON pw_bucket.watch_id = property_watch.id"
                )
            ).one()
            assert old == (None, "NOT_REQUESTED", {}, "LIVE")
            new_columns = {
                "history_days",
                "history_state",
                "history_target_start",
                "history_floor",
                "history_through",
                "history_end",
                "history_started_at",
                "history_finished_at",
                "history_progress",
            }
            defaults = dict(
                connection.execute(
                    text(
                        "SELECT column_name, column_default FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'property_watch' "
                        "AND column_name = ANY(:columns)"
                    ),
                    {"columns": sorted(new_columns)},
                ).all()
            )
            assert defaults == dict.fromkeys(new_columns)
            assert connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'pw_bucket' "
                    "AND column_name = 'origin'"
                )
            ).scalar_one() is None

            connection.execute(
                text(
                    "UPDATE property_watch SET last_status = 'READING_HISTORY', "
                    "history_days = 28, history_state = 'READING', history_progress = "
                    "CAST('{\"searches\": 2}' AS jsonb) WHERE id = CAST(:watch AS uuid)"
                ),
                {"watch": WATCH},
            )
            _insert_bucket(connection, HISTORY_START, origin="HISTORY")
            connection.execute(
                text(
                    "INSERT INTO pw_value_metric "
                    "(pw_value_id, bucket_start, events, bucket_seconds) "
                    "VALUES (CAST(:value AS uuid), CAST(:start AS timestamptz), 5, 300)"
                ),
                {"value": VALUE, "start": HISTORY_START},
            )

        command.downgrade(config, "0028")
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0028"
            assert connection.execute(
                text("SELECT last_status FROM property_watch WHERE id = CAST(:watch AS uuid)"),
                {"watch": WATCH},
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT bucket_start FROM pw_bucket WHERE watch_id = CAST(:watch AS uuid)"),
                {"watch": WATCH},
            ).scalar_one() == connection.execute(
                text("SELECT CAST(:start AS timestamptz)"), {"start": LIVE_START}
            ).scalar_one()
            metrics = connection.execute(
                text(
                    "SELECT bucket_start, events FROM pw_value_metric "
                    "WHERE pw_value_id = CAST(:value AS uuid)"
                ),
                {"value": VALUE},
            ).all()
            assert len(metrics) == 1 and metrics[0].events == 7

        # The old status CHECK is back: the new status is refused below 0029.
        _rejects(
            engine,
            "UPDATE property_watch SET last_status = 'READING_HISTORY'",
            {},
            "ck_property_watch_last_status_known",
        )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            carried = connection.execute(
                text(
                    "SELECT history_days, history_state, history_progress, origin "
                    "FROM property_watch JOIN pw_bucket ON pw_bucket.watch_id = property_watch.id"
                )
            ).one()
            assert carried == (None, "NOT_REQUESTED", {}, "LIVE")
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
            pytest.fail(f"schema drift detected between models and migrations:\n{exc}")
    finally:
        engine.dispose()


def test_0029_checks_accept_known_values_and_refuse_unknown_ones(clean_db: str) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "0028")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_0028(connection)
        command.upgrade(config, "head")

        with engine.begin() as connection:
            for state in PwHistoryState:
                connection.execute(
                    text("UPDATE property_watch SET history_state = :state"),
                    {"state": state.value},
                )
            for status in PwWatchStatus:
                connection.execute(
                    text("UPDATE property_watch SET last_status = :status"),
                    {"status": status.value},
                )
            for days in (None, 0, 1):
                connection.execute(
                    text("UPDATE property_watch SET history_days = :days"), {"days": days}
                )
            _insert_bucket(connection, HISTORY_START, origin=PwBucketOrigin.HISTORY.value)
            connection.execute(text("UPDATE pw_bucket SET origin = 'LIVE'"))
            connection.execute(text("UPDATE pw_bucket SET origin = 'HISTORY'"))

        _rejects(
            engine,
            "UPDATE property_watch SET history_state = 'UNKNOWN'",
            {},
            "ck_property_watch_history_state_known",
        )
        _rejects(
            engine,
            "UPDATE property_watch SET last_status = 'UNKNOWN'",
            {},
            "ck_property_watch_last_status_known",
        )
        _rejects(
            engine,
            "UPDATE property_watch SET history_days = -1",
            {},
            "ck_property_watch_history_days_range",
        )
        _rejects(
            engine,
            "UPDATE pw_bucket SET origin = 'UNKNOWN'",
            {},
            "ck_pw_bucket_origin_known",
        )
    finally:
        engine.dispose()


def test_0029_adds_origin_to_a_populated_hypertable_with_a_compressed_chunk(
    clean_db: str,
) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "0028")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_0028(connection)
            assert connection.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = 'pw_bucket'"
                )
            ).scalar_one() == 1
            version = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar_one()
            assert version
            connection.execute(
                text(
                    "ALTER TABLE pw_bucket SET (timescaledb.compress = true, "
                    "timescaledb.compress_segmentby = 'watch_id')"
                )
            )
            connection.execute(
                text(
                    "SELECT compress_chunk(chunk, if_not_compressed => true) "
                    "FROM show_chunks('pw_bucket') AS chunk"
                )
            )
            assert connection.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = 'pw_bucket' AND is_compressed"
                )
            ).scalar_one() >= 1

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT origin FROM pw_bucket WHERE watch_id = CAST(:watch AS uuid)"),
                {"watch": WATCH},
            ).scalar_one() == "LIVE"
            assert connection.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = 'pw_bucket' AND is_compressed"
                )
            ).scalar_one() >= 1

        command.downgrade(config, "0028")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'pw_bucket' AND column_name = 'origin'"
                )
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = 'pw_bucket' AND is_compressed"
                )
            ).scalar_one() >= 1

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT origin FROM pw_bucket")).scalar_one() == "LIVE"
            assert connection.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = 'pw_bucket' AND is_compressed"
                )
            ).scalar_one() >= 1
    finally:
        engine.dispose()
