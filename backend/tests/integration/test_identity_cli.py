"""The operator path: python -m app.cli.identity set, validate, enable."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cli import identity as cli
from app.models import Base
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider

pytestmark = pytest.mark.integration

INSTANCE = "identity-lab"


def _sync_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value.replace("+asyncpg", "+psycopg")


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    sync_url = _sync_url()
    engine = create_engine(sync_url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    instance_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO qradar_instance (id, name, console_host, api_version, verify_ssl, "
                "provider_kind, mcp_enabled, enabled, status, consecutive_failures) "
                "VALUES (:id, :name, 'mock', '20.0', true, 'mock', false, true, 'HEALTHY', 0)"
            ),
            {"id": instance_id, "name": INSTANCE},
        )
        insert_source = text(
            "INSERT INTO log_source (id, instance_id, qradar_id, name, enabled, criticality, "
            "monitoring_enabled, maintenance_mode, business_hours_only, business_hours_start, "
            "business_hours_end, business_days, timezone_name, custom_thresholds, first_seen_at) "
            "VALUES (:id, :instance, :qradar_id, :name, true, 'MEDIUM', true, false, false, "
            "8, 18, CAST('[1,2,3,4,5]' AS jsonb), 'UTC', CAST('{}' AS jsonb), now())"
        )
        for qradar_id, name in ((1003, "dc01"), (1001, "fw-edge-01")):
            connection.execute(
                insert_source,
                {
                    "id": uuid.uuid4(),
                    "instance": instance_id,
                    "qradar_id": qradar_id,
                    "name": name,
                },
            )
    engines: list = []

    def maker() -> async_sessionmaker:  # type: ignore[type-arg]
        async_engine = create_async_engine(
            os.environ["TEST_DATABASE_URL"], poolclass=NullPool
        )
        engines.append(async_engine)
        return async_sessionmaker(async_engine, expire_on_commit=False)

    monkeypatch.setattr(cli, "get_sessionmaker", maker)
    try:
        yield sync_url
    finally:
        if engines:

            async def _close() -> None:
                for item in engines:
                    await item.dispose()

            asyncio.run(_close())
        Base.metadata.drop_all(engine)
        engine.dispose()


def _rows(url: str, statement: str) -> list[tuple]:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(statement))]
    finally:
        engine.dispose()


def _call(command: str, source: str = "1003") -> list[str]:
    return [command, "--instance", INSTANCE, "--log-source", source, "--actor", "tester"]


def _set(source: str = "1003", **extra: str) -> list[str]:
    argv = [
        "set",
        "--instance",
        INSTANCE,
        "--log-source",
        source,
        "--event-id-property",
        extra.get("event_id", "EventID"),
        "--username-property",
        extra.get("username", "username"),
        "--actor",
        "tester",
    ]
    if "event_ids" in extra:
        argv.extend(["--event-ids", extra["event_ids"]])
    if "label" in extra:
        argv.extend(["--product-label", extra["label"]])
    return argv


def test_set_validate_enable_happy_path(operator: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(_set()) == 0
    created = capsys.readouterr().out
    assert "profile v1" in created
    assert "UNVALIDATED" in created
    assert "EventID" not in created
    assert cli.main(_call("validate")) == 0
    assert capsys.readouterr().out.strip() == "validation: VALID"
    assert cli.main(_call("enable")) == 0
    assert capsys.readouterr().out.strip() == "profile enabled"
    actions = [
        row[0] for row in _rows(operator, "SELECT action FROM audit_log ORDER BY created_at")
    ]
    assert actions == [
        "identity.profile.created",
        "identity.profile.validated",
        "identity.profile.enabled",
    ]
    enabled = _rows(operator, "SELECT enabled, validation_status FROM identity_source_profile")
    assert enabled == [(True, "VALID")]


def test_enable_before_validate_is_refused(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(_set()) == 0
    capsys.readouterr()
    assert cli.main(_call("enable")) == 1
    err = capsys.readouterr().err
    assert "profile version 1 is UNVALIDATED; run validate before enable" in err
    assert "EventID" not in err
    assert "username" not in err


def test_enable_after_a_mapping_change_is_refused(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(_set()) == 0
    assert cli.main(_call("validate")) == 0
    assert cli.main(_call("enable")) == 0
    capsys.readouterr()
    assert cli.main(_set(event_id="qid")) == 0
    updated = capsys.readouterr().out
    assert "profile v2" in updated
    assert "qid" not in updated
    assert cli.main(_call("enable")) == 1
    err = capsys.readouterr().err
    assert "profile version 2 is UNVALIDATED; run validate before enable" in err
    assert "qid" not in err


def test_a_bad_property_exits_1_without_echoing_it(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(_set(event_id="sourceip")) == 1
    err = capsys.readouterr().err
    assert "sourceip" not in err
    assert "not an allowed field" in err
    assert _rows(operator, "SELECT count(*) FROM identity_source_profile") == [(0,)]


def test_a_bad_event_id_is_not_echoed(operator: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(_set(event_ids="9999")) == 1
    err = capsys.readouterr().err
    assert "9999" not in err
    assert "catalog integers" in err


def test_a_long_label_is_not_echoed(operator: str, capsys: pytest.CaptureFixture[str]) -> None:
    label = "L" * 129
    assert cli.main(_set(label=label)) == 1
    err = capsys.readouterr().err
    assert label not in err
    assert "too long" in err


def test_an_empty_source_is_inconclusive_and_stored(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(_set(source="1001")) == 0
    capsys.readouterr()
    assert cli.main(
        ["validate", "--instance", INSTANCE, "--log-source", "1001", "--actor", "tester", "--json"]
    ) == 2
    out = capsys.readouterr().out
    assert "INCONCLUSIVE" in out
    assert "EventID" not in out
    stored = _rows(operator, "SELECT validation_status FROM identity_source_profile")
    assert stored == [("INCONCLUSIVE",)]


def test_an_unknown_property_name_is_invalid(
    operator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(_set(event_id="WinEventID")) == 0
    capsys.readouterr()
    assert cli.main(
        ["validate", "--instance", INSTANCE, "--log-source", "1003", "--actor", "tester", "--json"]
    ) == 2
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "WinEventID" not in out


def test_a_provider_error_prints_the_class_only(
    operator: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cli.main(_set()) == 0
    capsys.readouterr()

    async def boom(self, **kwargs):  # type: ignore[no-untyped-def]
        raise ProviderError("SELECT secret FROM events")

    monkeypatch.setattr(MockQRadarProvider, "probe_identity_fields", boom)
    assert cli.main(_call("validate")) == 1
    err = capsys.readouterr().err
    assert err.strip() == "error: ProviderError"
    assert "SELECT" not in err
    assert "secret" not in err


def test_unknown_instance_exits_1(operator: str, capsys: pytest.CaptureFixture[str]) -> None:
    argv = _set()
    argv[argv.index(INSTANCE)] = "missing-console"
    assert cli.main(argv) == 1
    assert "no QRadar instance" in capsys.readouterr().err
