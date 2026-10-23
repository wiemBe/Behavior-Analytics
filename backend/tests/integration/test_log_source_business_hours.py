"""PATCH validation for timezone and business hours. DB-gated."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import create_app
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run log-source validation tests",
    ),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
EDITOR = Principal(
    subject="editor", permissions=frozenset({"read:*", "log_source:write"})
)


def _client(principal: Principal) -> TestClient:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def _seeded_source_id(db_schema: str) -> str:
    del db_schema
    created = _client(ADMIN).post("/api/v1/log-sources/sync")
    assert created.status_code == 200
    listed = _client(ADMIN).get("/api/v1/log-sources")
    assert listed.status_code == 200
    return listed.json()[0]["id"]


def _error_fields(response) -> set[str]:
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    found: set[str] = set()
    for item in detail:
        found.update(part for part in item["loc"] if isinstance(part, str))
    return found


def test_unknown_timezone_is_422(db_schema: str) -> None:
    ls_id = _seeded_source_id(db_schema)
    resp = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}", json={"timezone_name": "Not/AZone"}
    )
    assert resp.status_code == 422
    assert "timezone_name" in _error_fields(resp)


def test_out_of_range_business_days_are_422(db_schema: str) -> None:
    ls_id = _seeded_source_id(db_schema)
    for days in ([0], [8]):
        resp = _client(EDITOR).patch(
            f"/api/v1/log-sources/{ls_id}", json={"business_days": days}
        )
        assert resp.status_code == 422
        assert "business_days" in _error_fields(resp)


def test_empty_days_with_business_hours_on_are_422(db_schema: str) -> None:
    ls_id = _seeded_source_id(db_schema)
    resp = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}",
        json={"business_hours_only": True, "business_days": []},
    )
    assert resp.status_code == 422
    assert "business_days" in _error_fields(resp)


def test_hours_outside_0_to_23_are_422(db_schema: str) -> None:
    ls_id = _seeded_source_id(db_schema)
    start = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}", json={"business_hours_start": 24}
    )
    assert start.status_code == 422
    assert "business_hours_start" in _error_fields(start)
    end = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}", json={"business_hours_end": -1}
    )
    assert end.status_code == 422
    assert "business_hours_end" in _error_fields(end)


def test_valid_zone_wrap_and_equal_bounds_are_accepted(db_schema: str) -> None:
    ls_id = _seeded_source_id(db_schema)
    istanbul = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}",
        json={
            "timezone_name": "Europe/Istanbul",
            "business_hours_only": True,
            "business_hours_start": 9,
            "business_hours_end": 18,
            "business_days": [1, 2, 3, 4, 5],
        },
    )
    assert istanbul.status_code == 200
    assert istanbul.json()["timezone_name"] == "Europe/Istanbul"

    wrap = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}",
        json={"business_hours_start": 22, "business_hours_end": 6},
    )
    assert wrap.status_code == 200
    assert wrap.json()["business_hours_start"] == 22
    assert wrap.json()["business_hours_end"] == 6

    same = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}",
        json={"business_hours_start": 8, "business_hours_end": 8},
    )
    assert same.status_code == 200
    assert same.json()["business_hours_start"] == 8
    assert same.json()["business_hours_end"] == 8


def test_enabling_hours_only_against_stored_empty_days_is_422(db_schema: str) -> None:
    """The schema sees only this payload. The route must read the stored days."""
    ls_id = _seeded_source_id(db_schema)
    cleared = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}",
        json={"business_hours_only": False, "business_days": []},
    )
    assert cleared.status_code == 200

    rejected = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}", json={"business_hours_only": True}
    )
    assert rejected.status_code == 422
    assert "business_days" in _error_fields(rejected)

    body = _client(EDITOR).get(f"/api/v1/log-sources/{ls_id}").json()
    assert body["business_hours_only"] is False
    assert body["business_days"] == []
