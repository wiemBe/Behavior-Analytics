"""A manual search run is queued, not held in the request (SCALE-2h2, D-87).

DB-gated. The route answers 202 with a QUEUED row and builds no provider; a second run is a 409
while one is queued or running and younger than the search's timeout plus two minutes, and a
stale one is closed as abandoned. The task runs the existing executor on the queued row, then the
alert evaluator, and never leaves the row QUEUED or RUNNING.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.models.enums import ExecutionStatus
from app.models.identity import AuditLog
from app.models.search import SearchExecution
from app.workers import tasks
from tests.integration.ariel_permit_fakes import CountingBudget
from tests.integration.manual_run_support import capture_queue, run_queued
from tests.integration.test_search_manual_run_api import _client, _create, _Provider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run search integration tests",
    ),
]


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


def _sql(db_url: str, work):  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        engine = create_async_engine(db_url, future=True, poolclass=NullPool)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _rows(db_url: str, search_id: str) -> list[dict]:
    async def read(session):  # type: ignore[no-untyped-def]
        found = await session.scalars(
            select(SearchExecution).where(SearchExecution.search_id == uuid.UUID(search_id))
        )
        return [
            {
                "id": str(row.id),
                "status": str(row.status),
                "error_type": row.error_type,
                "code": row.error_message_code,
                "trigger": row.trigger,
            }
            for row in found
        ]

    return _sql(db_url, read)


def _age(db_url: str, execution_id: str, *, seconds: int, status: ExecutionStatus) -> None:
    async def write(session):  # type: ignore[no-untyped-def]
        row = await session.get(SearchExecution, uuid.UUID(execution_id))
        row.created_at = datetime.now(UTC) - timedelta(seconds=seconds)
        row.started_at = row.created_at if status == ExecutionStatus.RUNNING else None
        row.status = status

    _sql(db_url, write)


def _forbid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("the request must not build a provider")

    monkeypatch.setattr("app.providers.factory.build_provider", forbidden)
    monkeypatch.setattr("app.providers.factory.get_provider", forbidden)
    monkeypatch.setattr(tasks, "build_provider", forbidden)


class _Evaluator:
    calls: list[str] = []  # noqa: RUF012 - reset per test

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    async def evaluate(self, search, execution):  # type: ignore[no-untyped-def]
        _Evaluator.calls.append(str(execution.status))


@pytest.fixture
def evaluator(monkeypatch: pytest.MonkeyPatch) -> type[_Evaluator]:
    _Evaluator.calls = []
    monkeypatch.setattr(tasks, "SearchAlertEvaluator", _Evaluator)
    return _Evaluator


def test_the_route_queues_builds_no_provider_and_audits_it(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    _forbid_provider(monkeypatch)

    response = client.post(f"/api/v1/searches/{search_id}/run")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["trigger"] == "MANUAL" and body["triggered_by"] == "admin"
    assert body["started_at"] is None and body["completed_at"] is None
    queued_at = datetime.fromisoformat(body["created_at"])
    assert abs((datetime.now(UTC) - queued_at).total_seconds()) < 30
    listed = client.get(f"/api/v1/searches/{search_id}/executions").json()
    assert listed[0]["created_at"] == body["created_at"], "a reload shows the same queue time"
    assert queued == [body["id"]]

    async def audits(session):  # type: ignore[no-untyped-def]
        rows = await session.scalars(select(AuditLog).where(AuditLog.action == "search.execute"))
        return [row.detail for row in rows]

    details = _sql(db_schema, audits)
    assert len(details) == 1 and details[0]["status"] == "QUEUED"


@pytest.mark.parametrize("state", [ExecutionStatus.QUEUED, ExecutionStatus.RUNNING])
def test_a_second_run_while_one_is_open_is_a_coded_409_naming_the_time(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, state: ExecutionStatus
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    first = client.post(f"/api/v1/searches/{search_id}/run").json()
    _age(db_schema, first["id"], seconds=30, status=state)

    refused = client.post(f"/api/v1/searches/{search_id}/run")

    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["detail_code"] == "search.run.conflict.running"
    since = datetime.fromisoformat(body["detail_params"]["since"])
    assert abs((datetime.now(UTC) - since).total_seconds() - 30) < 20
    assert queued == [first["id"]], "the refused run queued nothing"
    assert len(_rows(db_schema, search_id)) == 1


def test_a_finished_run_does_not_block_the_next(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    first = client.post(f"/api/v1/searches/{search_id}/run").json()
    run_queued(
        db_schema, monkeypatch, [first["id"]], provider=_Provider(), budget=CountingBudget()
    )
    _age(db_schema, first["id"], seconds=5, status=ExecutionStatus.COMPLETED)

    again = client.post(f"/api/v1/searches/{search_id}/run")

    assert again.status_code == 202, again.text
    assert len(queued) == 2


@pytest.mark.parametrize("state", [ExecutionStatus.QUEUED, ExecutionStatus.RUNNING])
def test_a_stale_open_run_is_closed_as_abandoned_and_a_new_run_is_allowed(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, state: ExecutionStatus
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    first = client.post(f"/api/v1/searches/{search_id}/run").json()
    timeout = client.get(f"/api/v1/searches/{search_id}").json()["timeout_seconds"]
    window = min(timeout, get_settings().ariel_max_timeout_seconds) + 120
    _age(db_schema, first["id"], seconds=window + 5, status=state)

    again = client.post(f"/api/v1/searches/{search_id}/run")

    assert again.status_code == 202, again.text
    by_id = {row["id"]: row for row in _rows(db_schema, search_id)}
    assert by_id[first["id"]]["status"] == "FAILED"
    assert by_id[first["id"]]["error_type"] == "ABANDONED"
    assert by_id[first["id"]]["code"] == "search.run.abandoned"
    assert by_id[again.json()["id"]]["status"] == "QUEUED"
    assert len(queued) == 2


def test_a_run_inside_the_window_is_not_reaped(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    capture_queue(monkeypatch)
    first = client.post(f"/api/v1/searches/{search_id}/run").json()
    timeout = client.get(f"/api/v1/searches/{search_id}").json()["timeout_seconds"]
    window = min(timeout, get_settings().ariel_max_timeout_seconds) + 120
    _age(db_schema, first["id"], seconds=window - 30, status=ExecutionStatus.QUEUED)

    assert client.post(f"/api/v1/searches/{search_id}/run").status_code == 409
    assert _rows(db_schema, search_id)[0]["status"] == "QUEUED"


def test_the_task_completes_the_row_and_evaluates_alerts_once(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, evaluator: type[_Evaluator]
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")
    provider = _Provider()
    budget = CountingBudget()

    outcome = run_queued(db_schema, monkeypatch, queued, provider=provider, budget=budget)

    assert outcome == [{"status": "done", "execution_id": queued[0], "outcome": "COMPLETED"}]
    assert evaluator.calls == ["COMPLETED"]
    assert provider.creates == 1 and budget.held == 0
    assert [row["status"] for row in _rows(db_schema, search_id)] == ["COMPLETED"]


def test_a_capacity_refusal_is_failed_capacity_and_still_evaluated(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, evaluator: type[_Evaluator]
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")
    provider = _Provider()

    run_queued(
        db_schema, monkeypatch, queued, provider=provider, budget=CountingBudget(refuse=True)
    )

    (row,) = _rows(db_schema, search_id)
    assert row["status"] == "FAILED" and row["error_type"] == "CAPACITY"
    assert provider.creates == 0
    assert evaluator.calls == ["FAILED"]


class _Closing(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def test_the_provider_is_closed_after_a_run(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, evaluator: type[_Evaluator]
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")
    provider = _Closing()

    run_queued(db_schema, monkeypatch, queued, provider=provider, budget=CountingBudget())

    assert provider.closed == 1


def test_an_exception_fails_the_row_closes_the_provider_and_is_never_left_open(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, evaluator: type[_Evaluator]
) -> None:
    class Boom(RuntimeError):
        pass

    class Broken(_Closing):
        async def create_ariel_search(self, aql: str):  # type: ignore[no-untyped-def]
            raise Boom("secret AQL text must not be stored")

    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")
    provider = Broken()

    def exploding_executor(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise Boom("executor could not start")

    monkeypatch.setattr(tasks, "SearchExecutor", exploding_executor)
    outcome = run_queued(db_schema, monkeypatch, queued, provider=provider, budget=CountingBudget())

    assert outcome[0]["status"] == "failed" and outcome[0]["error_class"] == "Boom"
    (row,) = _rows(db_schema, search_id)
    assert row["status"] == "FAILED"
    assert row["error_type"] == "Boom"
    assert row["code"] == "search.run.worker_failed"
    assert provider.closed == 1
    assert evaluator.calls == []

    async def stored_text(session):  # type: ignore[no-untyped-def]
        return await session.scalar(select(SearchExecution.error_message))

    assert "secret" not in (_sql(db_schema, stored_text) or "")


def test_a_row_the_executor_left_open_by_a_late_failure_is_still_closed(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, evaluator: type[_Evaluator]
) -> None:
    """The executor flushes RUNNING and then something outside it raises: the row is closed."""
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")
    real = tasks.SearchExecutor

    class Late(real):  # type: ignore[valid-type, misc]
        async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            execution = await super().execute(*args, **kwargs)
            execution.status = ExecutionStatus.RUNNING
            await self.session.commit()
            raise RuntimeError("late")

    monkeypatch.setattr(tasks, "SearchExecutor", Late)
    run_queued(db_schema, monkeypatch, queued, provider=_Provider(), budget=CountingBudget())

    (row,) = _rows(db_schema, search_id)
    assert row["status"] == "FAILED" and row["error_type"] == "RuntimeError"


def test_a_second_delivery_of_the_task_does_nothing(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, evaluator: type[_Evaluator]
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")
    provider = _Provider()

    outcome = run_queued(
        db_schema, monkeypatch, queued * 2, provider=provider, budget=CountingBudget()
    )

    assert [each["status"] for each in outcome] == ["done", "not_queued"]
    assert provider.creates == 1 and evaluator.calls == ["COMPLETED"]


def test_a_missing_row_is_reported_and_builds_no_provider(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_provider(monkeypatch)
    ghost = str(uuid.uuid4())
    outcome = run_queued(
        db_schema, monkeypatch, [ghost], provider=_Provider(), budget=CountingBudget()
    )
    assert outcome == [{"status": "not_found", "execution_id": ghost}]


def test_a_search_deleted_before_the_task_runs_fails_the_row_closed(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    client.post(f"/api/v1/searches/{search_id}/run")

    async def drop_link(session):  # type: ignore[no-untyped-def]
        row = await session.get(SearchExecution, uuid.UUID(queued[0]))
        row.search_id = uuid.uuid4()

    # A row whose search cannot be loaded is the nearest honest stand-in for a deleted search.
    from sqlalchemy import text

    async def detach(session):  # type: ignore[no-untyped-def]
        await session.execute(text("SET session_replication_role = replica"))
        await drop_link(session)

    _sql(db_schema, detach)
    outcome = run_queued(
        db_schema, monkeypatch, queued, provider=_Provider(), budget=CountingBudget()
    )
    assert outcome[0]["status"] == "failed"
    assert outcome[0]["error_class"] == "LookupError"


def test_a_broker_failure_closes_the_row_and_is_a_coded_503(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(db_schema)
    search_id = _create(client)

    def refuse(_id: str) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.run_manual_search_task, "delay", refuse)

    response = client.post(f"/api/v1/searches/{search_id}/run")

    assert response.status_code == 503, response.text
    assert response.json()["detail_code"] == "search.run.not_queued"
    (row,) = _rows(db_schema, search_id)
    assert row["status"] == "FAILED" and row["code"] == "search.run.not_queued"
    capture_queue(monkeypatch)
    assert client.post(f"/api/v1/searches/{search_id}/run").status_code == 202
