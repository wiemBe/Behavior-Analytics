"""The QRadar access check through the API and the task, with a database (ACC-1).

A scripted provider stands in for the console: the check, the stored rows, the syncs it starts,
the audit row, the rate limit and the guard run for real. DB-gated.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from sqlalchemy import func, select, update

from app.core.rate_limit import limiter
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.monitoring import QRadarAccessResult, QRadarAccessRun
from app.providers import access_catalog as catalog
from app.providers.base import ProviderAuthError
from app.services.qradar_access import ACCESS_RUN_STALE_SECONDS
from app.workers import tasks
from tests.integration.test_fleet_api import ADMIN, NOTHING, VIEWER, Client, audit_rows, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

BASE = "/api/v1/admin/qradar-access"
BODY = "BODY-MARKER-9d21c0"


class Scripted:
    """The console as the check sees it: a status per path, the Ariel create status apart."""

    refused: ClassVar[dict[str, int]] = {}
    ariel: ClassVar[int] = 201
    probes: ClassVar[list[str]] = []
    #: The check tasks the API queued: (console name, claimed).
    enqueued: ClassVar[list[tuple[str, bool]]] = []

    async def probe_access(self, path: str, *, range_header: str | None = None) -> int:
        Scripted.probes.append(path)
        return Scripted.refused.get(path, 200)

    async def probe_ariel_access(self) -> int:
        Scripted.probes.append("ariel")
        if Scripted.ariel in (401, 403):
            raise ProviderAuthError(BODY, status_code=Scripted.ariel)
        return Scripted.ariel

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _scripted(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, list[str]]]]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    limiter.reset()
    Scripted.refused, Scripted.ariel, Scripted.probes, Scripted.enqueued = {}, 201, [], []

    def maker():  # type: ignore[no-untyped-def]
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    # The task runs here, in the test, on its own sessions; the request never builds a provider.
    monkeypatch.setattr(tasks, "get_sessionmaker", maker)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: Scripted())
    queued: list[tuple[str, list[str]]] = []

    def recorder(name: str):  # type: ignore[no-untyped-def]
        def apply_async(*, args: list[str]) -> None:
            queued.append((name, args))

        return apply_async

    def enqueue_check(*, args: list) -> None:  # type: ignore[type-arg]
        Scripted.enqueued.append((args[0], args[1]))

    monkeypatch.setattr(tasks.check_qradar_access_task, "apply_async", enqueue_check)
    monkeypatch.setattr(tasks.sync_log_sources_task, "apply_async", recorder("log_sources"))
    monkeypatch.setattr(tasks.sync_rule_inventory_task, "apply_async", recorder("rules"))
    monkeypatch.setattr(tasks.collect_offenses_task, "apply_async", recorder("offenses"))
    monkeypatch.setattr(tasks.collect_metrics_task, "apply_async", recorder("metrics"))
    yield queued
    limiter.reset()


def _console(name: str = "lab", *, kind: str = "rest", enabled: bool = True) -> str:
    async def _add(session):  # type: ignore[no-untyped-def]
        row = QRadarInstance(
            name=name,
            console_host="https://qradar.example.internal",
            provider_kind=kind,
            sec_token="test-token",
            enabled=enabled,
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    return run(_add)


def _stored() -> dict[tuple[str, str], QRadarAccessResult]:
    async def _q(session):  # type: ignore[no-untyped-def]
        rows = (await session.scalars(select(QRadarAccessResult))).all()
        return {(str(r.instance_id), r.entry_id): r for r in rows}

    return run(_q)


def _entry(body: dict, console: int, entry_id: str) -> dict:
    return next(e for e in body["consoles"][console]["entries"] if e["id"] == entry_id)


def _drain() -> None:
    """Run the check tasks the API queued, as a worker would."""
    jobs, Scripted.enqueued = Scripted.enqueued, []
    for name, claimed in jobs:
        asyncio.run(tasks.check_qradar_access(name, claimed))


def _check(admin: Client) -> dict:
    """POST /check, let the worker finish, and return what GET shows."""
    posted = admin.post(f"{BASE}/check")
    assert posted.status_code == 202, posted.text
    _drain()
    return admin.get(BASE).json()


def _set_run(started_ago: int, finished: bool) -> None:
    async def _go(session):  # type: ignore[no-untyped-def]
        started = datetime.now(UTC) - timedelta(seconds=started_ago)
        await session.execute(
            update(QRadarAccessRun).values(
                started_at=started, finished_at=started if finished else None
            )
        )

    run(_go)


def test_the_guard_is_admin_only_and_a_refusal_sends_nothing(db_schema) -> None:
    _console()
    for principal in (VIEWER, NOTHING):
        client = Client(principal)
        assert client.get(BASE).status_code == 403
        assert client.post(f"{BASE}/check").status_code == 403
    assert Scripted.probes == [] and Scripted.enqueued == [] and _stored() == {}
    assert Client(ADMIN).get(BASE).status_code == 200


def test_before_any_check_every_entry_says_so_and_nothing_is_a_zero(db_schema) -> None:
    _console()
    body = Client(ADMIN).get(BASE).json()
    assert body["check_interval_seconds"] == 21600
    [console] = body["consoles"]
    assert console["applicable"] is True and console["last_checked_at"] is None
    assert console["check_running"] is False
    assert console["last_check_started_at"] is None and console["last_check_finished_at"] is None
    assert [e["id"] for e in console["entries"]] == [e.id for e in catalog.ACCESS_CATALOG]
    assert all(e["status"] is None and e["http_status"] is None for e in console["entries"])
    assert all(e["capability_verified"] is False for e in console["entries"])
    assert Scripted.probes == []  # the GET never reaches the console


def test_post_queues_the_task_answers_202_and_never_probes_in_the_request(db_schema) -> None:
    _console()
    admin = Client(ADMIN)
    posted = admin.post(f"{BASE}/check")
    assert posted.status_code == 202, posted.text
    body = posted.json()
    assert body["started"] == ["lab"] and body["already_running"] == [] and body["failed"] == []
    [console] = body["consoles"]
    assert console["check_running"] is True
    assert console["last_check_started_at"] is not None
    assert console["last_check_finished_at"] is None
    assert all(e["status"] is None for e in console["entries"])  # nothing stored yet
    assert Scripted.probes == [] and _stored() == {}  # no inline probe in the request
    assert Scripted.enqueued == [("lab", True)]
    [entry] = audit_rows("qradar_access.check")
    assert entry.actor == "admin" and entry.outcome == "SUCCESS"
    assert entry.detail["started"] == ["lab"]

    _drain()
    done = admin.get(BASE).json()["consoles"][0]
    assert done["check_running"] is False and done["last_check_finished_at"] is not None
    assert done["last_check_started_at"] <= done["last_check_finished_at"]
    assert len(_stored()) == len(catalog.ACCESS_CATALOG)


def test_a_second_post_while_a_check_runs_queues_nothing_and_names_the_running_one(
    db_schema,
) -> None:
    _console()
    admin = Client(ADMIN)
    first = admin.post(f"{BASE}/check")
    second = admin.post(f"{BASE}/check")
    assert first.status_code == second.status_code == 202
    assert second.json()["started"] == [] and second.json()["already_running"] == ["lab"]
    assert second.json()["consoles"][0]["check_running"] is True
    assert Scripted.enqueued == [("lab", True)]
    # Once the run finished, the next post starts a new one.
    _drain()
    third = admin.post(f"{BASE}/check")
    assert third.status_code == 429  # the two requests above used the minute's allowance
    limiter.reset()
    again = admin.post(f"{BASE}/check")
    assert again.json()["started"] == ["lab"]


def test_a_run_that_never_finished_stops_counting_as_running_after_the_bound(db_schema) -> None:
    _console()
    admin = Client(ADMIN)
    admin.post(f"{BASE}/check")
    Scripted.enqueued = []
    _set_run(ACCESS_RUN_STALE_SECONDS - 60, finished=False)
    assert admin.get(BASE).json()["consoles"][0]["check_running"] is True
    _set_run(ACCESS_RUN_STALE_SECONDS + 60, finished=False)  # its worker died
    assert admin.get(BASE).json()["consoles"][0]["check_running"] is False
    again = admin.post(f"{BASE}/check")
    assert again.json()["started"] == ["lab"] and again.json()["already_running"] == []
    assert Scripted.enqueued == [("lab", True)]


def test_a_queue_that_refuses_the_task_releases_the_run(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    _console()

    def refuse(*, args: list) -> None:  # type: ignore[type-arg]
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.check_qradar_access_task, "apply_async", refuse)
    admin = Client(ADMIN)
    body = admin.post(f"{BASE}/check").json()
    assert body["failed"] == ["lab"] and body["started"] == []
    assert admin.get(BASE).json()["consoles"][0]["check_running"] is False
    [entry] = audit_rows("qradar_access.check")
    assert entry.outcome == "FAILURE"


def test_check_now_stores_each_entry_and_the_get_returns_the_same_shape(db_schema) -> None:
    console_id = _console()
    Scripted.refused = {"/siem/offenses": 403, "/analytics/rules": 404}
    Scripted.ariel = 403
    body = _check(Client(ADMIN))
    assert (_entry(body, 0, "offenses")["status"], _entry(body, 0, "offenses")["http_status"]) == (
        "forbidden",
        403,
    )
    assert _entry(body, 0, "analytics_rules")["status"] == "not_found"
    assert _entry(body, 0, "ariel_searches")["status"] == "forbidden"
    assert _entry(body, 0, "log_sources")["status"] == "ok"
    assert _entry(body, 0, "offenses")["feeds"].startswith("The offense snapshots")
    assert _entry(body, 0, "offenses")["syncs_started"] == []
    stored = _stored()
    assert len(stored) == len(catalog.ACCESS_CATALOG)
    assert stored[(console_id, "offenses")].previous_status is None
    [entry] = audit_rows("qradar_access.check")
    assert BODY not in str(entry.detail)


def test_access_returning_starts_the_dependents_once_and_the_response_names_them(
    db_schema, _scripted
) -> None:
    queued = _scripted
    console_id = _console()
    Scripted.refused = {
        "/siem/offenses": 403,
        "/config/event_sources/log_source_management/log_sources": 403,
        "/config/event_sources/log_source_management/log_source_types": 403,
    }
    admin = Client(ADMIN)

    def check() -> dict:
        limiter.reset()  # four checks in a row: the rate limit has its own test
        return _check(admin)

    assert check()["consoles"][0]["syncs_started"] == []
    assert queued == []  # nothing runs for what is still refused, or on a first check

    Scripted.refused = {"/siem/offenses": 403}
    second = check()
    assert second["consoles"][0]["syncs_started"] == ["log_source_inventory"]
    assert _entry(second, 0, "log_sources")["syncs_started"] == ["log_source_inventory"]
    assert _entry(second, 0, "log_sources")["previous_status"] == "forbidden"
    assert _entry(second, 0, "offenses")["syncs_started"] == []
    assert queued == [("log_sources", ["lab"])]  # three entries, one sync

    third = check()
    assert third["consoles"][0]["syncs_started"] == []
    assert _entry(third, 0, "log_sources")["previous_status"] == "ok"
    assert queued == [("log_sources", ["lab"])]

    Scripted.refused = {}
    fourth = check()
    assert fourth["consoles"][0]["syncs_started"] == ["offense_snapshot"]
    assert queued[-1] == ("offenses", ["lab"]) and len(queued) == 2
    assert _stored()[(console_id, "offenses")].syncs_started == ["offense_snapshot"]


def test_check_now_is_rate_limited_and_a_deduped_post_counts(db_schema) -> None:
    _console()
    admin = Client(ADMIN)
    assert admin.post(f"{BASE}/check").status_code == 202
    assert admin.post(f"{BASE}/check").json()["already_running"] == ["lab"]
    third = admin.post(f"{BASE}/check")
    assert third.status_code == 429
    assert Scripted.enqueued == [("lab", True)]
    assert admin.get(BASE).status_code == 200  # the read is not the limited action


def test_only_enabled_rest_consoles_are_queued_and_the_read_cost_is_constant(db_schema) -> None:
    _console("lab")
    _console("sim", kind="mock")
    _console("off", enabled=False)
    admin = Client(ADMIN)
    posted = admin.post(f"{BASE}/check").json()
    assert posted["started"] == ["lab"]
    assert Scripted.enqueued == [("lab", True)]
    _drain()
    body = admin.get(BASE).json()
    assert [(c["instance_name"], c["applicable"]) for c in body["consoles"]] == [
        ("lab", True),
        ("sim", False),
    ]
    assert Scripted.probes.count("/system/about") == 1
    assert all(
        e["status"] is None for e in body["consoles"][1]["entries"]
    )  # nothing was checked on the mock console

    admin.get(BASE)
    two_consoles = admin.statements
    _console("lab2")
    admin.get(BASE)
    assert admin.statements == two_consoles


def test_a_console_without_a_token_ends_its_run_and_stores_nothing(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    _console()

    def no_token(*args: object, **kwargs: object) -> object:
        raise ValueError("QRadar instance 'lab' has no stored SEC token")

    monkeypatch.setattr(tasks, "build_provider_for_instance", no_token)
    admin = Client(ADMIN)
    body = _check(admin)
    assert _stored() == {}
    assert body["consoles"][0]["check_running"] is False
    assert body["consoles"][0]["last_check_finished_at"] is not None


def test_a_start_up_or_beat_run_is_recorded_and_skips_a_console_already_running(
    db_schema,
) -> None:
    _console()
    first = asyncio.run(tasks.check_qradar_access())
    assert first["results"][0]["status"] == "ok"
    admin = Client(ADMIN)
    done = admin.get(BASE).json()["consoles"][0]
    assert done["check_running"] is False and done["last_check_finished_at"] is not None
    admin.post(f"{BASE}/check")  # now a run is recorded as started
    Scripted.probes.clear()
    second = asyncio.run(tasks.check_qradar_access())
    assert second["results"][0]["status"] == "skipped_running"
    assert Scripted.probes == []


@pytest.mark.asyncio
async def test_the_scheduled_task_checks_every_rest_console_and_stores_codes_only(
    db_schema, monkeypatch: pytest.MonkeyPatch, _scripted
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    def maker():  # type: ignore[no-untyped-def]
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    async with maker()() as session:
        session.add(
            QRadarInstance(
                name="lab", console_host="https://q.example", provider_kind="rest",
                sec_token="t",
            )
        )
        session.add(QRadarInstance(name="sim", console_host="mock", provider_kind="mock"))
        await session.commit()

    Scripted.refused = {"/siem/offenses": 403}
    result = await tasks.check_qradar_access()
    by_name = {r["instance"]: r for r in result["results"]}
    assert by_name["sim"]["status"] == "not_applicable"
    assert by_name["lab"]["status"] == "ok" and by_name["lab"]["not_ok"] == ["offenses"]

    async with maker()() as session:
        rows = (await session.scalars(select(QRadarAccessResult))).all()
        assert len(rows) == len(catalog.ACCESS_CATALOG)
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert {r.http_status for r in rows} <= {200, 201, 403}
        assert BODY not in " ".join(f"{r.status}{r.syncs_started}" for r in rows)
