"""`python -m app.cli.history_backfill decide`: every exit path of the operator's command."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cli import history_backfill as cli
from app.core.config import Settings
from app.models import Base

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="
NAME = "lab-console"


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value


@pytest.fixture
def database() -> Iterator[str]:
    """A schema built from the models, with one registered console."""
    url = _database_url()
    engine = create_engine(url.replace("+asyncpg", "+psycopg"), future=True)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO qradar_instance (id, name, console_host, api_version, "
                    "verify_ssl, provider_kind, mcp_enabled, enabled, status, "
                    "consecutive_failures) VALUES (:id, :name, 'qradar.lab.local', '20.0', "
                    "true, 'REST', false, true, 'HEALTHY', 0)"
                ),
                {"id": uuid.uuid4(), "name": NAME},
            )
        yield url
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def operator(database: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the command's session factory at the test database (no pooled loop state)."""

    def maker() -> async_sessionmaker:  # type: ignore[type-arg]
        engine = create_async_engine(database, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(cli, "get_sessionmaker", maker)
    return database


def _rows(database: str, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    engine = create_engine(database.replace("+asyncpg", "+psycopg"), future=True)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql))]
    finally:
        engine.dispose()


DECISIONS = "SELECT answer, days, source, decided_by FROM history_backfill_decision"
AUDITS = (
    "SELECT action, actor, object_type, object_id, detail FROM audit_log "
    "WHERE action = 'history_backfill.decide'"
)


def _decide(answer: str, instance: str = NAME) -> int:
    return cli.main(
        ["decide", "--instance", instance, "--answer", answer, "--source", "installer"]
    )


def test_yes_inserts_with_the_configured_days_and_one_audit_row(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _decide("yes") == 0
    assert _rows(operator, DECISIONS) == [("YES", 28, "INSTALLER", "installer")]
    audits = _rows(operator, AUDITS)
    assert len(audits) == 1
    action, actor, object_type, object_id, detail = audits[0]
    assert (action, actor, object_type) == ("history_backfill.decide", "installer",
                                            "qradar_instance")
    instance_id = _rows(operator, "SELECT id FROM qradar_instance")[0][0]
    assert object_id == str(instance_id)
    assert detail == {"answer": "YES", "days": 28, "source": "INSTALLER"}
    assert "recorded YES (28 days)" in capsys.readouterr().out


def test_no_inserts_without_days_and_one_audit_row(operator: str) -> None:
    assert _decide("no") == 0
    assert _rows(operator, DECISIONS) == [("NO", None, "INSTALLER", "installer")]
    audits = _rows(operator, AUDITS)
    assert len(audits) == 1
    assert audits[0][4] == {"answer": "NO", "days": None, "source": "INSTALLER"}


def test_the_days_follow_the_setting_and_the_switch_does_not_matter(
    operator: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, encryption_key=KEY, history_backfill_enabled=False,
        history_backfill_days=14,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert _decide("yes") == 0
    assert _rows(operator, DECISIONS) == [("YES", 14, "INSTALLER", "installer")]


def test_a_second_run_with_the_other_answer_changes_nothing(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _decide("yes") == 0
    capsys.readouterr()
    before = _rows(operator, "SELECT * FROM history_backfill_decision")
    assert _decide("no") == 0
    out = capsys.readouterr().out
    assert _rows(operator, "SELECT * FROM history_backfill_decision") == before
    assert len(_rows(operator, AUDITS)) == 1
    today = _rows(operator, "SELECT to_char(decided_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') "
                            "FROM history_backfill_decision")[0][0]
    assert f"already decided on {today}: YES" in out


def test_a_missing_console_exits_non_zero_and_writes_nothing(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _decide("yes", instance="no-such-console") == 2
    err = capsys.readouterr().err
    assert "no-such-console" in err
    assert _rows(operator, DECISIONS) == []
    assert _rows(operator, AUDITS) == []


def test_a_row_inserted_between_the_read_and_the_write_is_not_overwritten(
    operator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reads: list[str] = []
    real = cli._existing_decision

    async def racing_read(session, instance_id):  # type: ignore[no-untyped-def]
        """The first read sees nothing; a concurrent run then wins the insert."""
        reads.append("read")
        if len(reads) == 1:
            engine = create_engine(operator.replace("+asyncpg", "+psycopg"), future=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO history_backfill_decision (instance_id, answer, days, "
                            "decided_at, decided_by, source) VALUES (:i, 'NO', NULL, now(), "
                            "'someone', 'UI')"
                        ),
                        {"i": instance_id},
                    )
            finally:
                engine.dispose()
            return None
        return await real(session, instance_id)

    monkeypatch.setattr(cli, "_existing_decision", racing_read)
    assert _decide("yes") == 0
    assert reads == ["read", "read"]  # the conflict was re-read, not overwritten
    assert _rows(operator, DECISIONS) == [("NO", None, "UI", "someone")]
    assert _rows(operator, AUDITS) == []
    assert "already decided on" in capsys.readouterr().out


def test_a_database_failure_exits_one_and_names_no_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> object:
        raise OperationalError("SELECT 1", {}, Exception("password=hunter2"))

    monkeypatch.setattr(cli, "get_sessionmaker", broken)
    assert _decide("yes") == 1
    err = capsys.readouterr().err
    assert "not recorded" in err and "hunter2" not in err


def _run(database: str, *args: str) -> subprocess.CompletedProcess[str]:
    parsed = make_url(database)
    env = dict(os.environ)
    env.update(
        POSTGRES_USER=parsed.username or "",
        POSTGRES_PASSWORD=parsed.password or "",
        POSTGRES_DB=parsed.database or "",
        POSTGRES_HOST=parsed.host or "localhost",
        POSTGRES_PORT=str(parsed.port or 5432),
        PYTHONPATH=str(BACKEND),
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.history_backfill", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120, check=False,
    )


def test_the_module_entry_point_on_its_exit_paths(database: str) -> None:
    ok = _run(database, "decide", "--instance", NAME, "--answer", "yes", "--source", "installer")
    assert ok.returncode == 0, ok.stderr
    assert _rows(database, DECISIONS) == [("YES", 28, "INSTALLER", "installer")]
    again = _run(database, "decide", "--instance", NAME, "--answer", "no", "--source", "installer")
    assert again.returncode == 0 and "already decided on" in again.stdout
    assert len(_rows(database, AUDITS)) == 1
    missing = _run(database, "decide", "--instance", "ghost", "--answer", "no",
                   "--source", "installer")
    assert missing.returncode == 2 and "ghost" in missing.stderr
    ui = _run(database, "decide", "--instance", NAME, "--answer", "no", "--source", "ui")
    assert ui.returncode == 2 and "invalid choice" in ui.stderr
    helped = _run(database, "decide", "--help")
    assert helped.returncode == 0 and "Exit codes:" in helped.stdout
