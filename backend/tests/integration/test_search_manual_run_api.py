"""POST /searches/{id}/run queues the run; the task holds the Ariel permit (SCALE-2h2).

DB-gated. The real app, the real route, the stored query. The budget is a counting
fake (the PostgreSQL budget itself is pinned in test_ariel_permit_*.py and in
test_search_scheduler.py); the provider counts the searches it is asked to create. The
route answers 202; the task runs here as the worker would, and a full budget is a
FAILED/CAPACITY row.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.providers.mock import MockQRadarProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.ariel_permit import _ARIEL_PRIORITY
from tests.integration.ariel_permit_fakes import CountingBudget
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


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    """One limit window per test: the run route is rate limited since FIX-8."""
    limiter.reset()
    yield
    limiter.reset()


class _Provider(MockQRadarProvider):
    def __init__(self) -> None:
        super().__init__(seed=1337)
        self.creates = 0

    async def create_ariel_search(self, aql: str):
        self.creates += 1
        return await super().create_ariel_search(aql)


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


def test_manual_run_with_a_full_budget_is_a_recorded_capacity_failure(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = CountingBudget(
        global_limit=6, instance_limit=3, refuse=True, wait_seconds=30.0, reported_held=6
    )
    provider = _Provider()
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)

    response = client.post(f"/api/v1/searches/{search_id}/run")

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "QUEUED"
    assert provider.creates == 0 and budget.priorities == []
    outcome = run_queued(db_schema, monkeypatch, queued, provider=provider, budget=budget)
    assert outcome[0]["outcome"] == "FAILED"
    assert provider.creates == 0
    assert budget.priorities == ["background"]
    assert _ARIEL_PRIORITY.get() == "live"
    rows = client.get(f"/api/v1/searches/{search_id}/executions").json()
    assert len(rows) == 1, "the refusal is recorded on the queued row"
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["error_type"] == "CAPACITY"
    assert "sourceip" not in (rows[0]["error_message"] or "")


def test_manual_run_with_a_granting_budget_completes(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = CountingBudget()
    provider = _Provider()
    client = _client(db_schema)
    search_id = _create(client)
    queued = capture_queue(monkeypatch)

    response = client.post(f"/api/v1/searches/{search_id}/run")

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "QUEUED"
    assert provider.creates == 0
    run_queued(db_schema, monkeypatch, queued, provider=provider, budget=budget)
    rows = client.get(f"/api/v1/searches/{search_id}/executions").json()
    assert [row["status"] for row in rows] == ["COMPLETED"]
    assert provider.creates == 1
    assert budget.acquired == 1 and budget.held == 0


def test_manual_run_is_limited_at_the_search_execution_rate(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each run takes a scarce Ariel slot, so the route carries its own limit.

    The default limit must not be what refuses the third call: the decorated
    route keeps the manual search-execution limit alone (FIX-8).
    """
    monkeypatch.setattr(get_settings(), "rate_limit_search_execution", "2/minute")
    monkeypatch.setattr(get_settings(), "rate_limit_default", "50/minute")
    provider = _Provider()
    client = _client(db_schema)
    search_id = _create(client)
    capture_queue(monkeypatch)
    url = f"/api/v1/searches/{search_id}/run"

    assert client.post(url).status_code == 202
    # The first run is still queued, so the second is the 409, and it still counts.
    assert client.post(url).status_code == 409
    third = client.post(url)

    assert third.status_code == 429, third.text
    assert "Retry-After" in third.headers
    assert provider.creates == 0, "nothing in the request path reaches QRadar"
