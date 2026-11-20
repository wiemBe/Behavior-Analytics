"""MSG-8b: a run's fixed error sentences are stored with their code; the routes serve the codes.

DB-gated. The executor writes `error_message_i18n` beside `error_message` on every path (final
failure, the retry mark, the reset at the start of an attempt, the 2,000-character cut); free
text gets no code. The API serves the code beside the text, the manual-run 503 and the cron-floor
422 carry theirs.
"""

from __future__ import annotations

import os
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.enums import ExecutionErrorType, ExecutionStatus
from app.providers.base import ProviderCapability, ProviderUnavailableError
from app.providers.dto import ArielSearchStatusDTO
from app.providers.mock import MockQRadarProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.aql_validator import AQLValidationError, validate_aql
from app.services.search_executor import SearchExecutor
from tests.integration.ariel_permit_fakes import CountingBudget
from tests.integration.factories import FakeClock, make_search, utc
from tests.integration.manual_run_support import capture_queue, run_queued

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run search integration tests",
    ),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
AQL = "SELECT sourceip FROM events LAST 1 HOURS"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _executor(session, provider, *, budget=None, **kwargs):  # type: ignore[no-untyped-def]
    return SearchExecutor(
        session, provider, budget or CountingBudget(), sleep=_noop_sleep, **kwargs
    )


def _no_retry_settings() -> Settings:
    return Settings(encryption_key="x" * 44, ariel_max_retries=0)


# --- the executor ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_timeout_is_stored_with_its_code(db_session) -> None:
    class Stuck(MockQRadarProvider):
        async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
            return ArielSearchStatusDTO(search_id=search_id, status="EXECUTE", progress=10)

    search = await make_search(db_session, timeout_seconds=10)
    executor = _executor(
        db_session,
        Stuck(seed=1),
        clock=FakeClock(utc(), step_seconds=5),
        settings=_no_retry_settings(),
    )
    run = await executor.execute(search, run_key="timeout", instance_key="default")

    assert run.status == ExecutionStatus.TIMEOUT
    assert run.error_message == "search exceeded timeout of 10s"
    assert run.error_message_i18n == {"code": "search.run.timeout", "params": {"timeout": 10}}
    assert run.error_message_code == "search.run.timeout"
    assert run.error_message_params == {"timeout": 10}


@pytest.mark.asyncio
async def test_an_ariel_error_with_no_text_is_coded_and_with_text_is_not(db_session) -> None:
    class Failing(MockQRadarProvider):
        messages: ClassVar[list[str]] = []

        async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
            return ArielSearchStatusDTO(
                search_id=search_id, status="ERROR", error_messages=list(self.messages)
            )

    search = await make_search(db_session)
    bare = await _executor(
        db_session, Failing(seed=1), settings=_no_retry_settings()
    ).execute(search, run_key="bare", instance_key="default")
    assert bare.error_type == ExecutionErrorType.SEARCH_FAILED.value
    assert bare.error_message == "Ariel search failed"
    assert bare.error_message_i18n == {"code": "search.run.failed", "params": {}}

    class WithText(Failing):
        messages: ClassVar[list[str]] = ["QRadar says no", "second"]

    worded = await _executor(
        db_session, WithText(seed=1), settings=_no_retry_settings()
    ).execute(search, run_key="worded", instance_key="default")
    assert worded.error_message == "QRadar says no; second"
    assert worded.error_message_i18n is None, "Ariel's own words are free text"


@pytest.mark.asyncio
async def test_a_refused_permit_is_stored_with_its_code(db_session) -> None:
    budget = CountingBudget(global_limit=4, instance_limit=2, refuse=True, wait_seconds=30.0)
    search = await make_search(db_session)
    run = await _executor(db_session, MockQRadarProvider(seed=1), budget=budget).execute(
        search, run_key="cap", instance_key="default"
    )
    assert run.error_type == ExecutionErrorType.CAPACITY.value
    assert run.error_message == (
        "No Ariel permit became available within 30 seconds (per-console cap 2, global cap 4)"
    )
    assert run.error_message_i18n == {
        "code": "search.run.capacity",
        "params": {"waited": 30.0, "instance_cap": 2, "global_cap": 4},
    }


@pytest.mark.asyncio
async def test_a_provider_that_cannot_run_aql_is_coded(db_session) -> None:
    class NoAql(MockQRadarProvider):
        def supports(self, capability: ProviderCapability) -> bool:
            return capability is not ProviderCapability.AQL_EXECUTION

    search = await make_search(db_session)
    run = await _executor(db_session, NoAql(seed=1)).execute(
        search, run_key="noaql", instance_key="default"
    )
    assert run.error_type == ExecutionErrorType.VALIDATION.value
    assert run.error_message == "configured provider cannot execute AQL"
    assert run.error_message_i18n == {"code": "search.run.no_aql_execution", "params": {}}


@pytest.mark.asyncio
async def test_a_result_parsing_failure_is_coded_and_a_cut_text_is_not(db_session) -> None:
    class BadResults(MockQRadarProvider):
        detail = "bad column"

        async def get_ariel_search_results(self, search_id: str, *, max_rows: int):
            raise ValueError(self.detail)

    search = await make_search(db_session)
    short = await _executor(
        db_session, BadResults(seed=1), settings=_no_retry_settings()
    ).execute(search, run_key="short", instance_key="default")
    assert short.error_type == ExecutionErrorType.PARSING.value
    assert short.error_message == "failed to fetch/parse results: bad column"
    assert short.error_message_i18n == {
        "code": "search.run.parse_failed",
        "params": {"detail": "bad column"},
    }

    class LongResults(BadResults):
        detail = "x" * 3000

    cut = await _executor(
        db_session, LongResults(seed=1), settings=_no_retry_settings()
    ).execute(search, run_key="long", instance_key="default")
    assert cut.error_message is not None and len(cut.error_message) == 2000
    assert cut.error_message.startswith("failed to fetch/parse results: xxx")
    assert cut.error_message_i18n is None, "a coded sentence is never shown cut"


@pytest.mark.asyncio
async def test_a_stored_query_that_fails_revalidation_is_coded(db_session) -> None:
    search = await make_search(db_session, aql="SELECT * FROM events")
    run = await _executor(db_session, MockQRadarProvider(seed=1)).execute(
        search, run_key="bad", instance_key="default"
    )
    assert run.error_type == ExecutionErrorType.VALIDATION.value
    assert run.error_message_i18n is not None
    assert run.error_message_i18n["code"] == "search.run.validation"
    params = run.error_message_i18n["params"]
    assert isinstance(params, dict)
    assert params["reasons"]["kind"] == "list"
    with pytest.raises(AQLValidationError) as expected:
        validate_aql(
            search.aql_query,
            max_time_range_hours=search.max_time_range_hours,
            max_result_rows=search.max_result_rows,
        )
    assert run.error_message == "; ".join(expected.value.reasons)
    assert [item["code"] for item in params["reasons"]["items"]] == [
        ref.code for ref in expected.value.refs if ref is not None
    ]


@pytest.mark.asyncio
async def test_a_provider_exception_text_is_free_text(db_session) -> None:
    class Down(MockQRadarProvider):
        async def create_ariel_search(self, aql: str):
            raise ProviderUnavailableError("QRadar is unreachable")

    search = await make_search(db_session)
    run = await _executor(db_session, Down(seed=1), settings=_no_retry_settings()).execute(
        search, run_key="down", instance_key="default"
    )
    assert run.error_message == "QRadar is unreachable"
    assert run.error_message_i18n is None


@pytest.mark.asyncio
async def test_a_retry_mark_carries_the_code_and_a_success_clears_both(db_session) -> None:
    calls = {"n": 0}

    class FlakyStatus(MockQRadarProvider):
        async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
            calls["n"] += 1
            if calls["n"] == 1:
                return ArielSearchStatusDTO(search_id=search_id, status="WAIT", progress=1)
            return await super().get_ariel_search_status(search_id)

    seen: list[tuple[str | None, dict[str, object] | None]] = []
    search = await make_search(db_session, timeout_seconds=10)
    settings = Settings(
        encryption_key="x" * 44,
        ariel_max_retries=2,
        ariel_retry_base_seconds=1,
        ariel_retry_max_seconds=2,
    )
    holder: dict[str, SearchExecutor] = {}

    async def watching_sleep(_seconds: float) -> None:
        run = await holder["executor"]._get_or_create_execution(
            search, run_key="retry", trigger="SCHEDULED", triggered_by=None
        )
        seen.append((run.error_message, run.error_message_i18n))

    executor = SearchExecutor(
        db_session,
        FlakyStatus(seed=1),
        CountingBudget(),
        settings=settings,
        clock=FakeClock(utc(), step_seconds=11),
        sleep=watching_sleep,
    )
    holder["executor"] = executor
    run = await executor.execute(search, run_key="retry", instance_key="default")

    assert run.status == ExecutionStatus.COMPLETED
    assert run.retry_count == 1
    assert seen[0] == (
        "search exceeded timeout of 10s",
        {"code": "search.run.timeout", "params": {"timeout": 10}},
    )
    assert run.error_message is None and run.error_message_i18n is None
    assert run.error_message_code is None and run.error_message_params is None


# --- the routes -----------------------------------------------------------------------------


class _Provider(MockQRadarProvider):
    def __init__(self) -> None:
        super().__init__(seed=1337)


def _client(db_url: str) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    settings = get_settings()
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_principal] = lambda: ADMIN
    return TestClient(app)


def _create(client: TestClient) -> str:
    created = client.post(
        "/api/v1/searches",
        json={"name": "manual", "aql_query": AQL, "schedule_cron": "0 * * * *"},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _run_to_the_end(db_schema, monkeypatch, budget):  # type: ignore[no-untyped-def]
    """POST the run (202, queued), run the task as the worker would, return the stored row."""
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)
    response = client.post(f"/api/v1/searches/{search_id}/run")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "QUEUED"
    run_queued(db_schema, monkeypatch, queued, provider=_Provider(), budget=budget)
    return client.get(f"/api/v1/searches/{search_id}/executions").json()[0]


def test_a_full_budget_is_a_capacity_row_that_carries_its_code(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    budget = CountingBudget(
        global_limit=6, instance_limit=3, refuse=True, wait_seconds=30.0, reported_held=6
    )

    row = _run_to_the_end(db_schema, monkeypatch, budget)

    assert row["status"] == "FAILED"
    assert row["error_type"] == "CAPACITY"
    assert row["error_message"] == (
        "No Ariel permit became available within 30 seconds (per-console cap 3, global cap 6)"
    )
    assert row["error_message_code"] == "search.run.capacity"
    assert row["error_message_params"] == {
        "waited": 30.0,
        "instance_cap": 3,
        "global_cap": 6,
    }


def test_a_budget_the_pool_cannot_count_still_names_its_caps_on_the_row(  # type: ignore[no-untyped-def]
    db_schema, monkeypatch
) -> None:
    budget = CountingBudget(global_limit=6, instance_limit=3, refuse=True, wait_seconds=2.5)

    row = _run_to_the_end(db_schema, monkeypatch, budget)

    assert row["error_message"] == (
        "No Ariel permit became available within 2.5 seconds (per-console cap 3, global cap 6)"
    )
    assert row["error_message_params"]["waited"] == 2.5


def test_a_completed_run_serves_no_code(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    row = _run_to_the_end(db_schema, monkeypatch, CountingBudget())
    assert row["status"] == "COMPLETED"
    assert row["error_message"] is None
    assert row["error_message_code"] is None
    assert row["error_message_params"] is None


def test_a_cron_below_the_floor_is_a_coded_422_on_create_and_on_update(db_schema: str) -> None:
    client = _client(db_schema)
    refused = client.post(
        "/api/v1/searches",
        json={"name": "fast", "aql_query": AQL, "schedule_cron": "*/2 * * * *"},
    )
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert body["detail"].startswith("schedule_cron '*/2 * * * *' (UTC) fires 120 seconds apart (")
    assert body["detail_code"] == "search.cron.below_floor"
    assert body["detail_params"]["gap"] == 120
    assert body["detail_params"]["min_seconds"] == 300

    search_id = _create(client)
    patched = client.patch(
        f"/api/v1/searches/{search_id}", json={"schedule_cron": "not a cron"}
    )
    assert patched.status_code == 422, patched.text
    assert patched.json()["detail_code"] == "search.cron.invalid"
    assert patched.json()["detail"].startswith(
        "schedule_cron 'not a cron' (UTC) is not a valid cron expression: "
    )
