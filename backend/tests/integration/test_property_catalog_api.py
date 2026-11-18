"""The property-catalog route against a real database and the mock provider."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.providers.mock import MockQRadarProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.property_catalog import refresh_catalog_snapshot
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration


def _client(db_url: str, permissions: frozenset[str]) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="u", permissions=permissions
    )
    return TestClient(app)


async def test_catalog_200_403_404(db_session, db_schema, monkeypatch) -> None:
    instance = await make_instance(db_session)
    typed = await make_log_source(db_session, instance, qradar_id=1, type_id=212)
    untyped = await make_log_source(db_session, instance, qradar_id=2, name="untyped")
    await db_session.commit()

    await refresh_catalog_snapshot(db_session, instance.id, lambda: MockQRadarProvider())

    def forbidden_factory(*args, **kwargs):
        raise AssertionError("Editor request must never construct a provider")

    monkeypatch.setattr("app.providers.factory.build_provider_for_instance", forbidden_factory)
    admin = _client(db_schema, frozenset({"admin:waf"}))
    body = admin.get(f"/api/v1/waf/log-sources/{typed.id}/property-catalog").json()
    assert body["status"] == "AVAILABLE" and body["filtered_by_source_type"] is True
    assert {p["name"] for p in body["properties"] if p["applies_to_source_type"]} >= {
        "URL", "WAF_Action",
    }
    assert "regex" not in str(body).lower() and "payload" not in str(body).lower()
    assert body["catalog_status"] == "COMPLETE" and body["as_of"]
    different = admin.get(
        f"/api/v1/waf/log-sources/{typed.id}/property-catalog?type_id=999999"
    ).json()
    assert not any(p["applies_to_source_type"] for p in different["properties"])
    # A type override never mutates the shared snapshot or another source's ranking.
    again = admin.get(f"/api/v1/waf/log-sources/{typed.id}/property-catalog").json()
    assert again["properties"] == body["properties"]
    url = f"/api/v1/waf/log-sources/{typed.id}/property-catalog"
    assert len(admin.get(url + "?limit=1").json()["properties"]) == 1
    assert admin.get(url + "?limit=201").status_code == 422
    assert admin.get(url + "?q=not-present").json()["properties"] == []
    ips = _client(db_schema, frozenset({"admin:ips"}))
    ips_url = f"/api/v1/ips/log-sources/{typed.id}/property-catalog"
    assert ips.get(ips_url + "?limit=1").json()["catalog_status"] == "COMPLETE"
    assert ips.get(f"/api/v1/ips/log-sources/{uuid.uuid4()}/property-catalog").status_code == 404
    assert _client(db_schema, frozenset({"read:*"})).get(
        f"/api/v1/ips/log-sources/{typed.id}/property-catalog"
    ).status_code == 403

    unknown = admin.get(f"/api/v1/waf/log-sources/{untyped.id}/property-catalog").json()
    assert unknown["filtered_by_source_type"] is False
    assert {p["applies_to_source_type"] for p in unknown["properties"]} == {None}

    assert admin.get(f"/api/v1/waf/log-sources/{uuid.uuid4()}/property-catalog").status_code == 404
    viewer = _client(db_schema, frozenset({"read:*"}))
    assert viewer.get(f"/api/v1/waf/log-sources/{typed.id}/property-catalog").status_code == 403


async def test_never_loaded_queue_once_and_queue_failure(
    db_session, db_schema, monkeypatch
) -> None:
    from app.workers.tasks import refresh_property_catalog

    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, type_id=212)
    await db_session.commit()
    queued = []
    monkeypatch.setattr(refresh_property_catalog, "delay", lambda ident: queued.append(ident))
    with _client(db_schema, frozenset({"admin:waf"})) as client:
        url = f"/api/v1/waf/log-sources/{source.id}/property-catalog"
        assert client.get(url).json()["catalog_status"] == "NEVER"
        for _ in range(2):
            body = client.get(url + "?refresh=true").json()
            assert body["catalog_status"] == "REFRESHING" and body["properties"] == []
        assert queued == [str(instance.id)]
    # A separate instance proves queue failure does not leak broker credentials.
    from app.models.property_catalog import PropertyCatalogState
    state = await db_session.get(PropertyCatalogState, instance.id)
    state.status = "FAILED"
    await db_session.commit()

    def broken(*args):
        raise RuntimeError("redis://private-credential")

    monkeypatch.setattr(refresh_property_catalog, "delay", broken)
    with _client(db_schema, frozenset({"admin:ips"})) as client:
        url = f"/api/v1/ips/log-sources/{source.id}/property-catalog?refresh=true"
        body = client.get(url).json()
        assert body["catalog_status"] == "FAILED"
        assert body["failure"]["failure_class"] == "QueueUnavailable"
        assert "private-credential" not in str(body)
