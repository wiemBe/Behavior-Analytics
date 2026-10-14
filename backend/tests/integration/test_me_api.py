"""Authentication, authorization and response honesty for the /me endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.security import rbac
from app.security.auth import get_principal
from app.security.rbac import PERM_ADMIN, PERM_ALERT_ACK, PERM_FLEET_READ, Principal

pytestmark = pytest.mark.integration


def _client(db_url: str, principal: Principal) -> TestClient:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        response = client.get("/api/v1/me")
    assert response.status_code == 401


async def test_admin_principal(db_schema: str) -> None:
    """An admin:* principal gets permissions == ["admin:*"] and all
    non-wildcard PERM_* in effective."""
    client = _client(
        db_schema,
        Principal(subject="admin-user", permissions=frozenset({PERM_ADMIN})),
    )
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject"] == "admin-user"
    assert body["email"] is None
    assert body["permissions"] == [PERM_ADMIN]

    # effective_permissions should contain every non-wildcard PERM_* constant
    # (exclude values ending with ":*", not the name PERM_ADMIN)
    expected_effective = sorted(
        getattr(rbac, name)
        for name in dir(rbac)
        if name.startswith("PERM_")
        and isinstance(getattr(rbac, name), str)
        and not getattr(rbac, name).endswith(":*")
    )
    assert body["effective_permissions"] == expected_effective


async def test_readonly_principal(db_schema: str) -> None:
    """A read:* principal gets every read: constant in
    effective_permissions and no write/admin ones."""
    client = _client(
        db_schema,
        Principal(subject="viewer", permissions=frozenset({"read:*"})),
    )
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject"] == "viewer"
    assert body["permissions"] == ["read:*"]

    # effective_permissions should contain only read:* constants
    effective = body["effective_permissions"]
    # All effective permissions should start with "read:"
    for perm in effective:
        assert perm.startswith("read:"), f"read:* principal should not have {perm}"
    # Should NOT have write or admin permissions
    assert "log_source:write" not in effective
    assert "admin:waf" not in effective
    assert "admin:outage" not in effective
    assert "admin:ips" not in effective
    assert "admin:classification" not in effective
    assert "admin:search_cadence" not in effective
    assert "rule:write" not in effective
    assert "coverage:write" not in effective
    assert "search:write" not in effective
    assert "search:execute" not in effective
    assert "alert:ack" not in effective
    assert "alert:resolve" not in effective

    # Every read: constant defined in rbac (PERM_* value starting with 'read:'
    # and not ending with ':*') must be present in effective_permissions
    expected_read_perms = sorted(
        getattr(rbac, name)
        for name in dir(rbac)
        if name.startswith("PERM_")
        and isinstance(getattr(rbac, name), str)
        and getattr(rbac, name).startswith("read:")
        and not getattr(rbac, name).endswith(":*")
    )
    for perm in expected_read_perms:
        assert perm in effective, f"read:* principal missing expected read: permission {perm}"


async def test_specific_permissions_principal(db_schema: str) -> None:
    """A principal with exactly {'read:fleet', 'alert:ack'} gets exactly those two."""
    perms = frozenset({PERM_FLEET_READ, PERM_ALERT_ACK})
    client = _client(db_schema, Principal(subject="specific-user", permissions=perms))
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject"] == "specific-user"
    assert body["permissions"] == sorted(perms)
    assert body["effective_permissions"] == sorted(perms)


async def test_email_passed_through(db_schema: str) -> None:
    """Email is passed through, and is null when absent."""
    # With email
    client = _client(
        db_schema,
        Principal(
            subject="user-with-email",
            email="user@example.com",
            permissions=frozenset({"read:*"}),
        ),
    )
    response = client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "user@example.com"

    # Without email
    client2 = _client(
        db_schema,
        Principal(subject="user-no-email", permissions=frozenset({"read:*"})),
    )
    response2 = client2.get("/api/v1/me")
    assert response2.status_code == 200, response2.text
    body2 = response2.json()
    assert body2["email"] is None