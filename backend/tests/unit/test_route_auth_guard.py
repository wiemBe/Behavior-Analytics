"""Every route the app serves must require an authenticated principal.

Routes are enumerated from the live `create_app()` object, not from files, so a
route added to any module -- or mounted without a router-level `requires(...)`
-- is picked up automatically and fails here until it is guarded or explicitly
allowlisted with a reason.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Paths that may be served without a principal. Each entry needs a reason.
_OPEN_PATHS = {
    # Container liveness/readiness probes carry no credentials and return no data
    # beyond "ok"/"ready".
    "/api/v1/health/live",
    "/api/v1/health/ready",
    # FastAPI's own schema and docs routes. create_app() mounts them only outside
    # production (docs_url/openapi_url are None there) precisely to avoid
    # advertising the surface; they expose no platform data.
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
}

# Mutating routes that a read-only principal may call. Add (METHOD, path) here
# only with a reason.
#
# KNOWN GAP, out of T-028's file scope (reported for a follow-up task): these two
# Phase 3 writes sit behind only the router-level read permission, so any
# `read:*` principal can edit SOC rule metadata or add a technique mapping. They
# need their own write permission. Remove the entries when that lands.
_READONLY_MAY_MUTATE: set[tuple[str, str]] = {
    ("PATCH", "/api/v1/rules/{rule_id}"),
    ("POST", "/api/v1/coverage/mappings"),
}

# Minimal valid bodies. A handler-level permission check runs after body
# validation, so a mutating route with a required body needs one here to reach
# its 403. A new mutating route without an entry fails with a 422 and a message.
_BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/api/v1/searches"): {
        "name": "guard",
        "aql_query": "SELECT sourceip FROM events LAST 1 HOURS",
        "schedule_cron": "*/5 * * * *",
    },
}

_ID = str(uuid.uuid4())


def _walk(items):  # type: ignore[no-untyped-def]
    """Flatten the app's route tree. FastAPI nests included routers lazily."""
    for r in items:
        if hasattr(r, "effective_candidates"):
            yield from _walk(r.effective_candidates())
            yield from _walk(r.effective_low_priority_routes())
        else:
            yield r


def _routes() -> list[tuple[str, str]]:
    app = create_app()
    found: set[tuple[str, str]] = set()
    for r in _walk(app.routes):
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", None)
        # A Mount, Host or websocket would need its own guard story; refuse to
        # let one slip past unexamined.
        assert methods and path, f"unclassifiable route {r!r}: extend this test"
        for m in methods - {"HEAD", "OPTIONS"}:
            found.add((m, path))
    # Cross-check the private-API walker against the public OpenAPI schema.
    documented = {
        (m.upper(), p)
        for p, ops in app.openapi()["paths"].items()
        for m in ops
    }
    assert documented <= found, f"walker missed routes: {sorted(documented - found)}"
    assert len(found) > 40
    return sorted(found)


ROUTES = _routes()


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", _ID, path)


async def _no_session():
    yield None


def _client(auth_provider: str) -> TestClient:
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": auth_provider})
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = _no_session
    return TestClient(app, raise_server_exceptions=False)


def test_route_set_includes_known_surfaces() -> None:
    paths = {p for _, p in ROUTES}
    for expected in (
        "/api/v1/log-sources", "/api/v1/log-sources/sync", "/api/v1/overview",
        "/api/v1/searches", "/api/v1/alerts", "/api/v1/offenses", "/api/v1/waf/sources",
    ):
        assert any(p.startswith(expected) for p in paths), expected


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_oidc_without_token_is_401(method: str, path: str) -> None:
    if path in _OPEN_PATHS:
        pytest.skip("allowlisted open path")
    resp = _client("oidc").request(method, _url(path))
    # 401, specifically: _oidc_principal rejects a missing token before it
    # reaches the Phase 4 501, and an unguarded route would answer 200/404/422/500.
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] in _MUTATING])
def test_readonly_token_cannot_mutate(method: str, path: str) -> None:
    if (method, path) in _READONLY_MAY_MUTATE:
        pytest.skip("allowlisted")
    kwargs = {}
    if (method, path) in _BODIES:
        kwargs["json"] = _BODIES[(method, path)]
    elif method in {"POST", "PATCH", "PUT"}:
        kwargs["json"] = {}
    resp = _client("local").request(
        method, _url(path), headers={"Authorization": "Bearer read-only"}, **kwargs
    )
    assert resp.status_code == 403, (
        f"{method} {path} -> {resp.status_code}; if 422, add a valid body to _BODIES"
    )


def test_open_paths_are_all_real() -> None:
    """An allowlist entry for a route that no longer exists is dead weight."""
    paths = {p for _, p in ROUTES}
    assert paths >= _OPEN_PATHS
