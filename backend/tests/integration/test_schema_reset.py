"""Guard tests for `reset_public_schema` (docs/tasks/T-083b.md).

The deadlock the helper avoids is intermittent, so these pin the guard directly: other
sessions on the test database are terminated, sessions on any other database are not.
"""

from __future__ import annotations

import contextlib
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _pids_present(sync_url: str, pids: list[int]) -> set[int]:
    """PIDs from `pids` still listed in pg_stat_activity (read from a fresh session)."""
    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT pid FROM pg_stat_activity WHERE pid = ANY(:pids)"),
                {"pids": pids},
            ).scalars()
            return set(rows)
    finally:
        engine.dispose()


def test_reset_terminates_sessions_on_this_database_only() -> None:
    sync_url = _sync_url()
    other_url = make_url(sync_url).set(database="postgres").render_as_string(hide_password=False)

    victim_engine = create_engine(sync_url, future=True)
    bystander_engine = create_engine(other_url, future=True)
    victim = victim_engine.connect()
    bystander = bystander_engine.connect()
    try:
        victim_pid = victim.execute(text("SELECT pg_backend_pid()")).scalar_one()
        bystander_pid = bystander.execute(text("SELECT pg_backend_pid()")).scalar_one()
        victim.commit()
        bystander.commit()
        assert {victim_pid, bystander_pid} == _pids_present(
            sync_url, [victim_pid, bystander_pid]
        )

        reset_public_schema(sync_url)

        # The victim is gone, and the wait inside the helper means it is gone already.
        assert _pids_present(sync_url, [victim_pid]) == set()
        # The session on the other database was never signalled.
        assert _pids_present(sync_url, [bystander_pid]) == {bystander_pid}
        assert bystander.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        for conn, engine in ((victim, victim_engine), (bystander, bystander_engine)):
            with contextlib.suppress(Exception):  # the victim was terminated server side
                conn.close()
            engine.dispose()

    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM pg_namespace WHERE nspname = 'public'")
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                    )
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
