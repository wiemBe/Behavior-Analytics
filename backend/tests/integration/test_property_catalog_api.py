"""The property-catalog route against a real database and the mock provider."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.property_catalog import PropertyCatalogService, get_property_catalog_service
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration


def _client(db_url: str, permissions: frozenset[str]) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            yield session

    app = create_app()
    service = PropertyCatalogService(ttl_seconds=600)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_property_catalog_service] = lambda: service
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u", permissions=permissions
    )
    return TestClient(app)


async def test_catalog_200_403_404(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    typed = await make_log_source(db_session, instance, qradar_id=1, type_id=212)
    untyped = await make_log_source(db_session, instance, qradar_id=2, name="untyped")
    await db_session.commit()

    admin = _client(db_schema, frozenset({"admin:waf"}))
    body = admin.get(f"/api/v1/waf/log-sources/{typed.id}/property-catalog").json()
    assert body["status"] == "AVAILABLE" and body["filtered_by_source_type"] is True
    assert {p["name"] for p in body["properties"] if p["applies_to_source_type"]} >= {
        "URL", "WAF_Action",
    }
    assert "regex" not in str(body).lower() and "payload" not in str(body).lower()

    unknown = admin.get(f"/api/v1/waf/log-sources/{untyped.id}/property-catalog").json()
    assert unknown["filtered_by_source_type"] is False
    assert {p["applies_to_source_type"] for p in unknown["properties"]} == {None}

    assert admin.get(f"/api/v1/waf/log-sources/{uuid.uuid4()}/property-catalog").status_code == 404
    viewer = _client(db_schema, frozenset({"read:*"}))
    assert viewer.get(f"/api/v1/waf/log-sources/{typed.id}/property-catalog").status_code == 403
