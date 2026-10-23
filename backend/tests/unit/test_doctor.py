from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import ssl
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from httpx import Response

from app.cli import doctor as doctor_module
from app.cli.doctor import (
    CheckResult,
    Doctor,
    Status,
    TlsProbe,
    exit_code,
    render_json,
    render_text,
)
from app.core.config import Environment, Settings
from app.models.enums import SearchFamily
from app.providers.dto import InstanceInfoDTO

#: The `app/` package, for the source sweep that keeps the doctor's known-name set honest.
APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _collector_name_constants(paths: list[pathlib.Path]) -> list[tuple[str, str]]:
    """Every module-level `COLLECTOR_NAME` / `*_COLLECTOR` string constant: (label, value)."""
    found: list[tuple[str, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
                continue
            value = node.value.value
            if not isinstance(value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and (
                    target.id == "COLLECTOR_NAME" or target.id.endswith("COLLECTOR")
                ):
                    found.append((f"{path.name}:{target.id}", value))
    return found


async def _dispose_nothing() -> None:
    """The doctor's engine disposal: a unit test holds no engine to dispose."""


class FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class FakeSession:
    def __init__(
        self,
        *,
        scalar_values: list[Any] | None = None,
        scalars_values: list[list[Any]] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.scalars_values = list(scalars_values or [])
        self.statements: list[object] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, query: object) -> Any:
        self.statements.append(query)
        return self.scalar_values.pop(0)

    async def scalars(self, query: object) -> FakeScalars:
        self.statements.append(query)
        return FakeScalars(self.scalars_values.pop(0))


class FakeSessionmaker:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


class FakeRedis:
    def __init__(
        self, *, ping: object = True, value: object = None, error: Exception | None = None
    ) -> None:
        self.ping_value = ping
        self.value = value
        self.error = error
        self.closed = False

    async def ping(self) -> object:
        if self.error:
            raise self.error
        return self.ping_value

    async def get(self, _key: str) -> object:
        if self.error:
            raise self.error
        return self.value

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def settings(tmp_path) -> Settings:
    token = tmp_path / "qradar.sec"
    ca = tmp_path / "qradar-ca.pem"
    token.write_text("fixture-token", encoding="utf-8")
    ca.write_text("fixture-ca", encoding="utf-8")
    return Settings(
        qradar_host="https://qradar.example.test",
        qradar_api_token_file=str(token),
        qradar_ca_bundle=str(ca),
        public_origin="https://soc.example.test",
        encryption_key="sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=",
    )


def make_doctor(settings: Settings, session: FakeSession | None = None, **kwargs: Any) -> Doctor:
    maker = FakeSessionmaker(session or FakeSession())
    return Doctor(
        settings=settings,
        sessionmaker=cast(Any, maker),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_db_connect_pass_and_fail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    doctor = make_doctor(settings)
    monkeypatch.setattr(doctor, "_scalar", AsyncMock(return_value=1))
    assert (await doctor.check_db_connect()).status is Status.PASS

    monkeypatch.setattr(
        doctor, "_scalar", AsyncMock(side_effect=ConnectionError("password=planted"))
    )
    result = await doctor.check_db_connect()
    assert result.status is Status.FAIL
    assert "planted" not in result.message
    assert settings.postgres_host in result.message


@pytest.mark.asyncio
async def test_migration_head_pass_and_fail(settings: Settings) -> None:
    passed = make_doctor(
        settings, FakeSession(scalars_values=[["0025"]]), migration_head=lambda: "0025"
    )
    assert (await passed.check_migration_head()).status is Status.PASS

    failed = make_doctor(
        settings, FakeSession(scalars_values=[["0024"]]), migration_head=lambda: "0025"
    )
    assert (await failed.check_migration_head()).status is Status.FAIL


@pytest.mark.asyncio
async def test_timescale_pass_warn_and_fail(settings: Settings) -> None:
    assert (
        await make_doctor(settings, FakeSession(scalar_values=[1, 1])).check_timescale()
    ).status is Status.PASS
    assert (
        await make_doctor(settings, FakeSession(scalar_values=[1, None])).check_timescale()
    ).status is Status.WARN
    assert (
        await make_doctor(settings, FakeSession(scalar_values=[None])).check_timescale()
    ).status is Status.WARN
    production = settings.model_copy(update={"environment": Environment.PRODUCTION})
    assert (
        await make_doctor(production, FakeSession(scalar_values=[None])).check_timescale()
    ).status is Status.FAIL


@pytest.mark.asyncio
async def test_retention_policy_disabled_queries_nothing(settings: Settings) -> None:
    session = FakeSession()
    result = await make_doctor(settings, session).check_retention_policy()
    assert result.status is Status.PASS
    assert result.message == "retention is disabled by configuration"
    assert session.statements == []


@pytest.mark.asyncio
async def test_retention_policy_warns_for_each_missing_table_with_one_query(
    settings: Settings,
) -> None:
    enabled = settings.model_copy(
        update={
            "retention_enabled": True,
            "retention_log_source_metric_days": 90,
            "retention_search_result_metric_days": 90,
            "retention_rule_metric_days": 30,
            "retention_offense_snapshot_days": 90,
        }
    )
    session = FakeSession(
        scalars_values=[["log_source_metric", "search_result_metric", "offense_snapshot"]]
    )
    result = await make_doctor(enabled, session).check_retention_policy()
    assert result.status is Status.WARN
    assert result.message == (
        "retention is enabled but these tables have no retention policy: rule_metric"
    )
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_retention_policy_pass_lists_all_configured_tables(settings: Settings) -> None:
    tables = [
        "log_source_metric",
        "search_result_metric",
        "rule_metric",
        "offense_snapshot",
    ]
    enabled = settings.model_copy(
        update={
            "retention_enabled": True,
            "retention_log_source_metric_days": 90,
            "retention_search_result_metric_days": 90,
            "retention_rule_metric_days": 90,
            "retention_offense_snapshot_days": 90,
        }
    )
    session = FakeSession(scalars_values=[tables])
    result = await make_doctor(enabled, session).check_retention_policy()
    assert result.status is Status.PASS
    for table in tables:
        assert table in result.message
    assert len(session.statements) == 1


def _day_group(message: str, days: int) -> str:
    prefix = f"{days}d: "
    for part in message.split("; "):
        if part.startswith(prefix):
            return part[len(prefix) :]
    raise AssertionError(f"{days}d group missing from {message}")


def _largest_retention_days(attr: str) -> int:
    """The field's ``le`` bound, or a large value when the field has none."""
    for meta in Settings.model_fields[attr].metadata:
        upper = getattr(meta, "le", None)
        if upper is not None:
            return int(upper)
    return 1_000_000_000


def _distinct_retention_days() -> dict[str, int]:
    """One day per setting, each near ``le`` or a distinct large value.

    Tables that share a setting stay together. Distinct settings each open
    their own group, which is the long form of the doctor line.
    """
    attrs: list[str] = []
    for deleter in doctor_module.BATCH_DELETERS:
        for item in deleter.tables:
            if item.days_attr not in attrs:
                attrs.append(item.days_attr)
    update: dict[str, int] = {}
    for offset, attr in enumerate(attrs):
        upper = None
        lower = 1
        for meta in Settings.model_fields[attr].metadata:
            le = getattr(meta, "le", None)
            ge = getattr(meta, "ge", None)
            if le is not None:
                upper = int(le)
            if ge is not None:
                lower = int(ge)
        value = (1_000_000_000 if upper is None else upper) - offset
        if value < lower:
            raise AssertionError(f"{attr} distinct day {value} is below {lower}")
        update[attr] = value
    if len(set(update.values())) != len(update):
        raise AssertionError(f"day values are not distinct: {update}")
    return update


@pytest.mark.asyncio
async def test_batch_retention_lists_every_deleter_and_queries_nothing(
    settings: Settings,
) -> None:
    session = FakeSession()
    result = await make_doctor(settings, session).check_batch_retention()
    assert result.status is Status.PASS
    assert result.runbook == "OPS-R14"
    assert session.statements == []
    assert "not gated by RETENTION_ENABLED" in result.message
    assert result.message.count("WAF_RETENTION_ENABLED") == 1
    assert result.message.count("IPS_RETENTION_ENABLED") == 1
    assert "apply_timescale_policies" not in result.message
    assert frozenset(
        item.table for deleter in doctor_module.BATCH_DELETERS for item in deleter.tables
    ) == doctor_module._BATCH_TABLE_NAMES
    for deleter in doctor_module.BATCH_DELETERS:
        assert deleter.task not in result.message
        for item in deleter.tables:
            assert item.table in result.message
    assert _day_group(result.message, 365) == "ips_anomaly, log_source_anomaly"
    rendered_text = render_text([result])
    rendered_json = render_json([result])
    assert json.loads(rendered_json)[0]["message"] == result.message
    assert result.message in rendered_text
    assert "***redacted***" not in rendered_text + rendered_json
    assert len(result.message) <= doctor_module._MESSAGE_CAP
    changed = settings.model_copy(update={"catchall_retention_days": 17})
    again = await make_doctor(changed, FakeSession()).check_batch_retention()
    assert _day_group(again.message, 17) == "catchall_bucket, catchall_sender"
    assert "catchall_bucket" not in _day_group(again.message, 35)
    assert "catchall_sender" not in _day_group(again.message, 35)


@pytest.mark.asyncio
async def test_batch_retention_render_fits_at_largest_day_values(settings: Settings) -> None:
    update = {
        item.days_attr: _largest_retention_days(item.days_attr)
        for deleter in doctor_module.BATCH_DELETERS
        for item in deleter.tables
    }
    widened = settings.model_copy(update=update)
    result = await make_doctor(widened, FakeSession()).check_batch_retention()
    rendered_text = render_text([result])
    rendered_json = render_json([result])
    payload = json.loads(rendered_json)
    assert payload[0]["message"] == result.message
    assert result.message in rendered_text
    assert len(result.message) <= doctor_module._MESSAGE_CAP
    assert doctor_module._safe_text(result.message) == result.message
    assert "***redacted***" not in rendered_text
    assert "***redacted***" not in rendered_json
    for name in doctor_module._BATCH_TABLE_NAMES:
        assert name in rendered_text
        assert name in payload[0]["message"]
    assert "3650d:" in result.message
    assert "1000000000d:" in result.message


@pytest.mark.asyncio
async def test_batch_retention_render_fits_when_every_day_value_differs(
    settings: Settings,
) -> None:
    days = _distinct_retention_days()
    widened = settings.model_copy(update=days)
    result = await make_doctor(widened, FakeSession()).check_batch_retention()
    rendered_text = render_text([result])
    rendered_json = render_json([result])
    payload = json.loads(rendered_json)
    groups = [part for part in result.message.split("; ") if part[:1].isdigit()]
    assert len(groups) == len(days)
    for name in doctor_module._BATCH_TABLE_NAMES:
        assert name in rendered_text
        assert name in payload[0]["message"]
    for gate in ("WAF_RETENTION_ENABLED", "IPS_RETENTION_ENABLED"):
        assert gate in rendered_text
        assert gate in payload[0]["message"]
    assert "***redacted***" not in rendered_text
    assert "***redacted***" not in rendered_json
    assert payload[0]["message"] == result.message
    assert result.message in rendered_text
    assert doctor_module._safe_text(result.message) == result.message
    assert len(result.message) <= doctor_module._MESSAGE_CAP


@pytest.mark.asyncio
async def test_redis_pass_and_fail(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    passing = FakeRedis()
    monkeypatch.setattr(doctor_module.redis, "from_url", lambda *_args, **_kwargs: passing)
    assert (await make_doctor(settings).check_redis()).status is Status.PASS
    assert passing.closed

    failing = FakeRedis(error=ConnectionError("redis://user:planted@redis/0"))
    monkeypatch.setattr(doctor_module.redis, "from_url", lambda *_args, **_kwargs: failing)
    result = await make_doctor(settings).check_redis()
    assert result.status is Status.FAIL
    assert "planted" not in result.message


@pytest.mark.asyncio
async def test_celery_workers_pass_and_fail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspect_mock = MagicMock()
    inspect_mock.ping.return_value = {
        "celery@worker-b": {"ok": "pong"},
        "celery@worker-a": {"ok": "pong"},
    }
    inspect_factory = MagicMock(return_value=inspect_mock)
    monkeypatch.setattr(doctor_module.celery_app.control, "inspect", inspect_factory)
    result = await make_doctor(settings).check_celery_workers()
    assert result.status is Status.PASS
    assert result.message == (
        "2 worker(s) returned pong: celery@worker-a, celery@worker-b"
    )
    inspect_factory.assert_called_once_with(timeout=2.0)

    inspect_mock.ping.return_value = None
    result = await make_doctor(settings).check_celery_workers()
    assert result.status is Status.FAIL
    assert result.runbook == "OPS-R41"


@pytest.mark.asyncio
async def test_celery_beat_pass_and_fail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    client = FakeRedis(value=(now - timedelta(seconds=20)).isoformat())
    monkeypatch.setattr(doctor_module.redis, "from_url", lambda *_args, **_kwargs: client)
    assert (await make_doctor(settings, now=lambda: now).check_celery_beat()).status is Status.PASS

    client.value = None
    missing = await make_doctor(settings, now=lambda: now).check_celery_beat()
    assert missing.status is Status.FAIL
    assert "or no worker is running its tasks" in missing.message

    client.value = (now - timedelta(seconds=180)).isoformat()
    stale = await make_doctor(settings, now=lambda: now).check_celery_beat()
    assert stale.status is Status.FAIL
    assert "or no worker is running its tasks" in stale.message


@pytest.mark.asyncio
async def test_qradar_tls_pass_warn_and_fail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    context = ssl.create_default_context()
    monkeypatch.setattr(doctor_module, "build_ssl_context", lambda *_args, **_kwargs: context)
    passed = make_doctor(
        settings,
        now=lambda: now,
        tls_probe=AsyncMock(return_value=TlsProbe(now + timedelta(days=90))),
    )
    monkeypatch.setattr(passed, "_instance", AsyncMock(return_value=None))
    assert (await passed.check_qradar_tls()).status is Status.PASS

    warning = make_doctor(
        settings,
        now=lambda: now,
        tls_probe=AsyncMock(return_value=TlsProbe(now + timedelta(days=10))),
    )
    monkeypatch.setattr(warning, "_instance", AsyncMock(return_value=None))
    assert (await warning.check_qradar_tls()).status is Status.WARN

    failed = make_doctor(settings, tls_probe=AsyncMock(side_effect=ssl.SSLError("bad chain")))
    monkeypatch.setattr(failed, "_instance", AsyncMock(return_value=None))
    assert (await failed.check_qradar_tls()).status is Status.FAIL


@pytest.mark.asyncio
async def test_qradar_auth_only_calls_validate_connection(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = SimpleNamespace(console_host="https://qradar.example.test")
    provider = MagicMock()
    provider.validate_connection = AsyncMock(return_value=InstanceInfoDTO(version="7.6.0"))
    provider.aclose = AsyncMock()
    doctor = make_doctor(settings, provider_factory=lambda _instance: provider)
    monkeypatch.setattr(doctor, "_instance", AsyncMock(return_value=instance))

    assert (await doctor.check_qradar_auth()).status is Status.PASS
    provider.validate_connection.assert_awaited_once_with()
    provider.aclose.assert_awaited_once_with()
    assert [call[0] for call in provider.method_calls] == ["validate_connection", "aclose"]

    provider.validate_connection = AsyncMock(side_effect=RuntimeError("token=planted-password"))
    provider.aclose = AsyncMock()
    result = await doctor.check_qradar_auth()
    assert result.status is Status.FAIL
    assert "planted-password" not in result.message


@pytest.mark.asyncio
async def test_watermarks_pass_warn_and_fail(settings: Settings) -> None:
    healthy = SimpleNamespace(
        collector="log_source_metric", consecutive_failures=0, last_error=None, lag_seconds=10
    )
    late = SimpleNamespace(
        collector="log_source_metric", consecutive_failures=0, last_error=None, lag_seconds=1000
    )
    failed = SimpleNamespace(
        collector="log_source_metric", consecutive_failures=3, last_error=None, lag_seconds=10
    )
    cadence = SimpleNamespace(family="PHASE_A_VOLUME", cadence_seconds=300)
    assert (
        await make_doctor(
            settings, FakeSession(scalars_values=[[healthy], [cadence]])
        ).check_watermarks()
    ).status is Status.PASS
    assert (
        await make_doctor(
            settings, FakeSession(scalars_values=[[late], [cadence]])
        ).check_watermarks()
    ).status is Status.WARN
    assert (
        await make_doctor(
            settings, FakeSession(scalars_values=[[failed], [cadence]])
        ).check_watermarks()
    ).status is Status.FAIL


@pytest.mark.parametrize(
    "collector, expected",
    [
        ("waf_endpoint_metric:17", 900),
        ("ips_collection:18", 900),
        ("rule_match", 300),
        ("catchall_senders", 300),
        ("geo_countries_source", 300),
    ],
)
def test_watermark_family_fallbacks_use_the_default_cadence(
    settings: Settings, collector: str, expected: int
) -> None:
    doctor = make_doctor(settings)
    assert doctor._watermark_cadence(collector, {}) == expected


@pytest.mark.asyncio
async def test_failing_watermark_names_print_unredacted_but_secrets_stay_masked(
    settings: Settings,
) -> None:
    from app.collectors.geo_collector import watermark_name
    from app.geo.fold import GeoDirection

    names = [watermark_name(direction) for direction in GeoDirection]
    names.append("catchall_senders")
    secret = "Zk9qL2mPx7VbN4cRt8YwHs3Q"  # token-shaped, not a known collector name
    rows = [
        SimpleNamespace(collector=n, consecutive_failures=3, last_error=None, lag_seconds=None)
        for n in [*names, secret, "password=planted-password"]
    ]
    cadence = SimpleNamespace(family="PHASE_A_VOLUME", cadence_seconds=300)

    result = await make_doctor(
        settings, FakeSession(scalars_values=[rows, [cadence]])
    ).check_watermarks()

    assert result.status is Status.FAIL
    for name in names:
        assert name in result.message
    assert "geo_countries_destination" in result.message
    assert secret not in result.message
    assert "planted-password" not in result.message
    assert "***redacted***" in result.message

    # The output layer redacts the message a second time, so the name has to survive that
    # pass too: the operator reads the printed line, not the check result.
    rendered = render_text([result]) + render_json([result])
    assert "geo_countries_destination" in rendered
    assert secret not in rendered
    assert "planted-password" not in rendered


def test_doctor_cli_prints_a_known_watermark_name_and_masks_a_token(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator's path: `python -m app.cli.doctor` prints the failing collectors."""
    from app.collectors.geo_collector import watermark_name
    from app.geo.fold import GeoDirection

    secret = "Zk9qL2mPx7VbN4cRt8YwHs3Qa1b"  # 30 characters, not a known collector name
    names = [
        watermark_name(GeoDirection.SOURCE),
        watermark_name(GeoDirection.DESTINATION),
        "waf_endpoint_evaluation:17",
    ]
    rows = [
        SimpleNamespace(collector=name, consecutive_failures=3, last_error=None, lag_seconds=None)
        for name in [*names, secret]
    ]
    cadence = SimpleNamespace(family="PHASE_A_VOLUME", cadence_seconds=300)

    def build_doctor() -> Doctor:
        doctor = make_doctor(settings, FakeSession(scalars_values=[rows, [cadence]]))
        for check_id, _runbook in doctor_module._CHECK_RUNBOOKS:
            if check_id == "watermarks":
                continue
            monkeypatch.setattr(
                doctor,
                f"check_{check_id}",
                AsyncMock(return_value=CheckResult(check_id, Status.PASS, "healthy")),
            )
        return doctor

    monkeypatch.setattr(doctor_module, "Doctor", build_doctor)
    monkeypatch.setattr(doctor_module, "dispose_engine", _dispose_nothing)

    monkeypatch.setattr(sys, "argv", ["doctor"])
    assert doctor_module.main() == 2
    printed = capsys.readouterr().out
    for name in names:
        assert name in printed
    assert secret not in printed
    assert "***redacted***" in printed

    monkeypatch.setattr(sys, "argv", ["doctor", "--json"])
    assert doctor_module.main() == 2
    printed = capsys.readouterr().out
    payload = json.loads(printed)
    watermarks = next(row for row in payload if row["id"] == "watermarks")
    for name in names:
        assert name in watermarks["message"]
    assert secret not in watermarks["message"]
    assert secret not in printed


def test_watermark_names_cover_every_collector_name_the_code_defines() -> None:
    """The doctor's known-name set cannot drift from the collectors the code defines."""
    from app.collectors.geo_collector import watermark_name
    from app.geo.fold import GeoDirection

    constants = _collector_name_constants(sorted(APP.rglob("*.py")))
    assert constants, "the scanner found no collector constant: it proves nothing"
    names = {value for _label, value in constants}
    names.update(watermark_name(direction) for direction in GeoDirection)
    missing = sorted(names - set(doctor_module._WATERMARK_NAMES))
    assert not missing, f"the doctor would mask these collector names: {missing}"


def test_the_collector_name_scanner_finds_a_constant(tmp_path: pathlib.Path) -> None:
    """The scanner must actually find something, or the guard above proves nothing."""
    sample = tmp_path / "sample.py"
    sample.write_text('COLLECTOR_NAME = "some_collector"\n', encoding="utf-8")
    assert _collector_name_constants([sample]) == [("sample.py:COLLECTOR_NAME", "some_collector")]
    empty = tmp_path / "empty.py"
    empty.write_text("VALUE = 1\n", encoding="utf-8")
    assert _collector_name_constants([empty]) == []


@pytest.mark.asyncio
async def test_cadence_pass_and_warn(settings: Settings) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    on_time = SimpleNamespace(
        family="PHASE_A_VOLUME", cadence_seconds=300, last_run_at=now - timedelta(seconds=10)
    )
    late = SimpleNamespace(
        family="PHASE_A_VOLUME", cadence_seconds=300, last_run_at=now - timedelta(hours=2)
    )
    assert (
        await make_doctor(
            settings, FakeSession(scalars_values=[[on_time]]), now=lambda: now
        ).check_cadence()
    ).status is Status.PASS
    assert (
        await make_doctor(
            settings, FakeSession(scalars_values=[[late]]), now=lambda: now
        ).check_cadence()
    ).status is Status.WARN


@pytest.mark.asyncio
async def test_cadence_skips_property_watch_while_its_switch_is_off(settings: Settings) -> None:
    """The default install: `PW_COLLECTION_ENABLED=false`, a PW row never run, no warning."""
    never = SimpleNamespace(family="PROPERTY_WATCH", cadence_seconds=3600, last_run_at=None)
    result = await make_doctor(settings, FakeSession(scalars_values=[[never]])).check_cadence()
    assert result.status is Status.PASS
    assert "PROPERTY_WATCH" not in result.message


@pytest.mark.asyncio
async def test_cadence_warns_for_property_watch_when_enabled_and_late(settings: Settings) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    late = SimpleNamespace(
        family="PROPERTY_WATCH", cadence_seconds=3600, last_run_at=now - timedelta(hours=3)
    )
    enabled = settings.model_copy(update={"pw_collection_enabled": True})
    result = await make_doctor(
        enabled, FakeSession(scalars_values=[[late]]), now=lambda: now
    ).check_cadence()
    assert result.status is Status.WARN
    assert "PROPERTY_WATCH" in result.message


@pytest.mark.asyncio
async def test_cadence_skips_explanation_when_its_switch_is_off(settings: Settings) -> None:
    never = SimpleNamespace(
        family="ANOMALY_EXPLANATION", cadence_seconds=3600, last_run_at=None
    )
    off = settings.model_copy(update={"explanation_enabled": False})
    result = await make_doctor(off, FakeSession(scalars_values=[[never]])).check_cadence()
    assert result.status is Status.PASS
    assert "ANOMALY_EXPLANATION" not in result.message


@pytest.mark.asyncio
async def test_cadence_warns_for_explanation_when_enabled_and_late(settings: Settings) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    late = SimpleNamespace(
        family="ANOMALY_EXPLANATION", cadence_seconds=3600, last_run_at=now - timedelta(hours=3)
    )
    # `explanation_enabled` is on by default, so no settings override is needed.
    result = await make_doctor(
        settings, FakeSession(scalars_values=[[late]]), now=lambda: now
    ).check_cadence()
    assert result.status is Status.WARN
    assert "ANOMALY_EXPLANATION" in result.message


@pytest.mark.asyncio
async def test_families_off_names_every_switch_in_switch_map_order(settings: Settings) -> None:
    result = await make_doctor(settings).check_families_off()
    assert result.status is Status.PASS
    expected = [attribute.upper() for attribute in doctor_module._FAMILY_SWITCHES.values()]
    expected.append("CUSTOM_RULES_ENABLED")
    assert [part.split("=")[0] for part in result.message.split(", ")] == expected


def test_every_search_family_has_a_switch_or_is_deliberately_switchless() -> None:
    """A new `SearchFamily` member without a switch cannot slip in unnoticed."""
    mapped = set(doctor_module._FAMILY_SWITCHES)
    known = mapped | set(doctor_module._FAMILIES_WITHOUT_SWITCH)
    assert known == {family.value for family in SearchFamily}
    for attribute in doctor_module._FAMILY_SWITCHES.values():
        assert attribute in Settings.model_fields
    # `PHASE_A_VOLUME` is the one switchless family, and the volume collector always runs.
    assert "PHASE_A_VOLUME" in doctor_module._FAMILIES_WITHOUT_SWITCH
    assert not mapped & set(doctor_module._FAMILIES_WITHOUT_SWITCH)


def test_family_enabled_reads_the_map(settings: Settings) -> None:
    doctor = make_doctor(settings)
    assert doctor._family_enabled("PHASE_A_VOLUME") is True
    assert doctor._family_enabled("PROPERTY_WATCH") is False
    assert doctor._family_enabled("ANOMALY_EXPLANATION") is True
    off = make_doctor(settings.model_copy(update={"explanation_enabled": False}))
    assert off._family_enabled("ANOMALY_EXPLANATION") is False


@pytest.mark.asyncio
async def test_families_off_reports_both_states_as_pass(settings: Settings) -> None:
    off = await make_doctor(settings).check_families_off()
    assert off.status is Status.PASS
    assert "WAF_COLLECTION_ENABLED=off" in off.message
    enabled = settings.model_copy(
        update={
            "waf_collection_enabled": True,
            "ips_collection_enabled": True,
            "rule_match_collection_enabled": True,
            "custom_rules_enabled": True,
        }
    )
    on = await make_doctor(enabled).check_families_off()
    assert on.status is Status.PASS
    assert "CUSTOM_RULES_ENABLED=on" in on.message
    rendered = render_text([on])
    assert "RULE_MATCH_COLLECTION_ENABLED=on" in rendered
    assert "CUSTOM_RULES_ENABLED=on" in rendered


@pytest.mark.asyncio
async def test_errors_recent_pass_warn_and_fail(settings: Settings) -> None:
    for count, expected in ((0, Status.PASS), (1, Status.WARN), (3, Status.FAIL)):
        result = await make_doctor(
            settings, FakeSession(scalar_values=[count])
        ).check_errors_recent()
        assert result.status is expected


@pytest.mark.asyncio
async def test_pg_database_size_pass_and_fail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    doctor = make_doctor(settings)
    monkeypatch.setattr(doctor, "_scalar", AsyncMock(return_value=1024))
    assert (await doctor.check_pg_database_size()).status is Status.PASS
    monkeypatch.setattr(doctor, "_scalar", AsyncMock(side_effect=RuntimeError("dsn planted-dsn")))
    result = await doctor.check_pg_database_size()
    assert result.status is Status.FAIL
    assert "planted-dsn" not in result.message


@pytest.mark.asyncio
async def test_encryption_pass_warn_and_fail(settings: Settings) -> None:
    assert (
        await make_doctor(settings, FakeSession(scalar_values=["decrypted"])).check_encryption()
    ).status is Status.PASS
    assert (
        await make_doctor(settings, FakeSession(scalar_values=[None])).check_encryption()
    ).status is Status.WARN
    failed_session = FakeSession(scalar_values=[])
    failed_session.scalar = AsyncMock(side_effect=RuntimeError("ciphertext gAAAAAplantedsecret"))
    result = await make_doctor(settings, failed_session).check_encryption()
    assert result.status is Status.FAIL
    assert "gAAAAA" not in result.message


@pytest.mark.asyncio
async def test_secrets_files_pass_and_fail(settings: Settings) -> None:
    assert (await make_doctor(settings).check_secrets_files()).status is Status.PASS
    missing = settings.model_copy(update={"qradar_api_token_file": None})
    result = await make_doctor(missing).check_secrets_files()
    assert result.status is Status.FAIL
    assert "file is not configured" in result.message


@pytest.mark.asyncio
async def test_public_origin_pass_and_fail(settings: Settings) -> None:
    assert (await make_doctor(settings).check_public_origin()).status is Status.PASS
    insecure = settings.model_copy(update={"public_origin": "http://soc.example.test"})
    assert (await make_doctor(insecure).check_public_origin()).status is Status.FAIL


@pytest.mark.asyncio
@respx.mock
async def test_version_match_pass_warn_and_fail(settings: Settings) -> None:
    respx.get(doctor_module.FRONTEND_HEALTH_URL).mock(
        return_value=Response(200, json={"status": "ok", "version": "0.1.0"})
    )
    backend = respx.get(doctor_module.BACKEND_VERSION_URL).mock(
        return_value=Response(200, json={"version": "0.1.0"})
    )
    doctor = make_doctor(settings)
    assert (await doctor.check_version_match()).status is Status.PASS

    backend.mock(return_value=Response(200, json={"version": "0.2.0"}))
    assert (await doctor.check_version_match()).status is Status.WARN

    respx.get(doctor_module.FRONTEND_HEALTH_URL).mock(return_value=Response(503))
    assert (await doctor.check_version_match()).status is Status.FAIL


def test_output_redacts_token_password_dsn_and_ciphertext() -> None:
    planted = (
        "token=abcdefghijklmnopqrstuvwxyz123456 "
        "password=hunter2 "
        "postgresql://user:dbpass@db.example/test "
        "gAAAAAabcdefghijklmnopqrstuvwxyz123456"
    )
    results = [CheckResult("fixture", Status.FAIL, planted, "OPS-R1")]
    output = render_text(results) + render_json(results)
    for secret in ("abcdefghijklmnopqrstuvwxyz123456", "hunter2", "dbpass", "gAAAAA"):
        assert secret not in output


def test_exit_code_matrix() -> None:
    passed = CheckResult("pass", Status.PASS, "ok")
    warned = CheckResult("warn", Status.WARN, "warn")
    failed = CheckResult("fail", Status.FAIL, "fail")
    assert exit_code([passed]) == 0
    assert exit_code([passed, warned]) == 1
    assert exit_code([passed, warned, failed]) == 2


@pytest.mark.asyncio
async def test_all_healthy_run_exits_zero(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    doctor = make_doctor(settings)
    for check_id, _runbook in doctor_module._CHECK_RUNBOOKS:
        monkeypatch.setattr(
            doctor,
            f"check_{check_id}",
            AsyncMock(return_value=CheckResult(check_id, Status.PASS, "healthy")),
        )

    results = await doctor.run()

    assert "qradar_clock" not in {result.id for result in results}
    assert exit_code(results) == 0


@pytest.mark.asyncio
async def test_timed_out_check_fails_and_next_check_runs(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    doctor = make_doctor(settings)
    next_check_ran = False

    async def too_slow() -> CheckResult:
        await asyncio.sleep(1)
        return CheckResult("db_connect", Status.PASS, "unexpected")

    async def next_check() -> CheckResult:
        nonlocal next_check_ran
        next_check_ran = True
        return CheckResult("migration_head", Status.PASS, "healthy")

    for check_id, _runbook in doctor_module._CHECK_RUNBOOKS:
        monkeypatch.setattr(
            doctor,
            f"check_{check_id}",
            AsyncMock(return_value=CheckResult(check_id, Status.PASS, "healthy")),
        )
    monkeypatch.setattr(doctor_module, "CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(doctor, "check_db_connect", too_slow)
    monkeypatch.setattr(doctor, "check_migration_head", next_check)

    results = await doctor.run()

    assert results[0] == CheckResult(
        "db_connect", Status.FAIL, "did not answer within 0.01 s", "OPS-R36"
    )
    assert next_check_ran
    assert results[1].status is Status.PASS


@pytest.mark.asyncio
async def test_unknown_settings_pass_and_warn(settings: Settings) -> None:
    clean = {
        "CATCHALL_COLLECTION_ENABLED": "true",
        "QRADAR_HOST": "https://qradar.example",
        "NOTIFY_TEAMS_WEBHOOK_URL": "https://hooks.example/planted-webhook-secret",
        "SMTP_HOST": "mail.example",
        "GIT_SHA": "abc123",
        "PATH": "/usr/bin",
        "HOSTNAME": "box",
    }
    passed = await make_doctor(settings, environ=clean).check_unknown_settings()
    assert passed.status is Status.PASS
    assert passed.message == "no unknown settings"
    assert passed.runbook is None

    planted = "planted-secret-value-9f3a"
    warned = await make_doctor(
        settings, environ={**clean, "CATCHALL_COLLECTION_ENABLE": planted}
    ).check_unknown_settings()
    assert warned.status is Status.WARN
    assert warned.message == "ignored unknown settings: CATCHALL_COLLECTION_ENABLE"
    assert planted not in warned.message
    assert warned.runbook == "OPS-R62"
    assert ("unknown_settings", "OPS-R62") in doctor_module._CHECK_RUNBOOKS


@pytest.mark.asyncio
async def test_unknown_settings_pass_for_the_names_install_and_compose_use(
    settings: Settings,
) -> None:
    from tests.unit.test_config_and_crypto import deployment_env_names

    planted = "planted-install-value"
    environ = dict.fromkeys(deployment_env_names(), planted)
    result = await make_doctor(settings, environ=environ).check_unknown_settings()
    assert result.status is Status.PASS
    assert result.message == "no unknown settings"
    assert result.runbook is None
    assert planted not in result.message
