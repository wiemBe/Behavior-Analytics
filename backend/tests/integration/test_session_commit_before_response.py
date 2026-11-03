"""The request's session ends before the response is sent (FIX-23).

``get_session`` commits after its ``yield``. With the default ``"request"`` scope
that code ran after the response had gone out, so a client holding the login
cookie could be refused for a row that was not yet committed, and a failed commit
left a 2xx for a write that was rolled back. ``httpx``'s ``ASGITransport`` waits
for the whole app call and cannot see this; a wrapper around the app checks the
database from another connection at the first ``http.response.start`` message.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.core.database as database
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.identity import AuthSession, User
from tests.integration.test_auth_api import ORIGIN, PASSWORD, _add_user, _login

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def stack(db_schema: str, monkeypatch: pytest.MonkeyPatch):
    """The real ``get_session`` (no override) on the test database, and a second,
    independent connection source for the observer."""
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    observer_engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    observer = async_sessionmaker(observer_engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_sessionmaker", maker)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "session", "public_origin": ORIGIN}
    )
    seen: dict[str, list[bool]] = {}

    def watch(method: str, path: str, probe):  # type: ignore[no-untyped-def]
        seen.setdefault(f"{method} {path}", [])

        async def wrapped(scope, receive, send):  # type: ignore[no-untyped-def]
            async def spy(message):  # type: ignore[no-untyped-def]
                if (
                    message["type"] == "http.response.start"
                    and scope["type"] == "http"
                    and scope["method"] == method
                    and scope["path"] == path
                    and message["status"] < 300
                ):
                    async with observer() as session:
                        seen[f"{method} {path}"].append(await probe(session))
                await send(message)

            await app(scope, receive, spy)

        return wrapped

    yield app, maker, seen, watch
    engine.sync_engine.dispose()
    observer_engine.sync_engine.dispose()


async def _session_rows(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(func.count()).select_from(AuthSession)))


async def _created_user_visible(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(User.id).where(User.username == "newbie")))


async def test_login_row_is_committed_before_the_response_starts(stack) -> None:
    _app, maker, seen, watch = stack
    await _add_user(maker, "ada")
    wrapped = watch("POST", "/api/v1/auth/login", _session_rows)
    client = TestClient(wrapped, headers={"Origin": ORIGIN})

    assert _login(client, "ada", PASSWORD).status_code == 200

    assert seen["POST /api/v1/auth/login"] == [True]
    # The cookie works on the very next request.
    assert client.get("/api/v1/me").status_code == 200


async def test_an_authenticated_write_is_committed_before_the_response_starts(stack) -> None:
    _app, maker, seen, watch = stack
    await _add_user(maker, "ada", roles=["admin"])
    wrapped = watch("POST", "/api/v1/users", _created_user_visible)
    client = TestClient(wrapped, headers={"Origin": ORIGIN})
    assert _login(client, "ada", PASSWORD).status_code == 200

    created = client.post(
        "/api/v1/users",
        json={"username": "newbie", "auth_source": "LOCAL", "password": PASSWORD, "roles": []},
    )

    assert created.status_code == 201, created.text
    assert seen["POST /api/v1/users"] == [True]


async def test_a_commit_that_fails_is_an_error_response(
    stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, maker, _seen, _watch = stack
    await _add_user(maker, "ada", roles=["admin"])
    client = TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=False)
    assert _login(client, "ada", PASSWORD).status_code == 200

    real_commit = AsyncSession.commit

    async def failing_commit(self: AsyncSession) -> None:
        if self.sync_session.info.get("flushed_newbie"):
            raise RuntimeError("commit refused")
        await real_commit(self)

    def mark(session, _context):  # type: ignore[no-untyped-def]
        if any(isinstance(o, User) and o.username == "newbie" for o in session.new):
            session.info["flushed_newbie"] = True

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    event.listen(Session, "before_flush", mark)
    try:
        created = client.post(
            "/api/v1/users",
            json={"username": "newbie", "auth_source": "LOCAL", "password": PASSWORD, "roles": []},
        )
    finally:
        event.remove(Session, "before_flush", mark)

    assert created.status_code >= 500, created.text
    async with maker() as session:
        assert await session.scalar(select(User.id).where(User.username == "newbie")) is None


async def test_a_failed_login_still_commits_the_lockout(stack) -> None:
    """The failure path commits before it raises (also pinned by
    ``test_auth_api.test_lockout_and_unlock_after_the_window``)."""
    app, maker, _seen, _watch = stack
    await _add_user(maker, "ada")
    client = TestClient(app, headers={"Origin": ORIGIN})

    for _ in range(3):
        assert _login(client, "ada", "wrong-password").status_code == 401

    async with maker() as session:
        user = await session.scalar(select(User).where(User.username == "ada"))
        assert user is not None and user.failed_login_count == 3


async def test_the_429_body_names_the_rate_not_an_object(
    stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, maker, _seen, _watch = stack
    await _add_user(maker, "ada")
    monkeypatch.setattr(get_settings(), "rate_limit_login", "2/minute")
    client = TestClient(app, headers={"Origin": ORIGIN})

    _login(client, "ada", "wrong-password")
    _login(client, "ada", "wrong-password")
    refused = _login(client, "ada", "wrong-password")

    assert refused.status_code == 429, refused.text
    body = refused.json()
    assert body["detail"] == "rate limit exceeded"
    assert body["limit"] == "2 per 1 minute"
    assert "object at 0x" not in refused.text
    assert "Retry-After" in refused.headers
