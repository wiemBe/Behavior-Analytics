"""Migration 0027: `custom_rule.pw_value_id`, the recreated subject CHECK, RESTRICT.

The round trip upgrades to 0026, seeds a Phase F rule and a property-watch value,
upgrades, writes a `PW_VALUE` rule, downgrades (which removes only that rule),
and upgrades again. The pairing CHECK is exercised on the recreated constraint
and `alembic check` is the authority for model drift.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
INSTANCE = "11111111-1111-1111-1111-111111111111"
SOURCE = "22222222-2222-2222-2222-222222222222"
WATCH = "55555555-5555-5555-5555-555555555555"
VALUE = "66666666-6666-6666-6666-666666666666"
KEPT_RULE = "33333333-3333-3333-3333-333333333331"
PW_RULE = "33333333-3333-3333-3333-333333333332"
PW_BREACH = "44444444-4444-4444-4444-444444444441"


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


def _seed(connection, *, history: bool = True) -> None:  # type: ignore[no-untyped-def]
    """`history` is False below revision 0029, which adds two NOT NULL watch columns."""
    new_columns = ", history_state, history_progress" if history else ""
    new_values = ", 'NOT_REQUESTED', CAST('{}' AS jsonb)" if history else ""
    connection.execute(
        text(
            "INSERT INTO qradar_instance (id, name, console_host, api_version, verify_ssl, "
            "provider_kind, mcp_enabled, enabled, status, consecutive_failures) "
            "VALUES (CAST(:i AS uuid), 'lab', 'qradar.lab.local', '20.0', true, "
            "'REST', false, true, 'HEALTHY', 0)"
        ),
        {"i": INSTANCE},
    )
    connection.execute(
        text(
            "INSERT INTO log_source (id, instance_id, qradar_id, name, enabled, criticality, "
            "monitoring_enabled, maintenance_mode, business_hours_only, business_hours_start, "
            "business_hours_end, business_days, timezone_name, custom_thresholds, first_seen_at) "
            "VALUES (CAST(:s AS uuid), CAST(:i AS uuid), 17, 'edge-fw', true, "
            "'MEDIUM', true, false, false, 8, 18, CAST('[1,2,3,4,5]' AS jsonb), 'UTC', "
            "CAST('{}' AS jsonb), now())"
        ),
        {"i": INSTANCE, "s": SOURCE},
    )
    connection.execute(
        text(
            "INSERT INTO property_watch (id, instance_id, name, scope_kind, log_source_id, "  # noqa: S608 - constant column list only
            "properties, pinned_values, detectors, severity, watch_version, enabled"
            f"{new_columns}) "
            "VALUES (CAST(:w AS uuid), CAST(:i AS uuid), 'w', 'LOG_SOURCE', CAST(:s AS uuid), "
            "CAST(:p AS jsonb), CAST('[]' AS jsonb), CAST('[]' AS jsonb), 'MEDIUM', 1, false"
            f"{new_values})"
        ),
        {
            "w": WATCH,
            "i": INSTANCE,
            "s": SOURCE,
            "p": json.dumps([{"kind": "NORMALIZED", "name": "username"}]),
        },
    )
    connection.execute(
        text(
            "INSERT INTO pw_value (id, watch_id, watch_version, value_key, truncated, pinned, "
            "active) VALUES (CAST(:v AS uuid), CAST(:w AS uuid), 1, 'alice', false, true, true)"
        ),
        {"v": VALUE, "w": WATCH},
    )


_RULE_SQL = (
    "INSERT INTO custom_rule (name, subject_kind, log_source_id, source_class, "
    "waf_endpoint_id, ips_entity_id, pw_value_id, metric, operator, threshold_value, "
    "sustain_buckets, recovery_buckets, severity, enabled, consecutive_breaching, "
    "consecutive_under, id) VALUES (:name, :kind, CAST(:ls AS uuid), :cls, "
    "CAST(:waf AS uuid), CAST(:ips AS uuid), CAST(:pw AS uuid), :metric, 'GT', 5, 1, 1, "
    "'MEDIUM', false, 0, 0, gen_random_uuid())"
)


def _rule(name: str, kind: str, metric: str, **columns: str | None) -> dict[str, object]:
    params: dict[str, object] = {
        "name": name,
        "kind": kind,
        "metric": metric,
        "ls": None,
        "cls": None,
        "waf": None,
        "ips": None,
        "pw": None,
    }
    params.update(columns)
    return params


def test_0027_round_trip_carries_data_and_has_no_drift(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic.util.exc import AutogenerateDiffsDetected

    from alembic import command

    config = _alembic_config(clean_db)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert "0027" in {item.revision for item in script.walk_revisions("base", heads[0])}
    revision = script.get_revision("0027")
    assert revision is not None
    assert revision.down_revision == "0026"

    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "0026")
        with engine.begin() as connection:
            _seed(connection, history=False)
            connection.execute(
                text(
                    "INSERT INTO custom_rule (name, subject_kind, log_source_id, metric, "
                    "operator, threshold_value, sustain_buckets, recovery_buckets, severity, "
                    "enabled, consecutive_breaching, consecutive_under, id) VALUES ('kept', "
                    "'LOG_SOURCE', CAST(:ls AS uuid), 'event_count', 'GT', 5, 1, 1, 'MEDIUM', "
                    "false, 0, 0, gen_random_uuid())"
                ),
                {"ls": SOURCE},
            )
            # Before 0027 the column does not exist.
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'custom_rule' AND column_name = 'pw_value_id'"
                    )
                ).scalar_one()
                == 0
            )

        command.upgrade(config, "0027")
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            assert context.get_current_revision() == "0027"
            connection.execute(
                text(_RULE_SQL),
                _rule("pinned", "PW_VALUE", "pw_value_events", pw=VALUE),
            )
            on_delete = connection.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint "
                    "WHERE conname = 'fk_custom_rule_pw_value_id_pw_value'"
                )
            ).scalar_one()
            assert on_delete == "r"  # RESTRICT
            assert connection.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_custom_rule_pw_value_id'")
            ).scalar_one() == 1

        command.downgrade(config, "0026")
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            assert context.get_current_revision() == "0026"
            names = set(connection.execute(text("SELECT name FROM custom_rule")).scalars())
            assert names == {"kept"}  # the PW_VALUE rule went, Phase F data stayed
            assert connection.execute(
                text("SELECT count(*) FROM pw_value WHERE id = CAST(:v AS uuid)"),
                {"v": VALUE},
            ).scalar_one() == 1

        command.upgrade(config, "head")
        with engine.connect() as connection:
            names = set(connection.execute(text("SELECT name FROM custom_rule")).scalars())
            assert names == {"kept"}
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
            pytest.fail(f"schema drift detected between models and migrations:\n{exc}")
    finally:
        engine.dispose()


def test_0027_downgrade_removes_pw_rule_breaches_with_the_rule(clean_db: str) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "head")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed(connection)
            connection.execute(
                text(_RULE_SQL), _rule("pinned", "PW_VALUE", "pw_value_events", pw=VALUE)
            )
            rule_id = connection.execute(text("SELECT id FROM custom_rule")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO custom_rule_breach (rule_id, state, first_bucket_start, "
                    "last_bucket_start, opened_at, worst_value, worst_measurement, last_value, "
                    "last_measurement, unmeasured_buckets, subject_label, details, id) "
                    "VALUES (:r, 'OPEN', now(), now(), now(), 9, 'EXACT', 9, 'EXACT', 0, "
                    "'alice', CAST('{}' AS jsonb), CAST(:b AS uuid))"
                ),
                {"r": rule_id, "b": PW_BREACH},
            )
        command.downgrade(config, "0026")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM custom_rule")).scalar_one() == 0
            assert (
                connection.execute(text("SELECT count(*) FROM custom_rule_breach")).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("case", "params", "ok"),
    [
        ("pw-value-pairs", _rule("c1", "PW_VALUE", "pw_value_events", pw=VALUE), True),
        ("pw-value-no-column", _rule("c2", "PW_VALUE", "pw_value_events"), False),
        (
            "pw-value-and-log-source",
            _rule("c3", "PW_VALUE", "pw_value_events", pw=VALUE, ls=SOURCE),
            False,
        ),
        (
            "log-source-with-pw-value",
            _rule("c4", "LOG_SOURCE", "event_count", ls=SOURCE, pw=VALUE),
            False,
        ),
        ("fleet-with-pw-value", _rule("c5", "FLEET_CLASS", "eps", cls="LINUX", pw=VALUE), False),
        ("log-source-pairs", _rule("c6", "LOG_SOURCE", "event_count", ls=SOURCE), True),
        ("log-source-no-column", _rule("c7", "LOG_SOURCE", "event_count"), False),
        ("unknown-kind", _rule("c8", "NOT_A_KIND", "events", pw=VALUE), False),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_recreated_subject_check_accepts_and_refuses_the_right_pairings(
    clean_db: str, case: str, params: dict[str, object], ok: bool
) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "head")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed(connection)
        if ok:
            with engine.begin() as connection:
                connection.execute(text(_RULE_SQL), params)
        else:
            with (
                pytest.raises(IntegrityError, match="ck_custom_rule_subject_matches_kind"),
                engine.begin() as connection,
            ):
                connection.execute(text(_RULE_SQL), params)
    finally:
        engine.dispose()


def test_on_delete_restrict_holds_at_the_database(clean_db: str) -> None:
    from alembic import command

    config = _alembic_config(clean_db)
    command.upgrade(config, "head")
    engine = create_engine(clean_db, future=True)
    try:
        with engine.begin() as connection:
            _seed(connection)
            connection.execute(
                text(_RULE_SQL), _rule("pinned", "PW_VALUE", "pw_value_events", pw=VALUE)
            )
        with (
            pytest.raises(IntegrityError, match="fk_custom_rule_pw_value_id_pw_value"),
            engine.begin() as connection,
        ):
            connection.execute(
                text("DELETE FROM pw_value WHERE id = CAST(:v AS uuid)"), {"v": VALUE}
            )
        with (
            pytest.raises(IntegrityError, match="fk_custom_rule_pw_value_id_pw_value"),
            engine.begin() as connection,
        ):
            # The watch delete cascades to pw_value, which the rule restricts.
            connection.execute(
                text("DELETE FROM property_watch WHERE id = CAST(:w AS uuid)"), {"w": WATCH}
            )
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM pw_value")).scalar_one() == 1
    finally:
        engine.dispose()
