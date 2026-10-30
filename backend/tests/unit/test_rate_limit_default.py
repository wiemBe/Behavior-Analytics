"""The default rate limit is applied to every API route (FIX-8).

slowapi 0.1.10's ``SlowAPIMiddleware`` could not see through FastAPI 0.143's
lazily included routers: it resolved no ``endpoint`` for any API route and
treated "no handler" as exempt, so ``RATE_LIMIT_DEFAULT`` never counted a
request. These tests send N+1 requests through the real ``create_app()`` and
fail if the default limit stops being applied again.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.main import create_app

# The edge peers of docker-compose.prod.yml; uvicorn trusts the proxy's
# X-Forwarded-For, which is how every analyst gets a key of their own (RL-1).
PROXY = "172.28.10.10"


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


def test_an_undecorated_route_refuses_request_n_plus_1_at_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "2/minute")
    with TestClient(create_app()) as client:
        first = client.get("/api/v1/version")
        second = client.get("/api/v1/version")
        third = client.get("/api/v1/version")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    # slowapi writes the window on the served and on the refused response alike.
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"
    assert third.headers["X-RateLimit-Limit"] == "2"
    assert "Retry-After" in third.headers


def test_the_default_counts_each_path_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "2/minute")
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/version").status_code == 200
        assert client.get("/api/v1/version").status_code == 200
        # A second path from the same client has its own bucket, so the
        # exhausted /version window does not follow the caller to /me.
        assert client.get("/api/v1/me").status_code == 200
        assert client.get("/api/v1/version").status_code == 429
        assert client.get("/api/v1/me").status_code == 200


async def test_two_forwarded_clients_have_separate_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")
    app = ProxyHeadersMiddleware(create_app(), trusted_hosts=PROXY)  # type: ignore[arg-type]

    async def windows(analyst: str) -> list[int]:
        transport = httpx.ASGITransport(app=app, client=(PROXY, 40000))
        headers = {"X-Forwarded-For": analyst}
        async with httpx.AsyncClient(transport=transport, base_url="http://backend") as client:
            first = await client.get("/api/v1/version", headers=headers)
            second = await client.get("/api/v1/version", headers=headers)
        return [first.status_code, second.status_code]

    # Both analysts arrive from the same trusted peer; uvicorn resolves them to
    # their own addresses, so neither inherits the other's exhausted window.
    assert await windows("198.51.100.7") == [200, 429]
    assert await windows("198.51.100.8") == [200, 429]


def test_the_liveness_probe_is_not_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")
    with TestClient(create_app()) as client:
        statuses = [client.get("/api/v1/health/live").status_code for _ in range(4)]

    # Docker polls this path on an interval with no credentials; a probe must
    # never be refused for polling.
    assert statuses == [200, 200, 200, 200]
