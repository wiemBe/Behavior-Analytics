"""Authentication, authorization and response honesty for the /me endpoint."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.identity import AuditLog, User
from app.security import rbac
from app.security.auth import get_principal
from app.security.passwords import hash_password
from app.security.rbac import PERM_ADMIN, PERM_ALERT_ACK, PERM_FLEET_READ, Principal

pytestmark = pytest.mark.integration

ORIGIN = "http://localhost:3000"
PASSWORD = "Correct-Horse-Battery-99!"


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


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


# --- The account's interface language (D-37, I18N-1) ---


async def _add_session_user(maker, username: str) -> User:
    """A local account that can sign in, with no roles: PUT /me/preferences
    requires a session and no permission, so no role is needed."""
    async with maker() as session:
        user = User(
            username=username,
            auth_source="LOCAL",
            hashed_password=hash_password(PASSWORD),
            is_active=True,
            display_name=username,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _login(client: TestClient, username: str):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )


async def _audit_rows(maker, action: str) -> list[AuditLog]:
    async with maker() as session:
        return list(
            await session.scalars(select(AuditLog).where(AuditLog.action == action))
        )


@pytest.fixture
def session_api(db_schema: str):
    """A session-mode app on the test schema: cookies enforced, CSRF on."""
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
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
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "session", "public_origin": ORIGIN}
    )
    client = TestClient(app, headers={"Origin": ORIGIN})
    try:
        yield client, maker
    finally:
        client.close()
        engine.sync_engine.dispose()


async def test_ui_language_round_trip_and_audit(session_api) -> None:
    """A new account reads 'en'; PUT stores 'tr' on the row, answers with the
    MeOut GET /me would return, and writes exactly one audit record."""
    client, maker = session_api
    user = await _add_session_user(maker, "ada")
    assert _login(client, "ada").status_code == 200

    me = client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["ui_language"] == "en"

    changed = client.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["ui_language"] == "tr"
    assert body["subject"] == "ada"
    assert body["username"] == "ada"
    # The PUT answer is exactly what GET /me answers now.
    assert body == client.get("/api/v1/me").json()

    async with maker() as session:
        row = await session.get(User, user.id)
        assert row is not None
        assert row.ui_language == "tr"
    records = await _audit_rows(maker, "account.preferences.update")
    assert len(records) == 1
    record = records[0]
    assert record.actor == "ada"
    assert record.actor_id == user.id
    assert record.object_type == "user"
    assert record.object_id == str(user.id)
    assert record.outcome == "SUCCESS"
    assert record.detail["old"] == "en"
    assert record.detail["new"] == "tr"
    assert record.source_ip is not None
    assert record.user_agent is not None

    # Re-sending the stored value is a 200 that changes nothing and writes
    # no second audit record.
    again = client.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert again.status_code == 200, again.text
    assert again.json()["ui_language"] == "tr"
    assert len(await _audit_rows(maker, "account.preferences.update")) == 1


async def test_preference_body_is_validated_by_the_schema(session_api) -> None:
    """An unknown language, a wrong case, an extra key or a missing key is a
    422, and none of them writes the row or the audit trail."""
    client, maker = session_api
    user = await _add_session_user(maker, "ada")
    assert _login(client, "ada").status_code == 200

    for bad in (
        {"ui_language": "de"},
        {"ui_language": "TR"},
        {"ui_language": "tr", "theme": "dark"},
        {},
    ):
        refused = client.put("/api/v1/me/preferences", json=bad)
        assert refused.status_code == 422, (bad, refused.text)

    async with maker() as session:
        row = await session.get(User, user.id)
        assert row is not None
        assert row.ui_language == "en"
    assert await _audit_rows(maker, "account.preferences.update") == []


async def test_preference_change_needs_a_session(session_api) -> None:
    client, _maker = session_api
    stranger = TestClient(client.app, headers={"Origin": ORIGIN})
    assert stranger.get("/api/v1/me").status_code == 401
    refused = stranger.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Authentication required."


async def test_a_disabled_account_cannot_change_its_preference(session_api) -> None:
    """A disabled caller gets the password route's 401, and nothing is
    written: neither the row nor the audit trail."""
    client, maker = session_api
    user = await _add_session_user(maker, "ada")
    assert _login(client, "ada").status_code == 200
    async with maker() as session:
        row = await session.get(User, user.id)
        assert row is not None
        row.disabled_at = datetime.now(UTC)
        await session.commit()

    assert client.get("/api/v1/me").status_code == 401
    refused = client.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Authentication required."

    async with maker() as session:
        row = await session.get(User, user.id)
        assert row is not None
        assert row.ui_language == "en"
    assert await _audit_rows(maker, "account.preferences.update") == []


async def test_a_cross_origin_preference_change_is_refused(session_api) -> None:
    """The CSRF middleware covers the new mutating route: a foreign Origin is
    refused before any handler runs, and a missing pair is too."""
    client, _maker = session_api
    foreign = TestClient(client.app, headers={"Origin": "https://evil.example"})
    refused = foreign.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert refused.status_code == 403
    assert "evil.example" in refused.json()["detail"]
    bare = TestClient(client.app)
    missing = bare.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert missing.status_code == 403
    assert "no Origin or Referer" in missing.json()["detail"]


async def test_development_principal_gets_409_and_writes_nothing(db_schema: str) -> None:
    """The development principal has no account row, so there is nothing to
    store a preference on: 409 with the contract's sentence, no audit."""
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
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
    # No get_principal override and no settings override: development with
    # auth_provider=local resolves the real local-dev principal.
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        response = client.put("/api/v1/me/preferences", json={"ui_language": "tr"})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This session has no account to store a preference on."
    )
    assert await _audit_rows(maker, "account.preferences.update") == []
    await engine.dispose()


async def test_a_stale_principal_over_a_dead_or_missing_row_gets_401(
    db_schema: str,
) -> None:
    """The handler re-checks the row the way the password route does: a
    disabled, an inactive or a deleted account is 401, never a write."""
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            disabled = User(
                username="ada",
                auth_source="LOCAL",
                is_active=True,
                disabled_at=datetime.now(UTC),
            )
            inactive = User(username="bea", auth_source="LOCAL", is_active=False)
            session.add_all([disabled, inactive])
            await session.commit()
            disabled_id, inactive_id = disabled.id, inactive.id
    finally:
        await engine.dispose()

    for principal in (
        Principal(subject="ada", username="ada", user_id=disabled_id),
        Principal(subject="bea", username="bea", user_id=inactive_id),
        Principal(subject="gone", user_id=uuid.uuid4()),
    ):
        client = _client(db_schema, principal)
        response = client.put("/api/v1/me/preferences", json={"ui_language": "tr"})
        assert response.status_code == 401, principal.subject
        assert response.json()["detail"] == "Authentication required."


async def test_the_table_refuses_an_unknown_language(db_schema: str) -> None:
    """The column's check constraint is the last guard: 'de' written straight
    to the table fails, and an insert that omits the column defaults to 'en'."""
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            defaulted = User(username="ada", auth_source="LOCAL", is_active=True)
            session.add(defaulted)
            await session.commit()
            assert defaulted.ui_language == "en"
        async with maker() as session:
            session.add(
                User(
                    username="bea",
                    auth_source="LOCAL",
                    is_active=True,
                    ui_language="de",
                )
            )
            with pytest.raises(IntegrityError, match="ui_language_known"):
                await session.commit()
    finally:
        await engine.dispose()