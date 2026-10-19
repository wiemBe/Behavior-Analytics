"""Migration 0030: history-backfill tables, the `origin` marker and a clean round trip."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import MetaData, Table, create_engine, insert, text
from sqlalchemy.exc import IntegrityError

from app.history.origin import PHASE_A_HISTORY_SOURCES, HistoryOrigin
from app.history.state import SCOPE_TABLE_FAMILIES, HistoryFamily, HistoryState
from app.models.enums import HistoryDecisionAnswer, HistoryDecisionSource
from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOURCE = uuid.UUID("22222222-2222-2222-2222-222222222222")
CATCHALL = uuid.UUID("22222222-2222-2222-2222-222222222223")
RULE = uuid.UUID("44444444-4444-4444-4444-444444444444")
BASE = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
LIVE_AT = BASE
HISTORY_AT = BASE - timedelta(minutes=5)
FOUR_TABLES = ("waf_source_bucket", "ips_source_bucket", "catchall_bucket", "rule_match_window")
HYPERTABLES = ("waf_source_bucket", "ips_source_bucket", "catchall_bucket")


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _alembic_config(sync_url: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    # Deliberately no alembic.ini: fileConfig() would disable app.* loggers for every
    # integration test that follows this one in the same process.
    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


@pytest.fixture
def clean_db() -> str:
    sync_url = _sync_url()
    reset_public_schema(sync_url)
    return sync_url


# --------------------------------------------------------------------------- seeding
_FILL = {uuid.UUID: uuid.uuid4, str: lambda: "x", datetime: lambda: BASE, int: lambda: 1,
         bool: lambda: False, float: lambda: 0.0, dict: dict}


def _row(connection, table: str, **values: object) -> None:  # type: ignore[no-untyped-def]
    """Insert one row by reflection, filling NOT NULL columns without a default."""
    reflected = Table(table, MetaData(), autoload_with=connection)
    row = dict(values)
    for column in reflected.columns:
        if column.name in row or column.nullable or column.server_default is not None:
            continue
        row[column.name] = _FILL[column.type.python_type]()
    connection.execute(insert(reflected).values(**row))


def _seed_sources(connection) -> None:  # type: ignore[no-untyped-def]
    connection.execute(
        text(
            "INSERT INTO qradar_instance (id, name, console_host, api_version, verify_ssl, "
            "provider_kind, mcp_enabled, enabled, status, consecutive_failures) "
            "VALUES (:instance, 'lab', 'qradar.lab.local', '20.0', true, "
            "'REST', false, true, 'HEALTHY', 0)"
        ),
        {"instance": INSTANCE},
    )
    for source, qradar_id in ((SOURCE, 17), (CATCHALL, 18)):
        connection.execute(
            text(
                "INSERT INTO log_source (id, instance_id, qradar_id, name, enabled, "
                "criticality, monitoring_enabled, maintenance_mode, business_hours_only, "
                "business_hours_start, business_hours_end, business_days, timezone_name, "
                "custom_thresholds, first_seen_at) VALUES (:source, :instance, :qid, "
                "'src', true, 'MEDIUM', true, false, false, 8, 18, "
                "CAST('[1,2,3,4,5]' AS jsonb), 'UTC', CAST('{}' AS jsonb), now())"
            ),
            {"source": source, "instance": INSTANCE, "qid": qradar_id},
        )
    _row(connection, "analytics_rule", id=RULE, instance_id=INSTANCE, qradar_id=5, name="rule")


def _seed_bucket_family(connection, at: datetime, **marker: str) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    """One bucket or window in each of the four tables, and one child row on each."""
    ids = {"waf": uuid.uuid4(), "ips": uuid.uuid4()}
    _row(connection, "waf_source_bucket", log_source_id=SOURCE, bucket_start=at, bucket_seconds=300,
         completeness="COMPLETE", request_count_measurement="EXACT",
         action_measurement="NOT_COLLECTED", response_code_measurement="NOT_COLLECTED",
         client_measurement="NOT_COLLECTED", **marker)
    _row(connection, "ips_source_bucket", log_source_id=SOURCE, bucket_start=at, bucket_seconds=300,
         destination_port_measurement="EXACT", destination_ip_measurement="EXACT",
         signature_measurement="EXACT", source_ip_measurement="EXACT",
         port_fanout_measurement="EXACT", action_measurement="EXACT", **marker)
    _row(connection, "catchall_bucket", catchall_log_source_id=CATCHALL, bucket_start=at,
         bucket_seconds=300, measurement="EXACT", **marker)
    _row(connection, "rule_match_window", instance_id=INSTANCE, window_start=at,
         window_seconds=300, status="COMPLETE", **marker)
    _row(connection, "waf_endpoint", id=ids["waf"], instance_id=INSTANCE, log_source_id=SOURCE,
         normalized_endpoint=f"/{ids['waf']}", http_method="GET")
    _row(connection, "waf_endpoint_metric", waf_endpoint_id=ids["waf"], bucket_start=at,
         bucket_seconds=300, request_count=3)
    _row(connection, "ips_entity", id=ids["ips"], instance_id=INSTANCE, log_source_id=SOURCE,
         dimension="SIGNATURE", value_key=f"sig-{ids['ips']}")
    _row(connection, "ips_entity_metric", ips_entity_id=ids["ips"], bucket_start=at, events=3,
         bucket_seconds=300)
    _row(connection, "rule_match_metric", rule_id=RULE, bucket_start=at, bucket_seconds=300,
         match_records=1, matched_events=1)
    return ids


def _count(connection, table: str, where: str = "true") -> int:  # type: ignore[no-untyped-def]
    return int(connection.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one())  # noqa: S608


CHILDREN = ("waf_endpoint_metric", "ips_entity_metric", "rule_match_metric")


def _history_phase_a_rows(connection) -> None:  # type: ignore[no-untyped-def]
    for offset, source in enumerate(("ariel_aggregate", None, *PHASE_A_HISTORY_SOURCES)):
        _row(connection, "log_source_metric", log_source_id=SOURCE,
             bucket_start=BASE - timedelta(hours=1 + offset), collection_source=source)


def _migration_head_check(config) -> None:  # type: ignore[no-untyped-def]
    from alembic.util.exc import AutogenerateDiffsDetected

    from alembic import command

    try:
        command.check(config)
    except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
        pytest.fail(f"schema drift detected between models and migrations:\n{exc}")


# --------------------------------------------------------------------------- tests
def test_0030_round_trip_carries_live_rows_and_downgrade_deletes_history(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    revision = script.get_revision("0030")
    assert revision is not None and revision.down_revision == "0029"

    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0029")
        with engine.begin() as connection:
            _seed_sources(connection)
            for table in FOUR_TABLES:
                assert "origin" not in {
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": table},
                    )
                }
            _seed_bucket_family(connection, LIVE_AT)
            _history_phase_a_rows(connection)

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0030"
            for table in FOUR_TABLES:
                assert connection.execute(text(f"SELECT origin FROM {table}")).scalars().all() == [  # noqa: S608
                    "LIVE"
                ], table
                # No server default is left: a writer must say which.
                assert connection.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns WHERE "
                        "table_schema = 'public' AND table_name = :t AND column_name = 'origin'"
                    ),
                    {"t": table},
                ).scalar_one() is None
            # Phase A's table is untouched (repository rule 6).
            assert "origin" not in {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'log_source_metric'"
                    )
                )
            }

            _row(connection, "history_backfill_decision", instance_id=INSTANCE, answer="YES",
                 days=28, decided_at=BASE, decided_by="installer", source="INSTALLER")
            _row(connection, "history_backfill_scope", instance_id=INSTANCE, family="WAF",
                 scope_key="17", state="READING", progress={})
            _row(connection, "history_backfill_scope", instance_id=INSTANCE, family="PHASE_A",
                 scope_key="", state="WAITING", progress={})
            _seed_bucket_family(connection, HISTORY_AT, origin=HistoryOrigin.HISTORY.value)
            # an `ariel_history` and a `mock_history` row already exist from the seeding
            assert _count(connection, "log_source_metric",
                          "collection_source IN ('ariel_history', 'mock_history')") == 2

        command.downgrade(config, "-1")
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0029"
            for table in ("history_backfill_decision", "history_backfill_scope"):
                assert connection.execute(
                    text("SELECT to_regclass(:n)"), {"n": f"public.{table}"}
                ).scalar() is None
            for table in FOUR_TABLES:
                assert "origin" not in {
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": table},
                    )
                }, table
                time_column = "window_start" if table == "rule_match_window" else "bucket_start"
                starts = connection.execute(
                    text(f"SELECT {time_column} FROM {table}")  # noqa: S608
                ).scalars().all()
                assert starts == [LIVE_AT], table
            for table in CHILDREN:
                assert connection.execute(
                    text(f"SELECT bucket_start FROM {table}")  # noqa: S608
                ).scalars().all() == [LIVE_AT], table
            # The history children of the deleted buckets are gone, the parents kept.
            assert _count(connection, "waf_endpoint") == 2
            assert _count(connection, "ips_entity") == 2
            assert _count(connection, "analytics_rule") == 1
            assert connection.execute(
                text("SELECT collection_source FROM log_source_metric ORDER BY bucket_start")
            ).scalars().all() == [None, "ariel_aggregate"]

        command.upgrade(config, "head")
        with engine.connect() as connection:
            for table in FOUR_TABLES:
                assert connection.execute(text(f"SELECT origin FROM {table}")).scalars().all() == [  # noqa: S608
                    "LIVE"
                ], table
            assert _count(connection, "history_backfill_decision") == 0
            assert _count(connection, "history_backfill_scope") == 0
        _migration_head_check(config)
    finally:
        engine.dispose()


def _expect_refused(engine, table: str, constraint: str, **values: object) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(IntegrityError, match=constraint), engine.begin() as connection:
        _row(connection, table, **values)


def test_0030_checks_accept_known_values_and_refuse_unknown_ones(clean_db: str) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "head")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_sources(connection)

        decision = {"decided_at": BASE, "decided_by": "installer"}
        # decision: YES carries days >= 7, NO carries none; both sources
        with engine.begin() as connection:
            _row(connection, "history_backfill_decision", instance_id=INSTANCE,
                 answer=HistoryDecisionAnswer.YES.value, days=7,
                 source=HistoryDecisionSource.INSTALLER.value, **decision)
            connection.execute(text("DELETE FROM history_backfill_decision"))
            _row(connection, "history_backfill_decision", instance_id=INSTANCE,
                 answer=HistoryDecisionAnswer.NO.value, days=None,
                 source=HistoryDecisionSource.UI.value, **decision)
            connection.execute(text("DELETE FROM history_backfill_decision"))
        for label, values, constraint in (
            ("answer", {"answer": "MAY", "days": None, "source": "UI"},
             "ck_history_backfill_decision_answer_known"),
            ("source", {"answer": "NO", "days": None, "source": "CLI"},
             "ck_history_backfill_decision_source_known"),
            ("days on NO", {"answer": "NO", "days": 28, "source": "UI"},
             "ck_history_backfill_decision_days_iff_yes"),
            ("YES without days", {"answer": "YES", "days": None, "source": "UI"},
             "ck_history_backfill_decision_days_iff_yes"),
            ("days below 7", {"answer": "YES", "days": 6, "source": "UI"},
             "ck_history_backfill_decision_days_minimum"),
        ):
            _expect_refused(engine, "history_backfill_decision", constraint,
                            instance_id=INSTANCE, **values, **decision)
            del label

        # scope: every family and every stored state; the key shape follows the family
        with engine.begin() as connection:
            for family in SCOPE_TABLE_FAMILIES:
                key = "17" if family in (HistoryFamily.WAF, HistoryFamily.IPS) else ""
                for state in HistoryState:
                    if state is HistoryState.NOT_REQUESTED:
                        continue
                    _row(connection, "history_backfill_scope", instance_id=INSTANCE,
                         family=family.value, scope_key=key, state=state.value, progress={})
                    connection.execute(text("DELETE FROM history_backfill_scope"))
        scope = {"instance_id": INSTANCE, "progress": {}}
        _expect_refused(engine, "history_backfill_scope", "ck_history_backfill_scope_family_known",
                        family="PROPERTY_WATCH", scope_key="", state="READING", **scope)
        _expect_refused(engine, "history_backfill_scope", "ck_history_backfill_scope_family_known",
                        family="GEO", scope_key="", state="READING", **scope)
        _expect_refused(engine, "history_backfill_scope", "ck_history_backfill_scope_state_known",
                        family="PHASE_A", scope_key="", state="NOT_REQUESTED", **scope)
        _expect_refused(engine, "history_backfill_scope", "ck_history_backfill_scope_state_known",
                        family="PHASE_A", scope_key="", state="DONE", **scope)
        for family, key in (("WAF", ""), ("IPS", ""), ("PHASE_A", "17"), ("CATCHALL", "17"),
                            ("RULE_MATCH", "17")):
            _expect_refused(engine, "history_backfill_scope",
                            "ck_history_backfill_scope_scope_key_shape",
                            family=family, scope_key=key, state="READING", **scope)

        # the scope key is part of the primary key; a duplicate is refused
        with engine.begin() as connection:
            _row(connection, "history_backfill_scope", family="WAF", scope_key="17",
                 state="READING", **scope)
            _row(connection, "history_backfill_scope", family="WAF", scope_key="18",
                 state="READING", **scope)
        _expect_refused(engine, "history_backfill_scope", "pk_history_backfill_scope",
                        family="WAF", scope_key="17", state="READING", **scope)

        # origin on the four tables
        with engine.begin() as connection:
            for origin in HistoryOrigin:
                at = BASE - timedelta(days=1, minutes=len(origin.value))
                _seed_bucket_family(connection, at, origin=origin.value)
        for table, keys in (
            ("waf_source_bucket", {"log_source_id": SOURCE, "bucket_start": BASE,
                                   "completeness": "COMPLETE",
                                   "request_count_measurement": "EXACT",
                                   "action_measurement": "NOT_COLLECTED",
                                   "response_code_measurement": "NOT_COLLECTED",
                                   "client_measurement": "NOT_COLLECTED"}),
            ("ips_source_bucket", {"log_source_id": SOURCE, "bucket_start": BASE,
                                   "destination_port_measurement": "EXACT",
                                   "destination_ip_measurement": "EXACT",
                                   "signature_measurement": "EXACT",
                                   "source_ip_measurement": "EXACT",
                                   "port_fanout_measurement": "EXACT",
                                   "action_measurement": "EXACT"}),
            ("catchall_bucket", {"catchall_log_source_id": CATCHALL, "bucket_start": BASE,
                                 "measurement": "EXACT"}),
            ("rule_match_window", {"instance_id": INSTANCE, "window_start": BASE,
                                   "status": "COMPLETE"}),
        ):
            _expect_refused(engine, table, f"ck_{table}_origin_known", origin="OLD", **keys)
            _expect_refused(engine, table, f"ck_{table}_origin_known", origin="live", **keys)
    finally:
        engine.dispose()


def test_0030_a_writer_that_omits_origin_is_refused(clean_db: str) -> None:
    """No default is left, so a writer that forgot the marker fails loudly."""
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "head")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_sources(connection)
        # bypass `_row`'s filler for origin by naming every other column
        with pytest.raises(IntegrityError, match="origin"), engine.begin() as connection:
            _row_without_origin(connection)
    finally:
        engine.dispose()


def _row_without_origin(connection) -> None:  # type: ignore[no-untyped-def]
    reflected = Table("rule_match_window", MetaData(), autoload_with=connection)
    values = {
        "instance_id": INSTANCE, "window_start": BASE, "window_seconds": 300,
        "status": "COMPLETE", "rows_returned": 0, "row_cap": 10, "unknown_ids": 0,
        "query_version": "v", "collected_at": BASE, "attempts": 1,
    }
    assert "origin" in reflected.c
    connection.execute(insert(reflected).values(**values))


@pytest.mark.parametrize("table", HYPERTABLES)
def test_0030_adds_origin_to_a_populated_hypertable_with_a_compressed_chunk(
    clean_db: str, table: str
) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "0029")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_sources(connection)
            _seed_bucket_family(connection, LIVE_AT)
            assert connection.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = :t"
                ),
                {"t": table},
            ).scalar_one() == 1
            version = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar_one()
            assert version  # reported in HB-2.report.md
            segment = "catchall_log_source_id" if table == "catchall_bucket" else "log_source_id"
            connection.execute(
                text(
                    f"ALTER TABLE {table} SET (timescaledb.compress = true, "
                    f"timescaledb.compress_segmentby = '{segment}')"
                )
            )
            connection.execute(
                text(
                    f"SELECT compress_chunk(chunk, if_not_compressed => true) "  # noqa: S608
                    f"FROM show_chunks('{table}') AS chunk"
                )
            )
            assert _compressed_chunks(connection, table) >= 1

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(text(f"SELECT origin FROM {table}")).scalar_one() == "LIVE"  # noqa: S608
            assert _compressed_chunks(connection, table) >= 1
            assert connection.execute(
                text(
                    "SELECT compression_enabled FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = :t"
                ),
                {"t": table},
            ).scalar_one() is True
            # a history row lands in the same (compressed) chunk's range
            history = HISTORY_AT
            if table == "waf_source_bucket":
                _row(connection, table, log_source_id=SOURCE, bucket_start=history,
                     bucket_seconds=300, completeness="COMPLETE",
                     request_count_measurement="EXACT", action_measurement="NOT_COLLECTED",
                     response_code_measurement="NOT_COLLECTED",
                     client_measurement="NOT_COLLECTED", origin="HISTORY")
            elif table == "ips_source_bucket":
                _row(connection, table, log_source_id=SOURCE, bucket_start=history,
                     bucket_seconds=300, destination_port_measurement="EXACT",
                     destination_ip_measurement="EXACT", signature_measurement="EXACT",
                     source_ip_measurement="EXACT", port_fanout_measurement="EXACT",
                     action_measurement="EXACT", origin="HISTORY")
            else:
                _row(connection, table, catchall_log_source_id=CATCHALL, bucket_start=history,
                     bucket_seconds=300, measurement="EXACT", origin="HISTORY")

        command.downgrade(config, "-1")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'origin'"
                ),
                {"t": table},
            ).scalar_one() == 0
            assert _count(connection, table) == 1  # the history row was deleted
            assert _compressed_chunks(connection, table) >= 1

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT origin FROM {table}")).scalar_one() == "LIVE"  # noqa: S608
            assert _compressed_chunks(connection, table) >= 1
    finally:
        engine.dispose()


def _compressed_chunks(connection, table: str) -> int:  # type: ignore[no-untyped-def]
    return int(
        connection.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = :t AND is_compressed"
            ),
            {"t": table},
        ).scalar_one()
    )


def test_0030_origin_is_added_to_a_populated_uncompressed_hypertable(clean_db: str) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "0029")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_sources(connection)
            for offset in range(3):
                _seed_bucket_family(connection, BASE - timedelta(days=offset))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            for table in FOUR_TABLES:
                assert _count(connection, table, "origin = 'LIVE'") == 3, table
                assert _count(connection, table) == 3
        _migration_head_check(config)
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- HB-2b requests
REQUEST_COLUMNS = ("start_requested_at", "start_requested_by", "stop_requested_at",
                   "stop_requested_by")


def test_0030_decision_requests_columns_checks_and_round_trip(clean_db: str) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "head")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed_sources(connection)
            columns = {
                row[0]: row[1:]
                for row in connection.execute(
                    text(
                        "SELECT column_name, is_nullable, column_default, data_type, "
                        "character_maximum_length FROM information_schema.columns "
                        "WHERE table_name = 'history_backfill_decision'"
                    )
                )
            }
            for name in REQUEST_COLUMNS:
                nullable, default, data_type, length = columns[name]
                assert nullable == "YES" and default is None, name  # no server default
                if name.endswith("_at"):
                    assert data_type == "timestamp with time zone", name
                else:
                    assert (data_type, length) == ("character varying", 255), name

        base = {"instance_id": INSTANCE, "decided_at": BASE, "decided_by": "installer",
                "source": "INSTALLER"}
        yes = {**base, "answer": "YES", "days": 28}
        # each pair is set together or not at all
        for values, constraint in (
            ({"start_requested_at": BASE}, "start_request_pair"),
            ({"start_requested_by": "a"}, "start_request_pair"),
            ({"stop_requested_at": BASE}, "stop_request_pair"),
            ({"stop_requested_by": "a"}, "stop_request_pair"),
        ):
            _expect_refused(engine, "history_backfill_decision",
                            f"ck_history_backfill_decision_{constraint}", **yes, **values)
        # a request exists only on a YES
        for values in (
            {"start_requested_at": BASE, "start_requested_by": "a"},
            {"stop_requested_at": BASE, "stop_requested_by": "a"},
        ):
            _expect_refused(engine, "history_backfill_decision",
                            "ck_history_backfill_decision_request_only_on_yes",
                            **{**base, "answer": "NO", "days": None}, **values)
        # a NO without requests is accepted; a YES may hold both requests
        with engine.begin() as connection:
            _row(connection, "history_backfill_decision", **{**base, "answer": "NO", "days": None})
            connection.execute(text("DELETE FROM history_backfill_decision"))
            _row(connection, "history_backfill_decision", **yes,
                 start_requested_at=BASE, start_requested_by="ayse",
                 stop_requested_at=BASE + timedelta(minutes=1), stop_requested_by="can")
            assert connection.execute(
                text("SELECT start_requested_by, stop_requested_by FROM history_backfill_decision")
            ).one() == ("ayse", "can")

        command.downgrade(config, "-1")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT to_regclass('public.history_backfill_decision')")
            ).scalar() is None
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _count(connection, "history_backfill_decision") == 0
        _migration_head_check(config)
    finally:
        engine.dispose()
