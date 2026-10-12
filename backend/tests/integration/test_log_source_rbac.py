"""RBAC and audit on the log-source routes. DB-gated."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import create_app
from app.models.identity import AuditLog
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run API permission integration tests",
    ),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
EDITOR = Principal(
    subject="editor", permissions=frozenset({"read:*", "log_source:write"})
)
NOTHING = Principal(subject="nobody", permissions=frozenset())


def _client(principal: Principal) -> TestClient:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def _audit_rows(action: str) -> list[AuditLog]:
    async def _go() -> list[AuditLog]:
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                stmt = select(AuditLog).where(AuditLog.action == action)
                return list((await s.scalars(stmt)).all())
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _seeded_source_id(db_schema) -> str:
    resp = _client(ADMIN).post("/api/v1/log-sources/sync")
    assert resp.status_code == 200
    return _client(ADMIN).get("/api/v1/log-sources").json()[0]["id"]


def test_patch_without_write_permission_is_403(db_schema) -> None:
    ls_id = _seeded_source_id(db_schema)
    resp = _client(VIEWER).patch(
        f"/api/v1/log-sources/{ls_id}", json={"monitoring_enabled": False}
    )
    assert resp.status_code == 403
    assert _audit_rows("log_source.update") == []
    # And nothing changed.
    assert _client(ADMIN).get(f"/api/v1/log-sources/{ls_id}").json()["monitoring_enabled"]


def test_patch_with_write_permission_updates_and_audits(db_schema) -> None:
    ls_id = _seeded_source_id(db_schema)
    resp = _client(EDITOR).patch(
        f"/api/v1/log-sources/{ls_id}",
        json={"monitoring_enabled": False, "owner": "soc-team"},
    )
    assert resp.status_code == 200
    assert resp.json()["monitoring_enabled"] is False
    rows = _audit_rows("log_source.update")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == "editor"
    assert row.object_type == "log_source"
    assert row.object_id == ls_id
    changes = row.detail["changes"]
    assert changes["monitoring_enabled"] == {"from": True, "to": False}
    assert changes["owner"]["to"] == "soc-team"


def test_patch_unknown_source_is_404_and_not_audited(db_schema) -> None:
    resp = _client(EDITOR).patch(
        "/api/v1/log-sources/00000000-0000-0000-0000-000000000000", json={"owner": "x"}
    )
    assert resp.status_code == 404
    assert _audit_rows("log_source.update") == []


def test_sync_is_forbidden_for_readonly_and_editor(db_schema) -> None:
    assert _client(VIEWER).post("/api/v1/log-sources/sync").status_code == 403
    assert _client(EDITOR).post("/api/v1/log-sources/sync").status_code == 403


def test_sync_allowed_for_admin(db_schema) -> None:
    assert _client(ADMIN).post("/api/v1/log-sources/sync").status_code == 200


def test_reads_need_a_read_permission(db_schema) -> None:
    assert _client(NOTHING).get("/api/v1/log-sources").status_code == 403
    assert _client(NOTHING).get("/api/v1/overview").status_code == 403
    assert _client(NOTHING).get("/api/v1/searches").status_code == 403
    assert _client(NOTHING).get("/api/v1/alerts").status_code == 403
    assert _client(VIEWER).get("/api/v1/log-sources").status_code == 200
    assert _client(VIEWER).get("/api/v1/overview").status_code == 200
    assert _client(VIEWER).get("/api/v1/searches").status_code == 200
    assert _client(VIEWER).get("/api/v1/alerts").status_code == 200
