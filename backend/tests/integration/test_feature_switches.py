"""The feature_switch table and its resolver against Postgres.

Gated on TEST_DATABASE_URL. The migration test resets the public schema and
drives Alembic as tests/integration/test_migrations.py does: ``Config()`` without
``alembic.ini``, so no logger configuration changes.
"""

from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.models.base import utcnow
from app.models.feature_switch import FEATURE_KEYS, FeatureSwitch
from app.services.feature_switches import (
    UNREADABLE_REASON,
    ResolvedSwitch,
    effective_settings,
    resolve,
)

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")


def _row(key: str, enabled: bool) -> FeatureSwitch:
    return FeatureSwitch(key=key, enabled=enabled, updated_at=utcnow(), updated_by="alice")


class _Session:
    """Stands in for a session whose every statement fails like a lost connection."""

    def __init__(self, error: str) -> None:
        self.error = error
        self.statements = 0

    async def scalars(self, *args: object, **kwargs: object) -> object:
        self.statements += 1
        raise RuntimeError(self.error)


async def test_the_table_wins_and_a_row_this_release_does_not_know_is_ignored(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("WAF_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("IPS_COLLECTION_ENABLED", "false")
    monkeypatch.delenv("CATCHALL_COLLECTION_ENABLED", raising=False)
    base = Settings()

    empty = await resolve(db_session, base)
    assert (empty.reason, empty.unknown_keys) == (None, ())
    assert set(empty.values) == set(FEATURE_KEYS)
    assert empty.values["waf_collection_enabled"] == ResolvedSwitch(True, "env")
    assert empty.values["catchall_collection_enabled"] == ResolvedSwitch(True, "default")
    # Both .env directions are in play below: the WAF row must turn an env true
    # off, the IPS row must turn an env false on. So a row beats .env either way,
    # not merely the code default.
    assert empty.values["ips_collection_enabled"] == ResolvedSwitch(False, "env")

    # A newer release's row survives a downgrade: drop the allow-list in this
    # test's own schema so it can exist, then check the resolver ignores it.
    await db_session.execute(
        text("ALTER TABLE feature_switch DROP CONSTRAINT ck_feature_switch_key_allowed")
    )
    db_session.add_all(
        [
            _row("waf_collection_enabled", False),
            _row("ips_collection_enabled", True),
            _row("future_switch", False),
        ]
    )
    await db_session.flush()

    resolved = await resolve(db_session, base)
    off, on = (resolved.values[key] for key in ("waf_collection_enabled", "ips_collection_enabled"))
    assert (off.enabled, off.source, off.updated_by) == (False, "db", "alice")
    assert off.updated_at is not None
    assert (on.enabled, on.source) == (True, "db")
    assert (resolved.unknown_keys, resolved.reason) == (("future_switch",), None)
    effective = effective_settings(base, resolved)
    assert (effective.waf_collection_enabled, effective.ips_collection_enabled) == (False, True)


async def test_a_failed_read_turns_every_switch_off() -> None:
    session = _Session("the connection is gone")

    resolved = await resolve(session, Settings())  # type: ignore[arg-type]

    assert resolved.reason == UNREADABLE_REASON
    assert {state.enabled for state in resolved.values.values()} == {False}
    assert {state.source for state in resolved.values.values()} == {"unreadable"}
    # CATCHALL_COLLECTION_ENABLED is on by default: a failed read must not keep it.
    assert effective_settings(Settings(), resolved).catchall_collection_enabled is False


async def test_the_kill_switch_sends_no_statement(monkeypatch) -> None:
    monkeypatch.setenv("WAF_COLLECTION_ENABLED", "true")
    monkeypatch.delenv("CATCHALL_COLLECTION_ENABLED", raising=False)
    session = _Session("the kill switch issued a statement")

    resolved = await resolve(session, Settings(feature_switches_db=False))  # type: ignore[arg-type]

    assert session.statements == 0
    assert resolved.reason is None
    assert resolved.values["waf_collection_enabled"] == ResolvedSwitch(True, "env")
    assert resolved.values["catchall_collection_enabled"] == ResolvedSwitch(True, "default")

    # The operator's path: the env var, through the constructor the process
    # uses. Settings() never touches get_settings()'s cache, so nothing leaks.
    monkeypatch.setenv("FEATURE_SWITCHES_DB", "false")
    operator = Settings()
    assert operator.feature_switches_db is False
    second = _Session("the kill switch issued a statement")
    resolved = await resolve(second, operator)  # type: ignore[arg-type]

    assert second.statements == 0
    assert resolved.reason is None
    assert {state.source for state in resolved.values.values()} <= {"env", "default"}
    assert resolved.values["waf_collection_enabled"] == ResolvedSwitch(True, "env")
    assert resolved.values["catchall_collection_enabled"] == ResolvedSwitch(True, "default")


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _table_exists(engine, name: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(text("SELECT to_regclass(:n)").bindparams(n=f"public.{name}")).scalar()
        )


def test_0072_round_trip_and_its_check_matches_the_allow_list() -> None:
    from alembic.config import Config
    from alembic.util.exc import AutogenerateDiffsDetected
    from sqlalchemy.exc import IntegrityError

    from alembic import command
    from tests.integration.schema_reset import reset_public_schema

    sync_url = _sync_url()
    reset_public_schema(sync_url)
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    engine = create_engine(sync_url, future=True)
    insert = (
        "INSERT INTO feature_switch (key, enabled, updated_at, updated_by) "
        "VALUES ('{}', true, now(), 'alice')"
    )
    try:
        command.upgrade(cfg, "0071")
        assert not _table_exists(engine, "feature_switch")

        command.upgrade(cfg, "0072")
        assert _table_exists(engine, "feature_switch")
        with engine.connect() as conn:
            definition = conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'feature_switch'::regclass "
                    "AND conname = 'ck_feature_switch_key_allowed'"
                )
            ).scalar_one()
        assert set(re.findall(r"'([a-z_]+)'", str(definition))) == set(FEATURE_KEYS)
        with engine.begin() as conn:
            conn.execute(text(insert.format("waf_collection_enabled")))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(insert.format("not_a_switch")))

        command.downgrade(cfg, "0071")
        assert not _table_exists(engine, "feature_switch")

        command.upgrade(cfg, "head")
        assert _table_exists(engine, "feature_switch")
        try:
            command.check(cfg)
        except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
            pytest.fail(f"schema drift detected between models and migrations:\n{exc}")
    finally:
        engine.dispose()
