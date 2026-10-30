"""Sign-in, sessions, directory configuration and account administration."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Environment, get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.enums import AuthSource
from app.models.identity import AuditLog, AuthSession, LdapConfig, Role, User
from app.security.passwords import hash_password
from app.services.seed import ensure_default_roles
from tests.integration.ldap_fake import ClosedPort, DirectoryStandIn, write_server_material

pytestmark = pytest.mark.integration

ORIGIN = "http://localhost:3000"
PASSWORD = "Correct-Horse-Battery-99!"
_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def api(db_schema: str):
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


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


async def _add_user(
    maker,
    username: str,
    *,
    password: str | None = PASSWORD,
    source: str = AuthSource.LOCAL.value,
    roles: list[str] | None = None,
    must_change: bool = False,
) -> User:
    async with maker() as session:
        await ensure_default_roles(session)
        attached: list[Role] = []
        for name in roles or []:
            role = await session.scalar(select(Role).where(Role.name == name))
            assert role is not None
            attached.append(role)
        user = User(
            username=username,
            auth_source=source,
            hashed_password=hash_password(password) if password else None,
            must_change_password=must_change,
            is_active=True,
            display_name=username,
            roles=attached,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _postgres_subprocess_env(database_url: str) -> dict[str, str]:
    """POSTGRES_* for the CLI, parsed from the database the test reads.

    ``create-admin`` builds its connection from these variables. They have to
    name the same database as ``db_schema``, or the row it writes is invisible
    to the login that follows.
    """
    parsed = make_url(database_url)
    host = parsed.host
    port = parsed.port
    user = parsed.username
    password = parsed.password
    database = parsed.database
    if host is None or port is None or user is None or password is None or database is None:
        raise AssertionError(
            "the test database URL must include user, password, host, port and database"
        )
    return {
        "POSTGRES_HOST": host,
        "POSTGRES_PORT": str(port),
        "POSTGRES_USER": user,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": database,
    }


def _assert_no_secret(blob: str, password: str) -> None:
    assert password not in blob
    assert "hashed_password" not in blob
    assert "***redacted***" not in blob


async def test_local_login_wrong_and_unknown_share_the_message(api) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    ok = _login(client, "ada", PASSWORD)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["username"] == "ada"
    assert body["auth_source"] == "LOCAL"
    assert body["must_change_password"] is False
    assert "admin" not in body["roles"]
    assert "token" not in body
    assert PASSWORD not in ok.text
    cookie = ok.headers["set-cookie"].lower()
    assert "qd_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "secure" not in cookie

    wrong = _login(client, "ada", "not-the-password")
    unknown = _login(client, "nobody", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Invalid username or password"
    assert PASSWORD not in wrong.text and PASSWORD not in unknown.text


async def test_empty_password_is_the_same_refusal(api) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    empty = _login(client, "ada", "")
    assert empty.status_code == 401
    assert empty.json()["detail"] == "Invalid username or password"


async def test_login_is_not_counted_against_the_default_limit(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decorated route keeps its own limit alone (FIX-8).

    With the default set to one per minute, a second sign-in attempt must
    still be answered as an attempt — 401, not the default's 429 — because
    /auth/login carries RATE_LIMIT_LOGIN instead.
    """
    client, maker = api
    await _add_user(maker, "ada")
    monkeypatch.setattr(get_settings(), "rate_limit_login", "3/minute")
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")

    refusals = [_login(client, "ada", "not-the-password") for _ in range(2)]

    assert [r.status_code for r in refusals] == [401, 401]


async def test_the_login_limit_refuses_the_next_attempt(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, maker = api
    await _add_user(maker, "grace")
    monkeypatch.setattr(get_settings(), "rate_limit_login", "2/minute")

    assert _login(client, "grace", PASSWORD).status_code == 200
    assert _login(client, "grace", "not-the-password").status_code == 401
    third = _login(client, "grace", PASSWORD)

    assert third.status_code == 429, third.text
    assert "Retry-After" in third.headers


async def test_lockout_and_unlock_after_the_window(api) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    for _ in range(4):
        assert _login(client, "ada", "wrong-password").status_code == 401
    fifth = _login(client, "ada", "wrong-password")
    assert fifth.status_code == 401
    locked = _login(client, "ada", PASSWORD)
    assert locked.status_code == 401
    assert locked.json()["detail"] == "Invalid username or password"
    async with maker() as session:
        user = await session.scalar(select(User).where(User.username == "ada"))
        assert user is not None and user.locked_until is not None
        window = user.locked_until
        await session.execute(
            update(User)
            .where(User.username == "ada")
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    again = _login(client, "ada", "wrong-password")
    assert again.status_code == 401
    async with maker() as session:
        user = await session.scalar(select(User).where(User.username == "ada"))
        assert user is not None
        # The expired window was cleared, so this failure starts a new count
        # rather than extending the old one.
        assert user.locked_until is None or user.locked_until != window
        assert user.failed_login_count == 1
    ok = _login(client, "ada", PASSWORD)
    assert ok.status_code == 200, ok.text


async def test_expired_and_idle_sessions_are_rejected(api) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    assert _login(client, "ada", PASSWORD).status_code == 200
    async with maker() as session:
        row = await session.scalar(select(AuthSession))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert client.get("/api/v1/me").status_code == 401

    assert _login(client, "ada", PASSWORD).status_code == 200
    async with maker() as session:
        row = await session.scalar(
            select(AuthSession).order_by(AuthSession.created_at.desc())
        )
        assert row is not None
        row.last_seen_at = datetime.now(UTC) - timedelta(minutes=481)
        await session.commit()
    assert client.get("/api/v1/me").status_code == 401


async def test_logout_and_a_disabled_user(api) -> None:
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    other = await _add_user(maker, "bea", roles=["viewer"])
    assert _login(client, "bea", PASSWORD).status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    gone = client.post("/api/v1/auth/logout")
    assert gone.status_code == 204
    assert client.get("/api/v1/me").status_code == 401

    assert _login(client, "ada", PASSWORD).status_code == 200
    disabled = client.post(f"/api/v1/users/{other.id}/disable")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["disabled"] is True
    assert PASSWORD not in disabled.text
    # Bea's old cookie was deleted with the account's sessions. A new login
    # of a disabled account is refused, and a session is not issued.
    refused = _login(client, "bea", PASSWORD)
    assert refused.status_code == 401
    assert "set-cookie" not in {k.lower() for k in refused.headers}


async def test_role_change_applies_on_the_next_request(api) -> None:
    client, maker = api
    admin = await _add_user(maker, "ada", roles=["admin"])
    viewer = await _add_user(maker, "bea", roles=["viewer"])
    assert _login(client, "bea", PASSWORD).status_code == 200
    assert client.get("/api/v1/offenses").status_code == 200
    # The admin call needs Ada's cookie, so Bea is logged out of this client
    # first. The session row stays; the next login of Bea reads the new roles.
    client.post("/api/v1/auth/logout")
    assert _login(client, "ada", PASSWORD).status_code == 200
    changed = client.put(f"/api/v1/users/{viewer.id}/roles", json={"roles": []})
    assert changed.status_code == 200, changed.text
    assert changed.json()["roles"] == []
    client.post("/api/v1/auth/logout")
    assert _login(client, "bea", PASSWORD).status_code == 200
    denied = client.get("/api/v1/offenses")
    assert denied.status_code == 403
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["roles"] == []
    assert me.json()["permissions"] == []
    del admin


async def test_must_change_password_gates_every_other_route(api) -> None:
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"], must_change=True)
    assert _login(client, "ada", PASSWORD).status_code == 200
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True
    denied = client.get("/api/v1/offenses")
    assert denied.status_code == 403
    assert denied.json()["detail"] == (
        "A password change is required before any other request."
    )
    changed = client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "Replacement-Password-12"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["must_change_password"] is False
    assert PASSWORD not in changed.text
    assert client.get("/api/v1/offenses").status_code == 200
    # The previous password no longer works. Log out first so the cookie is
    # the only credential under test.
    client.post("/api/v1/auth/logout")
    assert _login(client, "ada", PASSWORD).status_code == 401
    assert _login(client, "ada", "Replacement-Password-12").status_code == 200


async def test_last_admin_cannot_be_removed(api) -> None:
    client, maker = api
    ada = await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    stripped = client.put(f"/api/v1/users/{ada.id}/roles", json={"roles": ["viewer"]})
    assert stripped.status_code == 409
    assert "admin:*" in stripped.json()["detail"]
    disabled = client.post(f"/api/v1/users/{ada.id}/disable")
    assert disabled.status_code == 409
    created = client.post(
        "/api/v1/users",
        json={
            "username": "bea",
            "auth_source": "LOCAL",
            "password": "Another-Password-12",
            "roles": ["admin"],
        },
    )
    assert created.status_code == 201, created.text
    assert "password" not in created.json()
    assert created.json()["must_change_password"] is True
    # A second administrator exists, so Ada can leave the role.
    assert client.put(
        f"/api/v1/users/{ada.id}/roles", json={"roles": ["viewer"]}
    ).status_code == 200


async def test_unknown_roles_are_listed_and_duplicates_conflict(api) -> None:
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    bad = client.post(
        "/api/v1/users",
        json={
            "username": "bea",
            "auth_source": "LOCAL",
            "password": "Another-Password-12",
            "roles": ["nope", "also-nope"],
        },
    )
    assert bad.status_code == 422
    assert "unknown role: nope" in bad.text and "unknown role: also-nope" in bad.text
    ldap = client.post(
        "/api/v1/users",
        json={"username": "cara", "auth_source": "LDAP", "password": "nope-nope-nope"},
    )
    assert ldap.status_code == 422
    assert "no password" in ldap.json()["detail"]
    made = client.post(
        "/api/v1/users", json={"username": "cara", "auth_source": "LDAP", "roles": ["viewer"]}
    )
    assert made.status_code == 201, made.text
    assert made.json()["auth_source"] == "LDAP"
    assert made.json()["must_change_password"] is False
    again = client.post(
        "/api/v1/users", json={"username": "cara", "auth_source": "LDAP"}
    )
    assert again.status_code == 409


async def test_audit_rows_carry_no_password(api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    _login(client, "ada", "wrong-password")
    async with maker() as session:
        rows = list(await session.scalars(select(AuditLog)))
    assert rows
    actions = {row.action for row in rows}
    assert "auth.login" in actions
    assert "auth.login_failed" in actions
    for row in rows:
        _assert_no_secret(json.dumps(row.detail), PASSWORD)
        _assert_no_secret(json.dumps(row.detail), "wrong-password")
    assert PASSWORD not in caplog.text


async def test_ldap_login_paths(api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, maker = api
    async with maker() as session:
        session.add(
            LdapConfig(
                id=1, enabled=True, urls=["ldaps://directory.example"], start_tls=False,
                bind_template="{username}@corp.example", timeout_seconds=5, version=1,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    seen: dict[str, object] = {}

    class FakeConnection:
        def __init__(self, server: object, user: str, password: str, **_kwargs: object) -> None:
            seen["user"] = user
            seen["password"] = password
            self.entries: list[object] = []
            if seen.get("mode") == "down":
                self._down = True
            else:
                self._down = False

        def open(self) -> None:
            if self._down:
                from ldap3.core.exceptions import LDAPSocketOpenError

                raise LDAPSocketOpenError("refused")

        def bind(self) -> None:
            if seen.get("mode") == "invalid" or seen["password"] != PASSWORD:
                from ldap3.core.exceptions import LDAPInvalidCredentialsResult

                raise LDAPInvalidCredentialsResult("invalid")
            if seen.get("profile"):
                self.entries = [
                    type(
                        "Entry",
                        (),
                        {"displayName": "Ada Lovelace", "mail": "ada@corp.example"},
                    )()
                ]

        def search(self, **kwargs: object) -> None:
            assert "memberOf" not in (kwargs.get("attributes") or [])
            seen["filter"] = kwargs.get("search_filter")

        def unbind(self) -> None:
            return None

    monkeypatch.setattr("app.services.ldap_bind.Connection", FakeConnection)
    monkeypatch.setattr("app.services.ldap_bind.Server", lambda *_a, **_k: object())

    seen["mode"] = "invalid"
    bad = _login(client, "ada", "not-the-password")
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Invalid username or password"
    assert seen["user"] == "ada@corp.example"

    seen["mode"] = "down"
    down = _login(client, "ada", PASSWORD)
    assert down.status_code == 503
    assert "valid" not in down.json()["detail"].lower()
    async with maker() as session:
        assert await session.scalar(select(User).where(User.username == "ada")) is None

    seen["mode"] = "ok"
    seen["profile"] = True
    async with maker() as session:
        row = await session.get(LdapConfig, 1)
        assert row is not None
        row.user_search_base = "dc=corp,dc=example"
        await session.commit()
    ok = _login(client, "Ada", PASSWORD)
    assert ok.status_code == 200, ok.text
    assert ok.json()["auth_source"] == "LDAP"
    assert ok.json()["roles"] == []
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["username"] == "ada"
    denied = client.get("/api/v1/offenses")
    assert denied.status_code == 403
    async with maker() as session:
        user = await session.scalar(select(User).where(User.username == "ada"))
        assert user is not None
        assert user.hashed_password is None
        assert user.display_name == "Ada Lovelace"
        assert user.email == "ada@corp.example"
        provisions = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "user.provision"))
        )
    assert len(provisions) == 1
    assert seen["filter"] == "(sAMAccountName=ada)"

    # A pre-created account keeps its roles and is not provisioned again.
    client.post("/api/v1/auth/logout")
    await _add_user(maker, "bea", password=None, source=AuthSource.LDAP.value, roles=["analyst"])
    seen["profile"] = False
    bea = _login(client, "bea", PASSWORD)
    assert bea.status_code == 200, bea.text
    assert "analyst" in bea.json()["roles"]
    assert client.get("/api/v1/offenses").status_code == 200
    async with maker() as session:
        provisions = list(
            await session.scalars(select(AuditLog).where(AuditLog.action == "user.provision"))
        )
    assert len(provisions) == 1


async def test_a_local_account_is_not_sent_to_the_directory(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    async with maker() as session:
        session.add(
            LdapConfig(
                id=1, enabled=True, urls=["ldaps://directory.example"], start_tls=False,
                bind_template="{username}", timeout_seconds=5, version=1,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("LDAP was contacted for a local account")

    monkeypatch.setattr("app.services.ldap_bind.Connection", boom)
    wrong = _login(client, "ada", "not-the-password")
    assert wrong.status_code == 401


async def test_cross_site_post_is_refused(api) -> None:
    client, _maker = api
    bare = TestClient(client.app)
    missing = bare.post("/api/v1/auth/login", json={"username": "ada", "password": "x"})
    assert missing.status_code == 403
    assert "no Origin or Referer" in missing.json()["detail"]
    foreign = TestClient(client.app, headers={"Origin": "https://evil.example"})
    refused = foreign.post("/api/v1/auth/login", json={"username": "ada", "password": "x"})
    assert refused.status_code == 403
    assert "evil.example" in refused.json()["detail"]


async def test_production_cookie_is_secure(api) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    app = client.app
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={
            "environment": Environment.PRODUCTION,
            "auth_provider": "session",
            "public_origin": "https://soc.example",
        }
    )
    produced = TestClient(app, headers={"Origin": "https://soc.example"})
    response = produced.post(
        "/api/v1/auth/login", json={"username": "ada", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert "secure" in response.headers["set-cookie"].lower()


async def test_staging_session_cookie_is_secure_and_anonymous_is_not_admin(api) -> None:
    """Staging is the production posture: Secure cookie, 401 without a session, CSRF on."""
    client, maker = api
    await _add_user(maker, "ada")
    app = client.app
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={
            "environment": Environment.STAGING,
            "auth_provider": "local",
            "public_origin": "https://soc.example",
        }
    )
    produced = TestClient(app, headers={"Origin": "https://soc.example"})
    response = produced.post(
        "/api/v1/auth/login", json={"username": "ada", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert "secure" in response.headers["set-cookie"].lower()

    anonymous = TestClient(app)
    denied = anonymous.get("/api/v1/offenses")
    assert denied.status_code == 401, denied.text
    assert denied.json()["detail"] == "Authentication required."

    missing_origin = TestClient(app)
    refused = missing_origin.post(
        "/api/v1/auth/login", json={"username": "ada", "password": PASSWORD}
    )
    assert refused.status_code == 403, refused.text
    assert "no Origin or Referer" in refused.json()["detail"]


async def test_ldap_config_api_and_probe(
    api, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.DEBUG)
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    viewer = await _add_user(maker, "bea", roles=["viewer"])
    assert _login(client, "bea", PASSWORD).status_code == 200
    assert client.get("/api/v1/auth/ldap-config").status_code == 403
    client.post("/api/v1/auth/logout")
    assert _login(client, "ada", PASSWORD).status_code == 200

    current = client.get("/api/v1/auth/ldap-config")
    assert current.status_code == 200, current.text
    assert current.json()["version"] == 0
    assert current.json()["enabled"] is False

    stale = client.put(
        "/api/v1/auth/ldap-config",
        json={
            "enabled": False, "urls": [], "start_tls": False, "bind_template": "{username}",
            "timeout_seconds": 5, "version": 3,
        },
    )
    assert stale.status_code == 409

    saved = client.put(
        "/api/v1/auth/ldap-config",
        json={
            "enabled": False, "urls": [], "start_tls": False, "bind_template": "{username}",
            "timeout_seconds": 5, "version": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    conflict = client.put(
        "/api/v1/auth/ldap-config",
        json={
            "enabled": False, "urls": [], "start_tls": False, "bind_template": "{username}",
            "timeout_seconds": 5, "version": 0,
        },
    )
    assert conflict.status_code == 409

    reasons = client.put(
        "/api/v1/auth/ldap-config",
        json={
            "enabled": True,
            "urls": ["ldap://dc.example"],
            "start_tls": False,
            "ca_pem": "not-a-certificate",
            "bind_template": "cn=user",
            "timeout_seconds": 0,
            "version": 1,
        },
    )
    assert reasons.status_code == 422
    detail = " ".join(reasons.json()["detail"])
    assert "start_tls" in detail
    assert "bind_template" in detail
    assert "timeout_seconds" in detail
    assert "not a PEM" in detail

    material = write_server_material(tmp_path)
    standin = DirectoryStandIn(material[0], material[1], PASSWORD)
    plain = ClosedPort()
    standin.start()
    plain.start()
    try:
        failed = client.post(
            "/api/v1/auth/ldap-config/test",
            json={
                "urls": [f"ldaps://127.0.0.1:{plain.port}"],
                "start_tls": False,
                "ca_pem": material[2],
                "bind_template": "{username}@corp.example",
                "timeout_seconds": 5,
                "username": "ada",
                "password": PASSWORD,
            },
        )
        assert failed.status_code == 200, failed.text
        assert failed.json()["tls"]["status"] == "failed"
        assert PASSWORD not in failed.text

        system = client.post(
            "/api/v1/auth/ldap-config/test",
            json={
                "urls": [f"ldaps://127.0.0.1:{standin.port}"],
                "start_tls": False,
                "bind_template": "{username}@corp.example",
                "timeout_seconds": 5,
                "username": "ada",
                "password": PASSWORD,
            },
        )
        assert system.status_code == 200, system.text
        assert system.json()["tls"]["status"] == "failed"
        assert system.json()["tls"]["trust"] == "system"

        bad_pw = client.post(
            "/api/v1/auth/ldap-config/test",
            json={
                "urls": [f"ldaps://127.0.0.1:{standin.port}"],
                "start_tls": False,
                "ca_pem": material[2],
                "bind_template": "{username}@corp.example",
                "timeout_seconds": 5,
                "username": "ada",
                "password": PASSWORD + "-no",
            },
        )
        assert bad_pw.status_code == 200, bad_pw.text
        assert bad_pw.json()["tls"]["status"] == "ok"
        assert bad_pw.json()["certificate"]["subject"]
        assert bad_pw.json()["certificate"]["issuer"]
        assert bad_pw.json()["certificate"]["not_after"]
        assert bad_pw.json()["bind"]["status"] == "invalid_credentials"
        assert PASSWORD not in bad_pw.text

        good = client.post(
            "/api/v1/auth/ldap-config/test",
            json={
                "urls": [f"ldaps://127.0.0.1:{standin.port}"],
                "start_tls": False,
                "ca_pem": material[2],
                "bind_template": "{username}@corp.example",
                "timeout_seconds": 5,
                "username": "ada",
                "password": PASSWORD,
            },
        )
        assert good.status_code == 200, good.text
        assert good.json()["tls"]["status"] == "ok"
        assert good.json()["tls"]["trust"] == "ca_pem"
        assert good.json()["bind"]["status"] == "ok"
        assert isinstance(good.json()["latency_ms"], int)
        assert PASSWORD not in good.text
    finally:
        standin.stop()
        plain.stop()
        for path in (material[0], material[1]):
            path.unlink(missing_ok=True)

    assert PASSWORD not in caplog.text
    async with maker() as session:
        rows = list(await session.scalars(select(AuditLog)))
        stored = await session.get(LdapConfig, 1)
    for row in rows:
        _assert_no_secret(json.dumps(row.detail), PASSWORD)
    assert stored is not None
    assert PASSWORD not in json.dumps(list(stored.urls))
    # The probe is not an audit event.
    assert all(row.action != "auth.ldap_config.test" for row in rows)
    assert any(row.action == "auth.ldap_config.update" for row in rows)
    del viewer


def test_break_glass_admin_prints_the_password_once(db_schema: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            **_postgres_subprocess_env(db_schema),
            "ENCRYPTION_KEY": "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=",
            "SECRET_KEY": "test-secret-key-at-least-32-characters-long!!",
            "ENVIRONMENT": "development",
            "QRADAR_PROVIDER": "mock",
            "PYTHONPATH": ".",
        }
    )
    command = [sys.executable, "-m", "app.cli.users", "create-admin", "--username", "BreakGlass"]

    def run() -> subprocess.CompletedProcess[str]:
        # This process's interpreter, and a literal argv. The username is not external input.
        return subprocess.run(  # noqa: S603
            command, cwd=_BACKEND, env=env, capture_output=True, text=True, check=False
        )

    first = run()
    assert first.returncode == 0, first.stderr
    assert "password: " in first.stdout
    password = next(
        line.split(": ", 1)[1]
        for line in first.stdout.splitlines()
        if line.startswith("password: ")
    )
    assert password not in first.stderr
    assert "username: breakglass" in first.stdout

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
        signed = _login(client, "breakglass", password)
        assert signed.status_code == 200, signed.text
        assert signed.json()["must_change_password"] is True
        assert "admin" in signed.json()["roles"]
        client.post("/api/v1/auth/logout")
        second = run()
        assert second.returncode == 0, second.stderr
        new_password = next(
            line.split(": ", 1)[1]
            for line in second.stdout.splitlines()
            if line.startswith("password: ")
        )
        assert new_password != password
        assert _login(client, "breakglass", password).status_code == 401
        assert _login(client, "breakglass", new_password).status_code == 200
    finally:
        client.close()
        engine.sync_engine.dispose()


def test_session_mode_refuses_every_route_without_a_cookie(api) -> None:
    client, maker = api
    schema = client.app.openapi()
    open_paths = {"/api/v1/health/live", "/api/v1/health/ready", "/api/v1/auth/login",
                 "/api/v1/version"}
    checked = 0
    for path, operations in schema["paths"].items():
        if path in open_paths:
            continue
        concrete = path.replace("{user_id}", "00000000-0000-0000-0000-000000000001")
        concrete = concrete.replace("{log_source_id}", "00000000-0000-0000-0000-000000000001")
        concrete = concrete.replace("{dashboard_id}", "00000000-0000-0000-0000-000000000001")
        concrete = concrete.replace("{rule_id}", "00000000-0000-0000-0000-000000000001")
        concrete = concrete.replace("{technique_id}", "T1059")
        concrete = concrete.replace("{qradar_offense_id}", "1")
        concrete = concrete.replace("{source_class}", "OTHER")
        concrete = concrete.replace("{instance_id}", "00000000-0000-0000-0000-000000000001")
        if "{" in concrete:
            continue
        for method in operations:
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            checked += 1
            response = client.request(method.upper(), concrete)
            assert response.status_code == 401, f"{method} {concrete} -> {response.status_code}"
    assert checked > 40
    del maker


async def test_a_user_with_no_role_can_read_me_and_nothing_else(api) -> None:
    client, maker = api
    await _add_user(maker, "ada")
    assert _login(client, "ada", PASSWORD).status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    assert client.get("/api/v1/offenses").status_code == 403
    assert client.get("/api/v1/users").status_code == 403
    assert client.get("/api/v1/auth/ldap-config").status_code == 403
    logged_out = client.post("/api/v1/auth/logout")
    assert logged_out.status_code == 204
