"""The QRadar access check (ACC-1): the catalog, the classification, the probes, the syncs.

No database and no console: the provider runs on an `httpx.MockTransport`, the session is a
recording stand-in, and the Ariel permit is the counting budget the permit tests use.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.cli import doctor as doctor_module
from app.cli.doctor import Doctor, Status
from app.core.config import Settings
from app.models.monitoring import QRadarAccessResult
from app.providers import access_catalog as catalog
from app.providers.base import ProviderAuthError, ProviderError, ProviderUnavailableError
from app.providers.qradar_rest import ACCESS_PROBE_AQL, QRadarRestProvider
from app.services import qradar_access as access
from app.services.ariel_permit import ArielPermitUnavailable
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.integration.ariel_permit_fakes import CountingBudget

PROVIDER_SOURCE = Path(__file__).resolve().parents[2] / "app" / "providers" / "qradar_rest.py"
SECRET_BODY = "BODY-MARKER-sec-token-4f7a9c"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the catalog
def _provider_paths() -> set[str]:
    """Every API path string `qradar_rest.py` builds, f-string holes shown as `{}`."""
    tree = ast.parse(PROVIDER_SOURCE.read_text(encoding="utf-8"))
    inside: set[int] = set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            inside.update(id(part) for part in node.values)
            text = "".join(p.value if isinstance(p, ast.Constant) else "{}" for p in node.values)
            if text.startswith(("/", "{}/")):
                found.add(text)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in inside
            and node.value.startswith("/")
            and " " not in node.value
        ):
            found.add(node.value)
    # The client's base path and root, not API calls.
    return found - {"/", "/api"}


def _normal(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def _catalog_paths() -> set[str]:
    return {_normal(call.split(" ", 1)[1]) for call in catalog.catalog_calls()}


def _is_catalogued(path: str, known: set[str]) -> bool:
    if path in known:
        return True
    # `base = "/config/.../custom_properties"` and `f"{base}/regex_properties"`.
    if any(item.startswith(path + "/") for item in known):
        return True
    return path.startswith("{}") and any(item.endswith(path[2:]) for item in known)


def test_every_path_the_provider_calls_is_in_the_catalog() -> None:
    known = _catalog_paths()
    missing = sorted(p for p in _provider_paths() if not _is_catalogued(p, known))
    assert missing == [], f"qradar_rest.py calls paths the access catalog does not list: {missing}"


def test_the_coverage_check_sees_a_dropped_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break-it: without the offenses entry the sweep above would fail."""
    trimmed = tuple(e for e in catalog.ACCESS_CATALOG if e.id != "offenses")
    monkeypatch.setattr(catalog, "ACCESS_CATALOG", trimmed)
    known = _catalog_paths()
    assert "/siem/offenses" in {p for p in _provider_paths() if not _is_catalogued(p, known)}


def test_every_probed_catalog_path_is_one_the_provider_calls() -> None:
    called = _provider_paths()
    for entry in catalog.ACCESS_CATALOG:
        assert _is_catalogued(_normal(entry.path), called) or any(
            p.endswith(_normal(entry.path).rsplit("/", 1)[1]) for p in called
        ), entry.id


def test_ids_are_unique_and_syncs_are_known() -> None:
    ids = [entry.id for entry in catalog.ACCESS_CATALOG]
    assert len(ids) == len(set(ids))
    assert access.FIRST_ENTRY_ID in ids
    for entry in catalog.ACCESS_CATALOG:
        assert set(entry.syncs) <= set(catalog.SYNC_NAMES), entry.id
        assert entry.feeds.endswith(".") and entry.used_by and entry.sources


def test_a_capability_is_never_shown_as_verified_without_a_name_and_a_source() -> None:
    for entry in catalog.ACCESS_CATALOG:
        assert entry.capability_verified == (entry.required_capability is not None), entry.id
        if entry.capability_verified:
            assert entry.sources, entry.id
        else:
            assert entry.required_capability is None, entry.id


def test_the_ariel_entry_covers_the_lifecycle_and_license_pool_sends_no_range() -> None:
    ariel = catalog.CATALOG_BY_ID["ariel_searches"]
    assert (ariel.method, ariel.path, ariel.probe) == ("POST", "/ariel/searches", "ariel")
    assert {f"{c.method} {c.path}" for c in ariel.covers} >= {
        "DELETE /ariel/searches/{search_id}"
    }
    assert catalog.CATALOG_BY_ID["license_pool"].range_header is None
    for entry in catalog.ACCESS_CATALOG:
        if entry.probe == "get" and entry.id not in ("system_about", "license_pool"):
            assert entry.range_header == "items=0-0", entry.id


# ------------------------------------------------------------ classification
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (200, "ok"),
        (204, "ok"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (429, "error"),
        (500, "error"),
        (503, "error"),
        (302, "error"),
        (422, "error"),
    ],
)
def test_classify_status(code: int, expected: str) -> None:
    assert access.classify_status(code) == expected


def test_classify_exception_uses_the_type_and_the_code_only() -> None:
    cls = access.classify_exception
    assert cls(ProviderAuthError("x", status_code=403)) == access.ProbeOutcome("forbidden", 403)
    assert cls(ProviderAuthError("x", status_code=401)) == access.ProbeOutcome(
        "unauthorized", 401
    )
    assert cls(ProviderError("x", status_code=404)) == access.ProbeOutcome("not_found", 404)
    assert cls(ProviderUnavailableError("x", status_code=503)) == access.ProbeOutcome(
        "error", 503
    )
    assert cls(ProviderUnavailableError("timed out")) == access.ProbeOutcome("unreachable", None)
    assert cls(TimeoutError()) == access.ProbeOutcome("unreachable", None)
    assert cls(ProviderError("odd")) == access.ProbeOutcome("error", None)
    assert cls(RuntimeError("boom")) == access.ProbeOutcome("error", None)
    # Nothing was sent: nothing is learned.
    assert cls(ArielPermitUnavailable(30.0, 3, 6)) is None


# ----------------------------------------------------------- the REST probes
class Wire:
    """A console that answers by path, records every request, and plants a body marker."""

    def __init__(self, answers: dict[str, int] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path.removeprefix('/api')}"
        if request.method == "POST":
            return httpx.Response(
                self.answers.get(key, 201),
                json={"search_id": "probe-1", "status": "WAIT", "note": SECRET_BODY},
            )
        if request.method == "DELETE":
            return httpx.Response(self.answers.get(key, 200), json={"note": SECRET_BODY})
        return httpx.Response(self.answers.get(key, 200), json={"note": SECRET_BODY})

    def provider(self, budget: CountingBudget | None = None) -> QRadarRestProvider:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler),
            base_url="https://qradar.example.internal/api",
            headers={"SEC": "test-token", "Version": "20.0"},
        )
        return QRadarRestProvider(
            base_url="https://qradar.example.internal",
            sec_token="test-token",
            client=client,
            permit=budget,  # type: ignore[arg-type]
        )

    def sent(self) -> list[str]:
        return [f"{r.method} {r.url.path.removeprefix('/api')}" for r in self.requests]


@pytest.mark.asyncio
async def test_probe_access_returns_the_status_and_asks_for_one_item() -> None:
    wire = Wire({"GET /siem/offenses": 403})
    provider = wire.provider()
    assert await provider.probe_access("/siem/offenses", range_header="items=0-0") == 403
    assert await provider.probe_access("/system/about") == 200
    assert wire.requests[0].headers["Range"] == "items=0-0"
    assert "Range" not in wire.requests[1].headers


@pytest.mark.asyncio
async def test_probe_access_turns_a_transport_failure_into_our_own_message() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate path /secret/ca.pem rejected")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(refuse), base_url="https://qradar.example.internal/api"
    )
    provider = QRadarRestProvider(
        base_url="https://qradar.example.internal", sec_token="t", client=client
    )
    with pytest.raises(ProviderUnavailableError) as raised:
        await provider.probe_access("/system/about")
    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_a_full_probe_sends_one_cheapest_read_per_entry_and_license_pool_no_range() -> None:
    wire = Wire()
    provider = wire.provider(CountingBudget())
    answers = await access.probe_entries(provider)
    assert {k: v.status for k, v in answers.items() if v} == {
        e.id: "ok" for e in catalog.ACCESS_CATALOG
    }
    by_path = {r.url.path.removeprefix("/api"): r for r in wire.requests if r.method == "GET"}
    assert by_path["/config/deployment/license_pool"].headers.get("Range") is None
    assert by_path["/siem/offenses"].headers["Range"] == "items=0-0"
    # No detail endpoint, no status poll and no results fetch.
    assert not any(r.url.path.endswith("/log_sources/1") for r in wire.requests)
    assert "GET /ariel/searches/probe-1" not in wire.sent()
    assert sum(1 for r in wire.requests if r.method == "POST") == 1


@pytest.mark.asyncio
async def test_the_ariel_probe_runs_under_one_permit_and_cancels_the_search() -> None:
    wire = Wire()
    budget = CountingBudget()
    provider = wire.provider(budget)
    assert await provider.probe_ariel_access() == 201
    assert wire.sent() == ["POST /ariel/searches", "DELETE /ariel/searches/probe-1"]
    assert wire.requests[0].url.params["query_expression"] == ACCESS_PROBE_AQL
    assert budget.acquired == 1 and budget.held == 0


@pytest.mark.asyncio
async def test_a_failed_cancel_does_not_hide_the_create() -> None:
    wire = Wire({"DELETE /ariel/searches/probe-1": 500})
    provider = wire.provider(CountingBudget())
    provider._max_retries = 0
    assert await provider.probe_ariel_access() == 201


@pytest.mark.asyncio
async def test_ariel_refusals_carry_their_status_and_nothing_is_cancelled() -> None:
    wire = Wire({"POST /ariel/searches": 403})
    with pytest.raises(ProviderAuthError) as raised:
        await wire.provider(CountingBudget()).probe_ariel_access()
    assert raised.value.status_code == 403
    assert wire.sent() == ["POST /ariel/searches"]


@pytest.mark.asyncio
async def test_without_a_permit_nothing_is_sent_and_the_entry_is_deferred() -> None:
    wire = Wire()
    budget = CountingBudget(refuse=True)
    answers = await access.probe_entries(wire.provider(budget))
    assert answers["ariel_searches"] is None
    assert "POST /ariel/searches" not in wire.sent()
    assert budget.refused == 1 and budget.held == 0
    assert answers["offenses"] == access.ProbeOutcome("ok", 200)


@pytest.mark.asyncio
async def test_a_console_that_does_not_answer_is_not_asked_the_rest() -> None:
    calls: list[str] = []

    class Silent:
        async def probe_access(self, path: str, *, range_header: str | None = None) -> int:
            calls.append(path)
            raise ProviderUnavailableError("Could not reach QRadar")

        async def probe_ariel_access(self) -> int:
            calls.append("ariel")
            raise AssertionError("not asked")

    answers = await access.probe_entries(Silent())
    assert calls == ["/system/about"]
    assert {o.status for o in answers.values() if o} == {"unreachable"}
    assert len(answers) == len(catalog.ACCESS_CATALOG)


@pytest.mark.asyncio
async def test_a_slow_probe_is_unreachable_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "PROBE_TIMEOUT_SECONDS", 0.01)

    class Slow:
        async def probe_access(self, path: str, *, range_header: str | None = None) -> int:
            await asyncio.sleep(1)
            return 200

        async def probe_ariel_access(self) -> int:
            return 201

    answers = await access.probe_entries(Slow(), ariel_timeout=0.01)
    assert answers["system_about"] == access.ProbeOutcome("unreachable", None)


# ------------------------------------------------- the store, the syncs, no body
class FakeSession:
    bind = None

    def __init__(self, rows: list[QRadarAccessResult] | None = None) -> None:
        self.rows = rows or []
        self.added: list[QRadarAccessResult] = []

    async def scalars(self, _statement: object) -> Any:
        rows = self.rows

        class Result:
            def all(self) -> list[QRadarAccessResult]:
                return rows

        return Result()

    def add(self, row: QRadarAccessResult) -> None:
        self.added.append(row)
        self.rows.append(row)

    async def flush(self) -> None:
        return None


class Dispatcher:
    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.started: list[str] = []
        self.refuse = refuse

    def start(self, sync: str, instance: object) -> bool:
        if sync in self.refuse:
            return False
        self.started.append(sync)
        return True


INSTANCE = SimpleNamespace(id=uuid.uuid4(), name="lab", provider_kind="rest")


def _row(entry_id: str, status: str, *, at: datetime = NOW - timedelta(hours=6)) -> Any:
    return QRadarAccessResult(
        instance_id=INSTANCE.id,
        entry_id=entry_id,
        status=status,
        http_status=None if status in ("unreachable",) else {"ok": 200, "forbidden": 403}.get(
            status, 500
        ),
        checked_at=at,
        previous_status=None,
        syncs_started=[],
    )


def _all_ok() -> dict[str, access.ProbeOutcome | None]:
    return {e.id: access.ProbeOutcome("ok", 200) for e in catalog.ACCESS_CATALOG}


async def _check(
    rows: list[Any],
    answers: dict[str, access.ProbeOutcome | None],
    dispatcher: Dispatcher,
) -> tuple[access.ConsoleCheck, FakeSession]:
    session = FakeSession(rows)

    async def probe(_provider: object) -> dict[str, access.ProbeOutcome | None]:
        return answers

    report = await access.check_console_access(
        session,  # type: ignore[arg-type]
        INSTANCE,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        dispatcher=dispatcher,
        now=lambda: NOW,
        probe=probe,
    )
    return report, session


@pytest.mark.asyncio
async def test_a_first_check_stores_every_entry_and_starts_nothing() -> None:
    dispatcher = Dispatcher()
    report, session = await _check([], _all_ok(), dispatcher)
    assert len(session.added) == len(catalog.ACCESS_CATALOG)
    assert {r.previous_status for r in session.added} == {None}
    assert {r.checked_at for r in session.added} == {NOW}
    assert dispatcher.started == [] and report.syncs_started == []


@pytest.mark.asyncio
async def test_an_entry_that_stays_ok_or_stays_refused_starts_nothing() -> None:
    dispatcher = Dispatcher()
    rows = [_row(e.id, "ok") for e in catalog.ACCESS_CATALOG]
    rows[0] = _row("system_about", "forbidden")
    answers = _all_ok()
    answers["system_about"] = access.ProbeOutcome("forbidden", 403)
    report, _ = await _check(rows, answers, dispatcher)
    assert dispatcher.started == [] and report.syncs_started == []
    stayed = next(r for r in rows if r.entry_id == "system_about")
    assert (stayed.status, stayed.previous_status) == ("forbidden", "forbidden")
    ok_row = next(r for r in rows if r.entry_id == "offenses")
    assert (ok_row.status, ok_row.previous_status, ok_row.syncs_started) == ("ok", "ok", [])


@pytest.mark.asyncio
async def test_not_ok_to_ok_starts_exactly_the_dependents_once_and_records_them() -> None:
    dispatcher = Dispatcher()
    rows = [_row(e.id, "ok") for e in catalog.ACCESS_CATALOG]
    for row in rows:
        if row.entry_id in ("log_sources", "log_source_types", "log_source_groups"):
            row.status, row.http_status = "forbidden", 403
        if row.entry_id == "offenses":
            row.status, row.http_status = "unreachable", None
        if row.entry_id == "custom_properties":
            row.status, row.http_status = "forbidden", 403  # no sync depends on it
    report, _ = await _check(rows, _all_ok(), dispatcher)

    assert dispatcher.started == ["log_source_inventory", "offense_snapshot"]
    assert report.syncs_started == ["log_source_inventory", "offense_snapshot"]
    by_entry = {r.entry_id: r for r in rows}
    assert by_entry["log_sources"].syncs_started == ["log_source_inventory"]
    assert by_entry["log_source_groups"].syncs_started == ["log_source_inventory"]
    assert by_entry["offenses"].syncs_started == ["offense_snapshot"]
    assert by_entry["custom_properties"].syncs_started == []
    assert by_entry["analytics_rules"].syncs_started == []
    assert by_entry["log_sources"].previous_status == "forbidden"
    assert by_entry["offenses"].previous_status == "unreachable"


@pytest.mark.asyncio
async def test_a_sync_the_queue_refused_is_not_recorded_as_started() -> None:
    dispatcher = Dispatcher(refuse=frozenset({"rule_inventory"}))
    rows = [_row(e.id, "ok") for e in catalog.ACCESS_CATALOG]
    for row in rows:
        if row.entry_id in ("analytics_rules", "offenses"):
            row.status, row.http_status = "forbidden", 403
    report, _ = await _check(rows, _all_ok(), dispatcher)
    assert report.syncs_started == ["offense_snapshot"]
    assert next(r for r in rows if r.entry_id == "analytics_rules").syncs_started == []


@pytest.mark.asyncio
async def test_a_deferred_entry_leaves_its_stored_row_alone() -> None:
    dispatcher = Dispatcher()
    rows = [_row(e.id, "ok") for e in catalog.ACCESS_CATALOG]
    ariel = next(r for r in rows if r.entry_id == "ariel_searches")
    ariel.status, ariel.http_status = "forbidden", 403
    before = ariel.checked_at
    answers = _all_ok()
    answers["ariel_searches"] = None
    report, _ = await _check(rows, answers, dispatcher)
    assert report.deferred == ["ariel_searches"]
    assert (ariel.status, ariel.checked_at) == ("forbidden", before)
    assert dispatcher.started == []


@pytest.mark.asyncio
async def test_a_check_already_running_sends_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Held:
        def __init__(self, *args: object, **kwargs: object) -> None: ...

        async def __aenter__(self) -> bool:
            return False

        async def __aexit__(self, *exc: object) -> None: ...

    monkeypatch.setattr(access, "CollectorAdvisoryLock", Held)
    dispatcher = Dispatcher()
    report, session = await _check([], _all_ok(), dispatcher)
    assert report.ran is False and session.added == []


@pytest.mark.asyncio
async def test_no_response_body_is_stored_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    wire = Wire(
        {"GET /siem/offenses": 403, "GET /analytics/rules": 500, "POST /ariel/searches": 403}
    )
    provider = wire.provider(CountingBudget())
    session = FakeSession([])
    with caplog.at_level(logging.DEBUG):
        await access.check_console_access(
            session,  # type: ignore[arg-type]
            INSTANCE,  # type: ignore[arg-type]
            provider,
            settings=Settings(_env_file=None),
            dispatcher=Dispatcher(),
            now=lambda: NOW,
        )
    assert len(session.added) == len(catalog.ACCESS_CATALOG)
    stored = " ".join(
        f"{r.entry_id} {r.status} {r.http_status} {r.previous_status} {r.syncs_started}"
        for r in session.added
    )
    assert SECRET_BODY not in stored and "sec-token" not in stored
    assert SECRET_BODY not in caplog.text
    assert any(r.entry_id == "offenses" and r.status == "forbidden" for r in session.added)
    assert any(r.entry_id == "analytics_rules" and r.status == "error" for r in session.added)
    column_types = {c.name for c in QRadarAccessResult.__table__.columns}
    assert not any(name in column_types for name in ("body", "response", "payload", "error"))


# ----------------------------------------------------------- the stored view
def test_the_view_joins_the_catalog_and_names_the_latest_checks_syncs() -> None:
    older = NOW - timedelta(hours=6)
    rows = [_row(e.id, "ok", at=NOW) for e in catalog.ACCESS_CATALOG[:-1]]
    rows[0].syncs_started = ["health_cache"]
    stale = _row("offenses", "forbidden", at=older)
    stale.syncs_started = ["offense_snapshot"]
    rows = [r for r in rows if r.entry_id != "offenses"] + [stale]
    view = access.console_view(INSTANCE, rows)  # type: ignore[arg-type]
    assert [e.id for e in view.entries] == [e.id for e in catalog.ACCESS_CATALOG]
    assert view.last_checked_at == NOW
    assert view.syncs_started == ["health_cache"]
    unchecked = next(e for e in view.entries if e.id == "ariel_searches")
    assert unchecked.status is None and unchecked.checked_at is None
    offenses = next(e for e in view.entries if e.id == "offenses")
    assert offenses.syncs_started == [] and offenses.status == "forbidden"
    assert offenses.required_capability is None and offenses.capability_verified is False
    assert offenses.capability_hint == "Offenses"


# --------------------------------------------------- settings, beat, the task
def test_the_task_is_registered_and_scheduled_every_six_hours() -> None:
    assert Settings(_env_file=None).qradar_access_check_interval_seconds == 21600
    assert Settings(_env_file=None).rate_limit_access_check == "2/minute"
    entry = celery_app.conf.beat_schedule["check-qradar-access"]
    assert entry["task"] == "check_qradar_access" and entry["schedule"] == 21600.0
    assert tasks.check_qradar_access_task.name == "check_qradar_access"
    assert "check_qradar_access" in celery_app.tasks


def test_the_check_is_sent_when_a_worker_is_ready_and_a_dead_broker_is_survived(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from celery.signals import beat_init, worker_ready

    from app.workers import celery_app as celery_module

    sent: list[str] = []
    monkeypatch.setattr(celery_module.celery_app, "send_task", lambda name: sent.append(name))
    worker_ready.send(sender=None)
    assert sent == ["check_qradar_access"]
    # The beat-start signal keeps sending exactly the two tasks other tests pin.
    sent.clear()
    beat_init.send(sender=None)
    assert "check_qradar_access" not in sent

    def boom(name: str) -> None:
        raise OSError("broker down")

    monkeypatch.setattr(celery_module.celery_app, "send_task", boom)
    with caplog.at_level(logging.WARNING):
        responses = worker_ready.send(sender=None)
    assert all(not isinstance(r[1], Exception) for r in responses)
    assert "could not queue check_qradar_access" in caplog.text


@pytest.mark.asyncio
async def test_the_task_skips_a_console_on_another_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_for_each(run, instance_name=None, **kwargs):  # type: ignore[no-untyped-def]
        mock = SimpleNamespace(provider_kind="mock", name="m")
        return await run(None, mock, None)

    monkeypatch.setattr(tasks, "_for_each_instance", fake_for_each)
    assert await tasks.check_qradar_access() == {"status": "not_applicable"}


def test_the_celery_dispatcher_queues_by_console_name_and_reports_a_refused_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[tuple[str, list[str]]] = []

    def record(name: str):  # type: ignore[no-untyped-def]
        def apply_async(*, args: list[str]) -> None:
            queued.append((name, args))

        return apply_async

    monkeypatch.setattr(tasks.sync_log_sources_task, "apply_async", record("logs"))
    monkeypatch.setattr(tasks.sync_rule_inventory_task, "apply_async", record("rules"))
    monkeypatch.setattr(tasks.collect_offenses_task, "apply_async", record("offenses"))
    monkeypatch.setattr(tasks.collect_metrics_task, "apply_async", record("metrics"))
    dispatcher = access.CeleryDispatcher()
    for sync in ("log_source_inventory", "rule_inventory", "offense_snapshot"):
        assert dispatcher.start(sync, INSTANCE) is True  # type: ignore[arg-type]
    assert queued == [("logs", ["lab"]), ("rules", ["lab"]), ("offenses", ["lab"])]

    def broker_down(*, args: list[str]) -> None:
        raise ConnectionError("redis://:hunter2@broker refused")

    monkeypatch.setattr(tasks.collect_metrics_task, "apply_async", broker_down)
    assert dispatcher.start("log_source_metrics", INSTANCE) is False  # type: ignore[arg-type]
    assert dispatcher.start("not_a_sync", INSTANCE) is False  # type: ignore[arg-type]


# ------------------------------------------------------------------- doctor
class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _DoctorSession:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _DoctorSession:
        return self

    async def __aexit__(self, *exc: object) -> None: ...

    async def scalars(self, _statement: object) -> _Rows:
        return _Rows(self.rows)


def _doctor(rows: list[Any], instances: list[Any] | None = None) -> Doctor:
    doctor = Doctor(
        settings=Settings(_env_file=None),
        sessionmaker=lambda: _DoctorSession(rows),  # type: ignore[arg-type]
        now=lambda: NOW,
    )

    async def enabled() -> list[Any]:
        return [INSTANCE] if instances is None else instances

    doctor._enabled_instances = enabled  # type: ignore[method-assign]
    return doctor


def _stored(statuses: dict[str, str] | None = None, *, at: datetime = NOW) -> list[Any]:
    statuses = statuses or {}
    return [_row(e.id, statuses.get(e.id, "ok"), at=at) for e in catalog.ACCESS_CATALOG]


def test_the_doctor_check_sits_before_qradar_health_and_has_a_runbook() -> None:
    ids = [check_id for check_id, _ in doctor_module._CHECK_RUNBOOKS]
    assert ids.index("qradar_access") == ids.index("qradar_health") - 1
    assert ("qradar_access", "OPS-R71") in doctor_module._CHECK_RUNBOOKS


@pytest.mark.asyncio
async def test_doctor_passes_when_every_api_answers() -> None:
    result = await _doctor(_stored()).check_qradar_access()
    assert result.status is Status.PASS
    assert f"{len(catalog.ACCESS_CATALOG)} APIs reachable" in result.message


@pytest.mark.asyncio
async def test_doctor_warns_naming_a_refused_api_and_what_it_feeds() -> None:
    result = await _doctor(
        _stored({"offenses": "forbidden", "analytics_rules": "not_found"})
    ).check_qradar_access()
    assert result.status is Status.WARN and result.runbook == "OPS-R71"
    assert "forbidden Offenses (feeds: The offense snapshots behind the Offenses page" in (
        result.message
    )
    assert "not found Analytics rules (feeds: The rule inventory" in result.message


@pytest.mark.asyncio
async def test_doctor_fails_when_the_token_itself_is_not_accepted() -> None:
    result = await _doctor(_stored({"system_about": "unauthorized"})).check_qradar_access()
    assert result.status is Status.FAIL
    assert "not accepted (401)" in result.message and "system_about" in result.message


@pytest.mark.asyncio
async def test_doctor_warns_for_apis_that_did_not_answer_and_for_a_stale_result() -> None:
    silent = await _doctor(_stored({"offenses": "unreachable"})).check_qradar_access()
    assert silent.status is Status.WARN and "offenses (unreachable)" in silent.message
    old = await _doctor(_stored(at=NOW - timedelta(hours=13))).check_qradar_access()
    assert old.status is Status.WARN and "older than twice the check interval" in old.message


@pytest.mark.asyncio
async def test_doctor_says_not_checked_yet_and_skips_without_a_rest_console() -> None:
    none_yet = await _doctor([]).check_qradar_access()
    assert none_yet.status is Status.WARN and "lab: not checked yet" in none_yet.message
    partial = await _doctor(_stored()[:3]).check_qradar_access()
    assert partial.status is Status.WARN and "not checked yet" in partial.message
    mock = SimpleNamespace(id=uuid.uuid4(), name="m", provider_kind="mock")
    skipped = await _doctor([], [mock]).check_qradar_access()
    assert skipped.status is Status.SKIP


# --------------------------------------------------------------------- the docs
DOCS = Path(__file__).resolve().parents[3] / "docs"


def test_install_lists_every_catalog_api_and_the_runbook_exists() -> None:
    install = (DOCS / "INSTALL.md").read_text(encoding="utf-8")
    section = install[install.index("What the token must be allowed to read") :]
    section = section[: section.index("## Register a console manually")]
    for entry in catalog.ACCESS_CATALOG:
        assert f"| `{entry.path}` |" in section, entry.id
        assert entry.feeds in section, entry.id
    operations = (DOCS / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "| `qradar_access` |" in operations and "### OPS-R71." in operations
    api = (DOCS / "API.md").read_text(encoding="utf-8")
    assert "GET /api/v1/admin/qradar-access" in api
    assert "POST /api/v1/admin/qradar-access/check" in api


def test_the_probe_query_passes_the_stored_query_validator_under_default_settings() -> None:
    """Rule 4: only stored, validated queries run. The probe is one of them."""
    from app.services.aql_validator import validate_aql

    validated = validate_aql(ACCESS_PROBE_AQL, settings=Settings(_env_file=None))
    assert validated.datasets == ["EVENTS"] and validated.has_time_window
