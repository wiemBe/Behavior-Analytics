"""ARL-3 round 1: QRadar's health beside the Ariel load, in `/current`, the CLI and the doctor.

The database reads are replaced here (the integration file proves them against real rows); what
is asserted is the wiring: one entry per enabled console, a failing read that never fails the
part, the connection let go before QRadar is asked, the cache, the CLI block, the registry
sentences and the doctor's verdicts.
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.cli import ariel_load as cli
from app.cli import doctor as doctor_module
from app.cli.doctor import Doctor, Status, render_text
from app.core.config import ProviderKind, Settings, get_settings
from app.i18n.messages import REGISTRY
from app.models.instance import QRadarInstance
from app.providers.base import ProviderAuthError, ProviderUnavailableError
from app.providers.dto import (
    ArielSearchListDTO,
    DeploymentHostDTO,
    LicensePoolDTO,
    LicenseRateDTO,
    ManagedServerDTO,
)
from app.providers.mock import MockQRadarProvider
from app.services import ariel_load_current as current_module
from app.services.ariel_load_current import load_current_with_health, load_qradar
from app.services.qradar_health import (
    HEALTH_CACHE,
    REASON_CODES,
    REASONS,
    ArielRunsSnapshot,
)

SEC_TOKEN = "s3cr3t-qradar-token-do-not-leak"
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
REPO = pathlib.Path(__file__).resolve().parents[3]


def _instance(name: str = "lab", n: int = 1) -> QRadarInstance:
    return QRadarInstance(
        id=uuid.UUID(f"00000000-0000-0000-0000-{n:012d}"),
        name=name,
        console_host=f"https://{name}.example.internal",
        api_version="20.0",
        sec_token=SEC_TOKEN,
        verify_ssl=True,
        provider_kind=ProviderKind.MOCK.value,
    )


class _Session:
    """Records the order of `expunge` and `rollback` against the QRadar reads."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __contains__(self, _obj: object) -> bool:
        return True

    def expunge(self, _obj: object) -> None:
        self.events.append("expunge")

    async def rollback(self) -> None:
        self.events.append("rollback")


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    HEALTH_CACHE.clear()
    yield
    HEALTH_CACHE.clear()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Consoles, the search record and the providers, all scripted. Returns the script."""
    script: dict[str, Any] = {
        "consoles": [_instance("lab", 1)],
        "runs": {},
        "runs_error": None,
        "consoles_error": None,
        "providers": {},
        "events": [],
        "built": [],
    }

    async def consoles(_session: object) -> list[QRadarInstance]:
        if script["consoles_error"]:
            raise script["consoles_error"]
        return list(script["consoles"])

    async def runs(_session: object, ids: list[uuid.UUID], *, as_of: datetime) -> Any:
        if script["runs_error"]:
            raise script["runs_error"]
        return {i: script["runs"].get(i, _snapshot()) for i in ids}

    def build(instance: QRadarInstance, _settings: Settings | None = None) -> MockQRadarProvider:
        script["events"].append("provider")
        script["built"].append(instance.name)
        provider = script["providers"].setdefault(instance.name, MockQRadarProvider())
        assert isinstance(provider, MockQRadarProvider)
        return provider

    monkeypatch.setattr(current_module, "_enabled_consoles", consoles)
    monkeypatch.setattr(current_module, "read_ariel_runs", runs)
    monkeypatch.setattr("app.services.qradar_health.build_provider_for_instance", build)
    return script


def _snapshot(**kw: Any) -> ArielRunsSnapshot:
    values: dict[str, Any] = {
        "window_minutes": 15,
        "searches": 12,
        "unavailable": 0,
        "last_success_at": NOW,
        "in_flight": 1,
    }
    values.update(kw)
    return ArielRunsSnapshot(**values)


def _session(script: dict[str, Any]) -> Any:
    return _Session(script["events"])


# ------------------------------------------------------------- the `qradar` part


async def test_one_entry_per_enabled_console_in_a_list_named_by_console(
    wired: dict[str, Any],
) -> None:
    wired["consoles"] = [_instance("lab", 1), _instance("prod-east", 2)]
    wired["runs"] = {wired["consoles"][1].id: _snapshot(searches=5, in_flight=3)}
    part = await load_qradar(_session(wired), as_of=NOW)
    assert part["available"] is True
    names = [entry["instance_name"] for entry in part["consoles"]]
    assert names == ["lab", "prod-east"]
    lab, east = part["consoles"]
    assert lab["instance_id"] == str(wired["consoles"][0].id)
    # Each console got its own search record.
    assert lab["ariel"]["ours_in_flight"]["value"] == 1
    assert east["ariel"]["ours_in_flight"]["value"] == 3
    assert east["ariel"]["recent"]["value"]["searches"] == 5
    assert east["ariel"]["list_probe"]["value"]["held"] == 4
    json.dumps(part)  # the part is plain JSON


async def test_no_enabled_console_is_an_empty_list_not_a_failure(wired: dict[str, Any]) -> None:
    wired["consoles"] = []
    assert await load_qradar(_session(wired), as_of=NOW) == {"available": True, "consoles": []}


async def test_the_connection_is_released_before_qradar_is_asked(wired: dict[str, Any]) -> None:
    await load_qradar(_session(wired), as_of=NOW)
    events = wired["events"]
    assert events.index("rollback") < events.index("provider")
    assert events[0] == "expunge"


async def test_a_console_list_that_cannot_be_read_says_so_and_asks_nobody(
    wired: dict[str, Any],
) -> None:
    wired["consoles_error"] = RuntimeError(f"password={SEC_TOKEN}")
    part = await load_qradar(_session(wired), as_of=NOW)
    assert part == {"available": False, "reason": "the registered consoles could not be read"}
    assert wired["built"] == []


async def test_an_unreadable_search_record_is_its_own_state_and_qradar_is_still_read(
    wired: dict[str, Any],
) -> None:
    wired["runs_error"] = RuntimeError("boom")
    [entry] = (await load_qradar(_session(wired), as_of=NOW))["consoles"]
    assert entry["reachable"]["value"] is True
    assert entry["servers"]["state"] == "OK"
    for name in ("available", "recent", "ours_in_flight"):
        field = entry["ariel"][name]
        assert field["state"] == "NOT_COLLECTED" and field["value"] is None
        assert field["reason_code"] == "ariel_search_run_unreadable"
        assert field["reason"] == REASONS["ariel_search_run_unreadable"]


async def test_a_health_read_that_raises_is_failed_fields_and_never_fails_the_part(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    wired["consoles"] = [_instance("lab", 1), _instance("prod-east", 2)]

    async def boom(instance: QRadarInstance, **_kw: Any) -> Any:
        if instance.name == "lab":
            raise RuntimeError(f"token {SEC_TOKEN}")
        return await real(instance, **_kw)

    real = current_module.read_qradar_health
    monkeypatch.setattr(current_module, "read_qradar_health", boom)
    lab, east = (await load_qradar(_session(wired), as_of=NOW))["consoles"]
    assert lab["instance_name"] == "lab"
    for name in ("reachable", "api_latency_ms", "version", "servers", "eps", "license"):
        assert lab[name]["state"] == "FAILED" and lab[name]["value"] is None, name
        assert lab[name]["reason_code"] == "read_failed"
    assert lab["ariel"]["list_probe"]["state"] == "FAILED"
    assert SEC_TOKEN not in json.dumps(lab)
    assert east["reachable"]["value"] is True, "the other console is read as usual"


async def test_an_unreachable_and_a_timed_out_console_are_states_of_their_fields(
    wired: dict[str, Any],
) -> None:
    wired["consoles"] = [_instance("down", 1), _instance("slow", 2)]
    down = MockQRadarProvider()
    down.health_faults["probe_about"] = ProviderUnavailableError("no route", status_code=None)
    slow = MockQRadarProvider()
    slow.health_faults["list_deployment_hosts"] = TimeoutError()
    wired["providers"] = {"down": down, "slow": slow}
    part = await load_qradar(_session(wired), as_of=NOW)
    down_entry, slow_entry = part["consoles"]
    assert down_entry["reachable"] == {
        "state": "OK",
        "value": False,
        "reason_code": "unreachable",
        "reason": REASONS["unreachable"],
    }
    assert down_entry["servers"]["reason_code"] == "console_unreachable"
    assert slow_entry["eps"]["state"] == "FAILED" and slow_entry["eps"]["reason_code"] == "timeout"
    assert slow_entry["servers"]["state"] == "OK"


async def test_two_polls_inside_the_ttl_ask_qradar_once(wired: dict[str, Any]) -> None:
    first = (await load_qradar(_session(wired), as_of=NOW))["consoles"][0]
    second = (await load_qradar(_session(wired), as_of=NOW))["consoles"][0]
    assert wired["built"] == ["lab"], "the second poll built no provider"
    provider = wired["providers"]["lab"]
    assert provider.health_calls == {
        "probe_about": 1,
        "list_managed_servers": 1,
        "list_deployment_hosts": 1,
        "get_license_pool": 1,
        "count_ariel_searches": 1,
    }
    assert first["cached"] is False and second["cached"] is True
    assert second["cache_ttl_seconds"] == get_settings().qradar_health_cache_ttl_seconds
    assert second["as_of"] == first["as_of"]


async def test_the_search_record_is_fresh_on_a_cached_poll(wired: dict[str, Any]) -> None:
    [first] = (await load_qradar(_session(wired), as_of=NOW))["consoles"]
    wired["runs"] = {wired["consoles"][0].id: _snapshot(in_flight=7)}
    [second] = (await load_qradar(_session(wired), as_of=NOW))["consoles"]
    assert (first["ariel"]["ours_in_flight"]["value"], second["cached"]) == (1, True)
    assert second["ariel"]["ours_in_flight"]["value"] == 7


async def test_current_keeps_its_parts_and_adds_qradar(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[datetime] = []

    async def load_current(_session: object, *, now: datetime | None = None) -> dict[str, Any]:
        assert now is not None
        seen.append(now)
        return {"as_of": now.isoformat(), "in_flight": {"available": True}, "windows": {}}

    monkeypatch.setattr(current_module.log, "load_current", load_current)
    body = await load_current_with_health(_session(wired), now=NOW)
    assert list(body) == ["as_of", "in_flight", "windows", "qradar"]
    assert body["as_of"] == NOW.isoformat() and seen == [NOW]
    assert body["qradar"]["consoles"][0]["instance_name"] == "lab"


# ------------------------------------------------------------- the registry


def test_every_reason_is_a_coded_message_with_its_english_sentence() -> None:
    codes = {spec.code: spec for spec in REGISTRY if spec.code.startswith("qradar_health.")}
    assert set(codes) == {f"qradar_health.reason.{c}" for c in REASON_CODES}
    for code in REASON_CODES:
        spec = codes[f"qradar_health.reason.{code}"]
        assert spec.params == {} and REASONS[code] == spec.template


def test_the_generated_english_catalog_has_them_and_the_turkish_one_does_not() -> None:
    english = json.loads((REPO / "frontend/messages/en/backend.json").read_text())
    assert english["qradar_health"]["reason"]["forbidden"] == REASONS["forbidden"]
    turkish = json.loads((REPO / "frontend/messages/tr/backend.json").read_text())
    assert "qradar_health" not in turkish


# ------------------------------------------------------------- the CLI


async def _entry(**changes: Any) -> dict[str, Any]:
    provider = MockQRadarProvider()
    for key, value in changes.items():
        setattr(provider, key, value)
    from app.services.qradar_health import HealthReadCache, read_qradar_health

    health = await read_qradar_health(
        _instance(),
        settings=get_settings(),
        provider_factory=lambda _i: provider,
        ariel_runs=_snapshot(),
        cache=HealthReadCache(),
        now=lambda: NOW,
    )
    return health.model_dump(mode="json")


def _current(*entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "as_of": NOW.isoformat(),
        "in_flight": {
            "available": True,
            "active": [],
            "stale": [],
            "stale_after_seconds": 930,
        },
        "slots": {"available": False, "reason": "permit slots could not be read"},
        "windows": {"available": False, "reason": "recent searches could not be read"},
        "qradar": {"available": True, "consoles": list(entries)},
    }


async def test_the_cli_block_says_held_and_never_running() -> None:
    text = cli.render_current_table(_current(await _entry()))
    assert "QRadar health" in text
    assert re.search(r"console lab: reachable, API \d+ ms, QRadar 7\.5\.0 \(read now", text)
    assert "managed hosts: 1 of 1 ACTIVE" in text
    assert "EPS: average 120, peak 900, allocated 5000 (2.4 % of the allocation)" in text
    assert "Ariel searches held by QRadar (all users, finished and active): 4" in text
    assert "our searches in flight: 1" in text
    assert "Ariel answering our latest search: yes" in text
    assert "our searches, last 15 min: 12, 0 found Ariel unavailable" in text
    assert not re.search(r"\brunning\b", text, re.IGNORECASE)
    # The part is below the existing ones, which are unchanged.
    assert text.index("slots: not available") < text.index("QRadar health")


async def test_the_cli_block_names_every_state_it_could_not_read() -> None:
    entry = await _entry(
        health_faults={
            "list_managed_servers": ProviderAuthError("n", status_code=403),
            "count_ariel_searches": ProviderUnavailableError("x", status_code=503),
        }
    )
    text = cli.render_current_table(_current(entry))
    assert f"managed hosts: not read (FORBIDDEN: {REASONS['forbidden']})" in text
    assert (
        f"Ariel searches held by QRadar: not read (FAILED: {REASONS['ariel_unavailable']})" in text
    )
    assert "EPS: average" in text, "the other figures are still printed"

    down = await _entry(
        health_faults={"probe_about": ProviderUnavailableError("x", status_code=None)}
    )
    text = cli.render_current_table(_current(down))
    assert "console lab: UNREACHABLE" in text
    assert f"EPS: not read (NOT_COLLECTED: {REASONS['console_unreachable']})" in text
    assert "EPS: average 0" not in text


async def test_the_cli_block_flags_what_the_license_and_the_hosts_report() -> None:
    entry = await _entry(
        health_hosts=[
            DeploymentHostDTO(hostname="a", average_eps=4800, peak_eps=5000, eps_allocation=5000)
        ],
        health_servers=[
            ManagedServerDTO(hostname="a", status="ACTIVE"),
            ManagedServerDTO(hostname="b", status="DOWN"),
            ManagedServerDTO(hostname="c", status=None),
        ],
        health_license=LicensePoolDTO(
            eps=LicenseRateDTO(total=100, allocated=5000, overallocated=True)
        ),
    )
    text = cli.render_current_table(_current(entry))
    assert "managed hosts: 1 of 3 ACTIVE, 1 without a status (b DOWN, c no status)" in text
    assert "(96.0 % of the allocation)" in text
    assert "license EPS: total 100, allocated 5000 (over-allocated)" in text


async def test_the_cli_block_does_not_call_an_unreported_allocation_fine() -> None:
    entry = await _entry(
        health_license=LicensePoolDTO(eps=LicenseRateDTO(total=100, allocated=None))
    )
    text = cli.render_current_table(_current(entry))
    assert "license EPS: total 100, allocated None (allocation not reported)" in text
    assert "not over-allocated" not in text


def test_the_cli_block_covers_no_console_and_an_unreadable_part() -> None:
    quiet = _current()
    assert "QRadar health: no enabled console is registered" in cli.render_current_table(quiet)
    quiet["qradar"] = {"available": False, "reason": "the registered consoles could not be read"}
    text = cli.render_current_table(quiet)
    assert "QRadar health: not available (the registered consoles could not be read)" in text
    no_part = _current()
    del no_part["qradar"]
    text = cli.render_current_table(no_part)
    assert "QRadar health" not in text
    assert text.splitlines()[-1].startswith("windows: not available"), "nothing is appended"
    assert not text.endswith("\n")


def test_the_cli_current_asks_for_the_combined_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def combined(_session: object) -> dict[str, Any]:
        return _current()

    class _Maker:
        def __call__(self) -> Any:
            class _Ctx:
                async def __aenter__(self) -> None:
                    return None

                async def __aexit__(self, *_a: object) -> None:
                    return None

            return _Ctx()

    async def dispose() -> None:
        return None

    monkeypatch.setattr(cli, "load_current_with_health", combined)
    monkeypatch.setattr("app.core.database.get_sessionmaker", lambda: _Maker())
    monkeypatch.setattr("app.core.database.dispose_engine", dispose)
    assert cli.main(["--current", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["qradar"] == {"available": True, "consoles": []}


# ------------------------------------------------------------- the doctor


def _doctor(provider: MockQRadarProvider | None, instance: QRadarInstance | None) -> Doctor:
    settings = get_settings()

    class _Maker:
        def __call__(self) -> Any:
            raise AssertionError("the check reads no table of its own")

    doctor = Doctor(
        settings=settings,
        sessionmaker=cast(Any, _Maker()),
        provider_factory=lambda _i: cast(Any, provider),
    )

    async def one() -> QRadarInstance | None:
        return instance

    doctor._instance = one  # type: ignore[method-assign]
    return doctor


async def test_the_doctor_check_passes_when_everything_is_in_order() -> None:
    result = await _doctor(MockQRadarProvider(), _instance()).check_qradar_health()
    assert result.status is Status.PASS and result.runbook is None
    assert render_text([result]) == (
        "PASS  qradar_health 1 of 1 managed hosts ACTIVE; average 120 EPS of 5000 allocated "
        "(2.4 %); license pool not over-allocated; QRadar holds 4 Ariel search ids"
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"health_servers": [ManagedServerDTO(hostname="h2", status="DOWN")]},
            "1 managed host(s) not ACTIVE (h2 DOWN)",
        ),
        (
            {
                "health_servers": [
                    ManagedServerDTO(hostname="h1", status="ACTIVE"),
                    ManagedServerDTO(hostname="h3", status=None),
                ]
            },
            "1 managed host(s) not ACTIVE (h3 reports no status)",
        ),
        (
            {
                "health_hosts": [
                    DeploymentHostDTO(
                        hostname="a", average_eps=4501, peak_eps=1, eps_allocation=5000
                    )
                ]
            },
            "average EPS is 90.0 % of the allocation (over 90 %)",
        ),
        (
            {
                "health_license": LicensePoolDTO(
                    eps=LicenseRateDTO(total=100, allocated=5000, overallocated=True)
                )
            },
            "license pool over-allocated (EPS)",
        ),
        (
            {
                "health_license": LicensePoolDTO(
                    eps=LicenseRateDTO(total=5000, allocated=100, overallocated=False),
                    fpm=LicenseRateDTO(total=1, allocated=5, overallocated=True),
                )
            },
            "license pool over-allocated (FPM)",
        ),
    ],
)
async def test_the_doctor_check_warns_for_each_condition(
    changes: dict[str, Any], expected: str
) -> None:
    provider = MockQRadarProvider()
    for key, value in changes.items():
        setattr(provider, key, value)
    result = await _doctor(provider, _instance()).check_qradar_health()
    assert result.status is Status.WARN and result.runbook == "OPS-R67"
    assert expected in result.message


async def test_the_doctor_check_has_a_threshold_on_eps_but_not_on_the_held_searches() -> None:
    at_limit = MockQRadarProvider()
    at_limit.health_hosts = [
        DeploymentHostDTO(hostname="a", average_eps=4500, peak_eps=1, eps_allocation=5000)
    ]
    assert (await _doctor(at_limit, _instance()).check_qradar_health()).status is Status.PASS
    many = MockQRadarProvider()
    many.health_ariel_list = ArielSearchListDTO(held=100_000, truncated=True)
    result = await _doctor(many, _instance()).check_qradar_health()
    assert result.status is Status.PASS
    assert "QRadar holds at least 100000 Ariel search ids" in result.message


async def test_the_doctor_check_skips_an_unreachable_console_and_an_unregistered_one() -> None:
    down = MockQRadarProvider()
    down.health_faults["probe_about"] = ProviderUnavailableError("x", status_code=None)
    result = await _doctor(down, _instance()).check_qradar_health()
    assert (result.status, result.message) == (
        Status.SKIP,
        "the console did not answer; see qradar_auth",
    )
    none = await _doctor(None, None).check_qradar_health()
    assert none.status is Status.SKIP and "no enabled QRadar instance" in none.message


async def test_the_doctor_check_never_calls_unread_figures_healthy() -> None:
    nothing = MockQRadarProvider()
    for name in ("list_managed_servers", "list_deployment_hosts", "get_license_pool"):
        nothing.health_faults[name] = ProviderAuthError("no", status_code=403)
    skipped = await _doctor(nothing, _instance()).check_qradar_health()
    assert skipped.status is Status.SKIP
    assert "nothing could be read" in skipped.message

    partial = MockQRadarProvider()
    partial.health_faults["get_license_pool"] = ProviderAuthError("no", status_code=403)
    result = await _doctor(partial, _instance()).check_qradar_health()
    assert result.status is Status.PASS
    assert "not read: license (FORBIDDEN)" in result.message
    assert "license pool not over-allocated" not in result.message


async def test_the_doctor_check_survives_a_provider_that_will_not_build() -> None:
    def factory(_i: QRadarInstance) -> Any:
        raise RuntimeError(f"token {SEC_TOKEN}")

    settings = get_settings()
    doctor = Doctor(settings=settings, sessionmaker=cast(Any, object()), provider_factory=factory)

    async def one() -> QRadarInstance:
        return _instance()

    doctor._instance = one  # type: ignore[method-assign]
    result = await doctor.check_qradar_health()
    assert result.status is Status.SKIP
    assert SEC_TOKEN not in result.message


def test_the_runbook_entry_exists_and_says_held() -> None:
    operations = (REPO / "docs/OPERATIONS.md").read_text()
    entry = operations[operations.index("### OPS-R67.") :]
    for needle in ("qradar_health", "WARN", "over-allocated", "90 %", "held"):
        assert needle in entry, needle
    assert doctor_module._CHECK_RUNBOOKS[-1] == ("qradar_health", "OPS-R67")
    row = next(line for line in operations.splitlines() if line.startswith("| `qradar_health` |"))
    assert "OPS-R67" in row
    assert not re.search(r"searches? (that )?(is|are) running|running searches", entry, re.I)


@pytest.mark.parametrize(
    "module",
    [
        "app.services.ariel_load_current",
        "app.services.qradar_health",
        "app.cli.ariel_load",
        "app.cli.doctor",
        "app.api.routes.ariel_load",
    ],
)
def test_each_new_module_imports_cleanly_as_the_first_import_of_a_process(
    module: str, tmp_path: pathlib.Path
) -> None:
    """A cold interpreter, as the operator's command starts it: no import cycle hidden by the
    suite's import order."""
    import os
    import subprocess
    import sys

    import app

    backend = str(pathlib.Path(app.__file__).resolve().parent.parent)
    done = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-c", f"import {module}"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": backend},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
