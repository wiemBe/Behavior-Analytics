"""POST /searches/{id}/run holds an Ariel permit; a full budget is a recorded 503.

DB-gated. The real app, the real route, the stored query. The budget is a counting
fake (the PostgreSQL budget itself is pinned in test_ariel_permit_*.py and in
test_search_scheduler.py); the provider counts the searches it is asked to create.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes.searches import get_ariel_permit_factory
from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.providers.factory import get_provider
from app.providers.mock import MockQRadarProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.ariel_permit import _ARIEL_PRIORITY
from tests.integration.ariel_permit_fakes import CountingBudget

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run search integration tests",
    ),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
AQL = "SELECT sourceip FROM events LAST 1 HOURS"


class _Provider(MockQRadarProvider):
    def __init__(self) -> None:
        super().__init__(seed=1337)
        self.creates = 0

    async def create_ariel_search(self, aql: str):
        self.creates += 1
        return await super().create_ariel_search(aql)


def _client(db_url: str, budget: CountingBudget, provider: _Provider) -> TestClient:
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

    async def _provider():  # type: ignore[no-untyped-def]
        yield provider

    settings = get_settings()
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_principal] = lambda: ADMIN
    app.dependency_overrides[get_provider] = _provider
    app.dependency_overrides[get_ariel_permit_factory] = lambda: lambda _session, _settings: budget
    return TestClient(app)


def _create(client: TestClient) -> str:
    created = client.post(
        "/api/v1/searches",
        json={"name": "manual", "aql_query": AQL, "schedule_cron": "0 * * * *"},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def test_manual_run_with_a_full_budget_is_503_and_records_the_refusal(db_schema: str) -> None:
    budget = CountingBudget(
        global_limit=6, instance_limit=3, refuse=True, wait_seconds=30.0, reported_held=6
    )
    provider = _Provider()
    client = _client(db_schema, budget, provider)
    search_id = _create(client)

    response = client.post(f"/api/v1/searches/{search_id}/run")

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == (
        "the Ariel search budget is full (6 of 6 in flight); waited 30 s; "
        "nothing was sent to QRadar"
    )
    assert provider.creates == 0
    assert budget.priorities == ["background"]
    assert _ARIEL_PRIORITY.get() == "live"
    rows = client.get(f"/api/v1/searches/{search_id}/executions").json()
    assert len(rows) == 1, "the refusal is recorded, not rolled back with the 503"
    assert rows[0]["status"] == "FAILED"
    assert rows[0]["error_type"] == "CAPACITY"
    assert "sourceip" not in (rows[0]["error_message"] or "")


def test_manual_run_with_a_granting_budget_completes(db_schema: str) -> None:
    budget = CountingBudget()
    provider = _Provider()
    client = _client(db_schema, budget, provider)
    search_id = _create(client)

    response = client.post(f"/api/v1/searches/{search_id}/run")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED"
    assert provider.creates == 1
    assert budget.acquired == 1 and budget.held == 0
