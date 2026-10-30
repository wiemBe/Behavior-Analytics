"""Who a request is counted as, behind uvicorn's proxy-headers middleware (RL-1).

Uvicorn rewrites ``scope["client"]`` from ``X-Forwarded-For`` only when the peer is listed in
``FORWARDED_ALLOW_IPS``. The bundled proxy and the frontend container are both listed, so a
server-rendered read is counted under the analyst's own address. Any other peer is not trusted
and its header is ignored.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.rate_limit import limiter

PROXY = "172.28.10.10"
FRONTEND = "172.28.10.11"
TRUSTED = f"{PROXY},{FRONTEND}"


def _app(trusted: str) -> ProxyHeadersMiddleware:
    app = FastAPI()

    @app.get("/key")
    async def key(request: Request) -> dict[str, str]:
        return {"key": limiter._key_func(request)}  # the key the limiter would count under

    return ProxyHeadersMiddleware(app, trusted_hosts=trusted)  # type: ignore[arg-type]


async def _key(peer: str, forwarded: str | None, trusted: str = TRUSTED) -> str:
    transport = httpx.ASGITransport(app=_app(trusted), client=(peer, 40000))  # type: ignore[arg-type]
    headers = {} if forwarded is None else {"X-Forwarded-For": forwarded}
    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as client:
        response = await client.get("/key", headers=headers)
    return str(response.json()["key"])


@pytest.mark.anyio
async def test_the_frontend_peer_is_counted_under_the_forwarded_analyst() -> None:
    first = await _key(FRONTEND, "198.51.100.7")
    second = await _key(FRONTEND, "198.51.100.8")

    assert (first, second) == ("198.51.100.7", "198.51.100.8")


@pytest.mark.anyio
async def test_the_frontend_peer_without_a_header_is_counted_under_itself() -> None:
    assert await _key(FRONTEND, None) == FRONTEND


@pytest.mark.anyio
async def test_a_browser_injected_earlier_hop_does_not_choose_the_key() -> None:
    # The bundled proxy appends the real peer; the frontend forwards that last hop only.
    assert await _key(PROXY, "6.6.6.6, 198.51.100.7") == "198.51.100.7"
    # Even a whole list reaching a trusted peer resolves from the right.
    assert await _key(FRONTEND, "6.6.6.6, 198.51.100.7") == "198.51.100.7"


@pytest.mark.anyio
async def test_an_untrusted_peers_forwarded_header_is_ignored() -> None:
    assert await _key("172.28.10.99", "198.51.100.7") == "172.28.10.99"
    assert await _key("203.0.113.5", "198.51.100.7") == "203.0.113.5"


@pytest.mark.anyio
async def test_with_the_frontend_not_listed_its_header_is_ignored() -> None:
    assert await _key(FRONTEND, "198.51.100.7", trusted=PROXY) == FRONTEND
