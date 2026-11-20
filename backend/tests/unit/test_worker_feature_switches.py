"""Workers decide from the effective feature switches (OPS-FLAGS S3a): the unit half.

The database-backed behaviour (row over env over default, one statement per run, the
fleet stopping mid-run) is in `tests/integration/test_worker_feature_switches.py`.
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from app.core.config import Settings
from app.models.feature_switch import FEATURE_KEYS
from app.services.feature_switches import UNREADABLE_REASON
from app.workers import tasks

REAL_EFFECTIVE_SETTINGS = tasks._effective_settings
APP = pathlib.Path(tasks.__file__).resolve().parents[1]

#: Functions that still read a switch next to `get_settings()`. The constructor falls
#: back to it only when a caller passes no settings; every worker passes the effective
#: ones. S3b replaces the API-side callers and may shrink this.
KNOWN_DIRECT_READERS = {("services/pw_pipeline.py", "__init__")}


@pytest.fixture
def real_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "_effective_settings", REAL_EFFECTIVE_SETTINGS)


class _BrokenSession:
    async def __aenter__(self) -> _BrokenSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def scalars(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("connection lost, password=hunter2")


@pytest.mark.asyncio
async def test_an_unreadable_table_turns_every_switch_off_and_logs_the_reason_once(
    real_switches: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    base = Settings(_env_file=None, waf_collection_enabled=True, ips_collection_enabled=True)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: lambda: _BrokenSession())

    with caplog.at_level(logging.WARNING, logger="app.tasks"):
        settings, reason = await tasks._effective_settings("dispatch_waf_collection")

    assert reason == UNREADABLE_REASON
    assert not any(getattr(settings, key) for key in FEATURE_KEYS)
    lines = [r for r in caplog.records if r.name == "app.tasks"]
    assert [(r.task, r.reason) for r in lines] == [  # type: ignore[attr-defined]
        ("dispatch_waf_collection", UNREADABLE_REASON)
    ]
    # The task's own line carries the reason code, never the driver's message.
    assert "hunter2" not in " ".join(r.getMessage() for r in lines)


@pytest.mark.asyncio
async def test_a_sessionmaker_that_raises_is_the_same_fail_closed_read(
    real_switches: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> object:
        raise OSError("no pool")

    monkeypatch.setattr(tasks, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(tasks, "get_sessionmaker", broken)
    settings, reason = await tasks._effective_settings("x")
    assert reason == UNREADABLE_REASON
    assert not any(getattr(settings, key) for key in FEATURE_KEYS)


def test_the_reason_is_added_only_when_the_table_was_unreadable() -> None:
    assert tasks._with_reason({"status": "disabled"}, None) == {"status": "disabled"}
    assert tasks._with_reason({"status": "disabled"}, UNREADABLE_REASON) == {
        "status": "disabled",
        "reason": UNREADABLE_REASON,
    }


def _functions(path: pathlib.Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == name
        for n in ast.walk(node)
    )


def _reads_a_switch(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr in FEATURE_KEYS for n in ast.walk(node))


def test_no_function_reads_a_switch_from_get_settings_except_the_known_ones() -> None:
    found = {
        (path.relative_to(APP).as_posix(), fn.name)
        for path in APP.rglob("*.py")
        for fn in _functions(path)
        if _calls(fn, "get_settings") and _reads_a_switch(fn)
    }
    assert found <= KNOWN_DIRECT_READERS, sorted(found - KNOWN_DIRECT_READERS)


def test_every_task_that_reads_a_switch_gets_it_from_the_effective_settings() -> None:
    readers = [fn for fn in _functions(pathlib.Path(tasks.__file__)) if _reads_a_switch(fn)]
    assert len(readers) >= 10
    for fn in readers:
        assert _calls(fn, "_effective_settings"), fn.name
        assert not _calls(fn, "get_settings"), fn.name


#: Route modules whose handlers read a switch, directly or through a service they hand
#: `settings` to. They take `EffectiveSettingsDep`; a plain `SettingsDep` would read `.env`.
EFFECTIVE_ROUTE_MODULES = (
    "custom_rules", "entities", "history_backfill", "property_watches",
    "qradar_health_detail", "rules", "search_cadence", "waf",
)


@pytest.mark.parametrize("module", EFFECTIVE_ROUTE_MODULES)
def test_routes_that_read_a_switch_take_the_effective_settings(module: str) -> None:
    tree = ast.parse((APP / "api" / "routes" / f"{module}.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "EffectiveSettingsDep" in names
    assert "SettingsDep" not in names


def test_the_anomaly_routes_that_reach_a_switch_take_the_effective_settings() -> None:
    """Only these handlers read one (detector status, WAF collection health)."""
    tree = ast.parse((APP / "api" / "routes" / "anomalies.py").read_text())
    effective = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.AsyncFunctionDef)
        and any("EffectiveSettingsDep" in ast.unparse(a.annotation) for a in fn.args.args
                if a.annotation is not None)
    }
    assert effective == {"detector_capabilities", "list_waf_sources", "get_waf_source"}


def test_no_route_module_reads_a_switch_from_a_plain_settings_dependency() -> None:
    """Any route module that reads a switch itself must be one of the effective ones."""
    for path in (APP / "api" / "routes").glob("*.py"):
        text = path.read_text()
        tree = ast.parse(text)
        reads = _reads_a_switch(tree)
        uses_plain = any(
            isinstance(n, ast.Name) and n.id == "SettingsDep" for n in ast.walk(tree)
        )
        assert not (reads and uses_plain), path.name
