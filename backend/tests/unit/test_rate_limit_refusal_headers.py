"""Counted refusals carry the same window a 200 does (FIX-14, BL-1 #29).

The default-limit dependency counts the request and writes ``X-RateLimit-*``
onto the dependency ``Response``. FastAPI copies those headers when the route
returns. A refusal builds a fresh response. These tests go through
``create_app`` and fail if a counted 401, coded 403, 404 or validation 422
drops the window, if a 429 loses ``Retry-After``, or if ``/health/ready``
grows any of the headers.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.core.version import migration_head
from app.main import create_app

_WINDOW = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
_ORIGIN = "http://localhost:3000"
_MISSING_ALERT = "00000000-0000-0000-0000-000000000099"


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


async def _session():  # type: ignore[no-untyped-def]
    """A session that finds nothing. Readiness sees the image head."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.scalar = AsyncMock(return_value=0)
    page = MagicMock()
    page.all.return_value = []
    session.scalars = AsyncMock(return_value=page)

    async def execute(statement: object) -> MagicMock:
        result = MagicMock()
        if "version_num" in str(statement):
            result.all.return_value = [[migration_head()]]
        else:
            result.all.return_value = []
        return result

    session.execute = execute
    yield session


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def _window(response: httpx.Response, *, limit: str, remaining: str) -> str:
    found = {name: response.headers.get(name) for name in _WINDOW}
    assert found["X-RateLimit-Limit"] == limit, (response.status_code, found)
    assert found["X-RateLimit-Remaining"] == remaining, (response.status_code, found)
    reset = found["X-RateLimit-Reset"]
    assert reset is not None, (response.status_code, found)
    # slowapi writes the window end as a number; the fractional part is its own.
    float(reset)
    return reset


def _counted_like(refusal: httpx.Response, followed: httpx.Response, *, limit: str) -> None:
    """The refusal and the following 200 name one limit; each is its path's first hit."""
    refusal_reset = _window(refusal, limit=limit, remaining="4")
    assert followed.status_code == 200, followed.text
    followed_reset = _window(followed, limit=limit, remaining="4")
    assert abs(float(refusal_reset) - float(followed_reset)) <= 2
    # Retry-After belongs to the 429. A counted refusal and the following 200
    # carry the window only.
    assert "Retry-After" not in refusal.headers
    assert "Retry-After" not in followed.headers


def test_a_credential_less_logout_carries_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "5/minute")
    monkeypatch.setattr(get_settings(), "auth_provider", "session")
    with _client() as client:
        refused = client.post("/api/v1/auth/logout", headers={"Origin": _ORIGIN})
        followed = client.get("/api/v1/version")
        again = client.post("/api/v1/auth/logout", headers={"Origin": _ORIGIN})

    assert refused.status_code == 401, refused.text
    assert refused.json()["detail_code"] == "auth.session.required"
    _counted_like(refused, followed, limit="5")
    # The logout path's own bucket moved; the following /version did not share it.
    assert _window(again, limit="5", remaining="3") == _window(refused, limit="5", remaining="4")
    assert "Retry-After" not in again.headers


def test_a_coded_403_carries_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "5/minute")
    body = {"current_password": "old-password", "new_password": "new-password"}
    with _client() as client:
        refused = client.post("/api/v1/auth/password", json=body)
        followed = client.get("/api/v1/version")

    assert refused.status_code == 403, refused.text
    assert refused.json()["detail_code"] == "auth.password.local_only"
    _counted_like(refused, followed, limit="5")


def test_a_404_carries_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "5/minute")
    with _client() as client:
        refused = client.get(f"/api/v1/alerts/{_MISSING_ALERT}")
        followed = client.get("/api/v1/version")

    assert refused.status_code == 404, refused.text
    assert refused.json()["detail"] == "alert not found"
    _counted_like(refused, followed, limit="5")


def test_a_validation_422_carries_the_window_of_the_following_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "5/minute")
    with _client() as client:
        # Query validation runs after the router dependency, so the 422 is counted
        # on the same path the following list uses.
        refused = client.get("/api/v1/alerts", params={"limit": 0})
        followed = client.get("/api/v1/alerts")

    assert refused.status_code == 422, refused.text
    assert "Retry-After" not in refused.headers
    refusal_reset = _window(refused, limit="5", remaining="4")
    assert followed.status_code == 200, followed.text
    assert "Retry-After" not in followed.headers
    assert _window(followed, limit="5", remaining="3") == refusal_reset


def test_a_429_still_names_the_window_and_when_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "2/minute")
    with _client() as client:
        first = client.get("/api/v1/version")
        second = client.get("/api/v1/version")
        third = client.get("/api/v1/version")

    assert first.status_code == second.status_code == 200
    assert _window(first, limit="2", remaining="1")
    assert "Retry-After" not in first.headers
    assert third.status_code == 429, third.text
    assert third.json()["detail"] == "rate limit exceeded"
    assert "limit" in third.json()
    assert _window(third, limit="2", remaining="0")
    assert "Retry-After" in third.headers


def test_readiness_carries_no_rate_limit_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")
    with _client() as client:
        polls = [client.get("/api/v1/health/ready") for _ in range(3)]

    assert [response.status_code for response in polls] == [200, 200, 200]
    for response in polls:
        assert response.json()["status"] == "ready"
        for name in (*_WINDOW, "Retry-After"):
            assert name not in response.headers
