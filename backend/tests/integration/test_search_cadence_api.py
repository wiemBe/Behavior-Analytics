"""Authentication, authorization and response honesty for the cadence API."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.enums import SearchFamily
from app.models.identity import AuditLog
from app.models.search_cadence import SearchCadenceSetting
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = pytest.mark.integration


async def _seed(session) -> None:  # type: ignore[no-untyped-def]
    stamp = datetime(2026, 10, 12, tzinfo=UTC)
    session.add_all(
        SearchCadenceSetting(
            family=family.value,
            cadence_seconds=3600,
            updated_at=stamp,
        )
        for family in SearchFamily
    )
    await session.commit()


def _client(db_url: str, principal: Principal) -> TestClient:
    engine = create_async_engine(db_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_get_requires_a_principal() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "oidc"}
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/admin/search-cadence")
    assert response.status_code == 401


async def test_readonly_can_get_but_cannot_put_and_last_run_is_null(
    db_session, db_schema
) -> None:
    await _seed(db_session)
    client = _client(
        db_schema,
        Principal(subject="viewer", permissions=frozenset({"read:*"})),
    )
    response = client.get("/api/v1/admin/search-cadence")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["family"] for item in body] == [family.value for family in SearchFamily]
    assert all(item["last_run"] is None for item in body)
    assert body[0]["bucket_seconds"] == 300
    assert body[2]["bucket_seconds"] is None
    assert body[3]["bucket_seconds"] == 3600
    # New assertions: look up by family, not by position
    by_family = {item["family"]: item for item in body}
    assert by_family[SearchFamily.IPS_COLLECTION.value]["bucket_seconds"] == 300
    assert by_family[SearchFamily.CATCHALL_SENDERS.value]["bucket_seconds"] == 300
    assert by_family[SearchFamily.ANOMALY_EXPLANATION.value]["bucket_seconds"] is None
    denied = client.put(
        "/api/v1/admin/search-cadence/ANOMALY_EXPLANATION",
        json={"cadence_seconds": 300},
    )
    assert denied.status_code == 403


async def test_admin_validation_not_found_update_and_audit(db_session, db_schema) -> None:
    await _seed(db_session)
    client = _client(
        db_schema,
        Principal(subject="admin-user", permissions=frozenset({"admin:*"})),
    )
    unknown = client.put(
        "/api/v1/admin/search-cadence/UNKNOWN",
        json={"cadence_seconds": 300},
    )
    assert unknown.status_code == 404
    invalid = client.put(
        "/api/v1/admin/search-cadence/ANOMALY_EXPLANATION",
        json={"cadence_seconds": 600},
    )
    assert invalid.status_code == 422
    updated = client.put(
        "/api/v1/admin/search-cadence/ANOMALY_EXPLANATION",
        json={"cadence_seconds": 14400},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["cadence_seconds"] == 14400

    db_session.expire_all()
    audits = list(
        (
            await db_session.scalars(
                select(AuditLog).where(AuditLog.action == "search_cadence.update")
            )
        ).all()
    )
    assert len(audits) == 1
    assert audits[0].actor == "admin-user"
    assert audits[0].detail == {"old": 3600, "new": 14400}
