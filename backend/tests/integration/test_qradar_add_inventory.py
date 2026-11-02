"""`python -m app.cli.qradar add` queues the first inventory after the row commits.

The operator's command, not the registry helper. A second connection reads the
row from inside the fake broker send, which is how "after commit" is checked.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cli import qradar as cli
from app.models import Base
from app.models.instance import QRadarInstance
from app.workers import tasks

pytestmark = pytest.mark.integration

QUEUED = "first inventory queued"
NOT_QUEUED = "first inventory was not queued; the hourly beat will catch up"


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value


def _rows(database: str, sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_engine(database.replace("+asyncpg", "+psycopg"), future=True)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql), params)]
    finally:
        engine.dispose()


@pytest.fixture
def database() -> Iterator[str]:
    url = _database_url()
    engine = create_engine(url.replace("+asyncpg", "+psycopg"), future=True)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        yield url
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def sessions(database: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point both the command and the worker loop at this database."""

    def maker() -> async_sessionmaker:  # type: ignore[type-arg]
        engine = create_async_engine(database, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(cli, "get_sessionmaker", maker)
    monkeypatch.setattr(tasks, "get_sessionmaker", maker)
    return database


@dataclass
class QueueLog:
    calls: list[dict[str, object]] = field(default_factory=list)
    others: list[str] = field(default_factory=list)


@pytest.fixture
def queued(sessions: str, monkeypatch: pytest.MonkeyPatch) -> QueueLog:
    log = QueueLog()

    def record(*args: object, **kwargs: object) -> None:
        sent = kwargs.get("args")
        if sent is None and args:
            sent = args[0]
        visible = _rows(
            sessions,
            "SELECT id::text, enabled FROM qradar_instance WHERE name = 'lab'",
        )
        copied = [str(item) for item in sent] if isinstance(sent, list | tuple) else sent
        log.calls.append({"args": copied, "visible": visible})

    def forbid(task_name: str):
        def _apply(*_args: object, **_kwargs: object) -> None:
            log.others.append(task_name)

        return _apply

    monkeypatch.setattr(tasks.sync_instance_inventories_task, "apply_async", record)
    monkeypatch.setattr(tasks.sync_log_sources_task, "apply_async", forbid("sync_log_sources"))
    monkeypatch.setattr(
        tasks.sync_rule_inventory_task, "apply_async", forbid("sync_rule_inventory")
    )
    return log


def _add(
    *extra: str,
    name: str = "lab",
    url: str = "https://console.example",
    verify: bool = False,
) -> int:
    argv = ["add", "--name", name, "--url", url, *extra]
    if not verify:
        argv.append("--no-verify")
    return cli.main(argv)


def _insert(database: str, name: str) -> str:
    instance_id = uuid.uuid4()
    engine = create_engine(database.replace("+asyncpg", "+psycopg"), future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO qradar_instance (id, name, console_host, api_version, "
                    "verify_ssl, provider_kind, mcp_enabled, enabled, status, "
                    "consecutive_failures) VALUES (:id, :name, :host, '20.0', "
                    "true, 'rest', false, true, 'UNKNOWN', 0)"
                ),
                {"id": instance_id, "name": name, "host": f"https://{name}.example"},
            )
    finally:
        engine.dispose()
    return str(instance_id)


def test_add_enqueues_that_instance_once_after_the_row_is_committed(
    sessions: str, queued: QueueLog, capsys: pytest.CaptureFixture[str]
) -> None:
    other = _insert(sessions, "other")

    assert _add() == 0
    out = capsys.readouterr().out

    assert queued.others == []
    assert len(queued.calls) == 1
    sent = queued.calls[0]["args"]
    visible = queued.calls[0]["visible"]
    assert isinstance(sent, list)
    assert isinstance(visible, list)
    assert visible, "the row was not committed before the enqueue"
    assert sent == [visible[0][0]]
    assert sent[0] != other
    assert visible[0][1] is True
    assert QUEUED in out.splitlines()
    assert _rows(sessions, "SELECT count(*) FROM log_source") == [(0,)]
    assert _rows(sessions, "SELECT count(*) FROM qradar_instance") == [(2,)]


def test_an_unchanged_add_does_not_enqueue_again(
    sessions: str, queued: QueueLog, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add() == 0
    assert QUEUED in capsys.readouterr().out.splitlines()

    assert _add() == 0
    second = capsys.readouterr().out

    assert len(queued.calls) == 1
    assert QUEUED not in second
    assert NOT_QUEUED not in second
    assert _rows(sessions, "SELECT count(*) FROM qradar_instance") == [(1,)]


def test_a_url_change_enqueues_the_same_instance_again(
    sessions: str, queued: QueueLog
) -> None:
    assert _add() == 0
    assert _add(url="https://console.example:8443") == 0

    assert len(queued.calls) == 2
    first = queued.calls[0]["args"]
    second = queued.calls[1]["args"]
    assert first == second
    host = _rows(sessions, "SELECT console_host FROM qradar_instance WHERE name = 'lab'")
    assert host == [("https://console.example:8443",)]


def test_a_description_change_does_not_enqueue(
    sessions: str, queued: QueueLog
) -> None:
    assert _add() == 0
    assert _add("--description", "lab notes") == 0

    assert len(queued.calls) == 1
    assert _rows(sessions, "SELECT description FROM qradar_instance WHERE name = 'lab'") == [
        ("lab notes",)
    ]


def test_a_new_token_enqueues_and_the_same_token_does_not(
    sessions: str, queued: QueueLog, tmp_path: Path
) -> None:
    first = tmp_path / "token-a"
    second = tmp_path / "token-b"
    first.write_text("alpha-token\n")
    second.write_text("beta-token\n")

    assert _add("--token-file", str(first)) == 0
    assert _add("--token-file", str(first)) == 0
    assert len(queued.calls) == 1

    assert _add("--token-file", str(second)) == 0
    assert len(queued.calls) == 2
    assert queued.calls[0]["args"] == queued.calls[1]["args"]

    assert _add() == 0
    assert len(queued.calls) == 2
    # The omitted token did not wipe the stored one. The value itself stays out of the test.
    assert _rows(sessions, "SELECT sec_token IS NOT NULL FROM qradar_instance") == [(True,)]


def test_a_disabled_console_is_stored_and_not_enqueued(
    sessions: str, queued: QueueLog, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add("--disabled") == 0
    out = capsys.readouterr().out

    assert queued.calls == []
    assert queued.others == []
    assert QUEUED not in out
    assert NOT_QUEUED not in out
    assert _rows(sessions, "SELECT enabled FROM qradar_instance WHERE name = 'lab'") == [(False,)]
    assert _rows(sessions, "SELECT count(*) FROM log_source") == [(0,)]


def test_a_disabled_console_stays_unqueued_when_its_url_changes(
    sessions: str, queued: QueueLog
) -> None:
    assert _add("--disabled") == 0
    assert _add("--disabled", url="https://other.example") == 0

    assert queued.calls == []
    assert _rows(
        sessions, "SELECT console_host, enabled FROM qradar_instance WHERE name = 'lab'"
    ) == [("https://other.example", False)]


def test_enabling_a_disabled_console_enqueues_it(
    sessions: str, queued: QueueLog, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add("--disabled") == 0
    capsys.readouterr()

    assert _add() == 0
    out = capsys.readouterr().out

    assert len(queued.calls) == 1
    sent = queued.calls[0]["args"]
    assert isinstance(sent, list)
    row = _rows(sessions, "SELECT id::text, enabled FROM qradar_instance WHERE name = 'lab'")
    assert row == [(sent[0], True)]
    assert QUEUED in out.splitlines()


def test_a_broker_failure_keeps_the_row_prints_the_line_and_exits_zero(
    sessions: str,
    queued: QueueLog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("redis://:s3cret@broker:6379/0")

    monkeypatch.setattr(tasks.sync_instance_inventories_task, "apply_async", explode)

    with caplog.at_level(logging.WARNING, logger="app.cli.qradar"):
        code = _add()
    out, err = capsys.readouterr()

    assert code == 0
    assert NOT_QUEUED in out.splitlines()
    assert QUEUED not in out
    assert "s3cret" not in out + err + caplog.text
    assert _rows(sessions, "SELECT name, enabled FROM qradar_instance") == [("lab", True)]
    assert _rows(sessions, "SELECT count(*) FROM log_source") == [(0,)]
    warnings = [record for record in caplog.records if record.name == "app.cli.qradar"]
    assert len(warnings) == 1
    assert getattr(warnings[0], "error_class", None) == "ConnectionError"
    stored = _rows(sessions, "SELECT id::text FROM qradar_instance")
    assert getattr(warnings[0], "instance_id", None) == stored[0][0]
    assert queued.others == []


def test_a_failed_connection_check_still_queues_and_exits_one(
    sessions: str,
    queued: QueueLog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Down:
        async def validate_connection(self) -> object:
            raise RuntimeError("no route")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(cli, "build_provider_for_instance", lambda *_a, **_k: _Down())

    assert _add(verify=True) == 1
    out = capsys.readouterr().out
    assert "FAILED (RuntimeError)" in out
    assert "no route" not in out
    assert QUEUED in out.splitlines()
    assert len(queued.calls) == 1
    assert queued.calls[0]["visible"]
    assert _rows(sessions, "SELECT count(*) FROM qradar_instance") == [(1,)]
    assert _rows(sessions, "SELECT count(*) FROM log_source") == [(0,)]


def test_a_plaintext_url_exits_two_and_writes_nothing(
    sessions: str, queued: QueueLog, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add(url="http://console.example") == 2
    err = capsys.readouterr().err

    assert "https://" in err
    assert queued.calls == []
    assert queued.others == []
    assert _rows(sessions, "SELECT count(*) FROM qradar_instance") == [(0,)]


@pytest.mark.asyncio
async def test_an_instance_id_does_not_visit_another_console(sessions: str) -> None:
    alpha_id = uuid.uuid4()
    disabled_id = uuid.uuid4()
    engine = create_async_engine(sessions, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as setup:
        setup.add_all(
            [
                QRadarInstance(
                    id=alpha_id,
                    name="alpha",
                    console_host="https://alpha.example",
                    provider_kind="rest",
                    enabled=True,
                ),
                QRadarInstance(
                    name="bravo",
                    console_host="https://bravo.example",
                    provider_kind="rest",
                    enabled=True,
                ),
                QRadarInstance(
                    id=disabled_id,
                    name="down",
                    console_host="https://down.example",
                    provider_kind="rest",
                    enabled=False,
                ),
            ]
        )
        await setup.commit()

    ran: list[str] = []

    async def run(session: object, instance: QRadarInstance, provider: object) -> dict[str, str]:
        ran.append(instance.name)
        return {"status": "ok"}

    try:
        one = await tasks._for_each_instance(run, instance_id=alpha_id, needs_provider=False)
        assert ran == ["alpha"]
        assert one["instances"] == 1

        ran.clear()
        named = await tasks._for_each_instance(
            run, "bravo", instance_id=alpha_id, needs_provider=False
        )
        assert ran == ["alpha"]
        assert named["instances"] == 1

        ran.clear()
        disabled = await tasks._for_each_instance(
            run, instance_id=disabled_id, needs_provider=False
        )
        assert ran == []
        assert disabled["status"] == "no-instance"

        ran.clear()
        missing = await tasks._for_each_instance(
            run, instance_id=uuid.uuid4(), needs_provider=False
        )
        assert ran == []
        assert missing["status"] == "no-instance"
    finally:
        await engine.dispose()
