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
    # Build identity: version, git SHA and migration head only. An operator who cannot sign in
    # must still read which build is running.
    "/api/v1/version",
    # FastAPI's own schema and docs routes. create_app() mounts them only outside
    # production (docs_url/openapi_url are None there) precisely to avoid
    # advertising the surface; they expose no platform data.
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    # Login is unauthenticated. A missing session is a wrong password, not a
    # missing principal, and the two must not be the same response.
    "/api/v1/auth/login",
}

# Mutating routes that a read-only principal may call. Add (METHOD, path) here
# only with a reason. The Phase 3 writes that sat behind only the router-level
# read permission now check their own write permission (PATCH /rules/{rule_id}
# needs rule:write, POST /coverage/mappings needs coverage:write), so the guard
# test proves 403 for a read-only principal.
#
# Login is unauthenticated, so a read-only bearer token is not a 403: the
# handler answers 401 for a bad password. Logout is how any caller, including
# a read-only one, ends their own session.
#
# Draft preview is POST because the unsaved rule is a body. It reads stored
# buckets, writes nothing, and the router already requires `read:custom_rules`,
# which `read:*` satisfies. `custom_rule:write` is not required.
_READONLY_MAY_MUTATE: set[tuple[str, str]] = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/custom-rules/preview"),
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
    ("PUT", "/api/v1/waf/log-sources/{log_source_id}/profile"): {
        "expected_profile_version": None,
        "product_label": None,
        "mapping": {
            "URL": "URL",
            "METHOD": "HTTP_Method",
            "VIRTUAL_HOST": None,
            "ACTION": None,
            "RESPONSE_CODE": None,
            "RULE_ID": None,
            "USER_AGENT": None,
        },
        "action_value_map": {"BLOCKED": [], "ALLOWED": []},
    },
    ("PUT", "/api/v1/ips/log-sources/{log_source_id}/profile"): {
        "expected_profile_version": None,
        "product_label": None,
        "preset": "CISCO_FTD",
        "mapping": {
            "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
            "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
            "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
            "PROTOCOL": {"kind": "NORMALIZED", "name": "protocolid"},
            "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
            "ACTION": None,
            "SEVERITY": {"kind": "NORMALIZED", "name": "severity"},
        },
        "action_value_map": {"BLOCKED": [], "ALLOWED": []},
    },
    ("POST", "/api/v1/coverage/mappings"): {
        "technique_id": "T1059",
        "rule_id": "00000000-0000-0000-0000-000000000000",
    },
    # Phase C. Both routes check their write permission in the handler, after body
    # validation, so the guard needs a body that passes validation.
    ("POST", "/api/v1/classification-rules"): {
        "kind": "NAME_REGEX",
        "pattern": "^guard-",
        "target_class": "OTHER",
    },
    ("PATCH", "/api/v1/log-sources/{log_source_id}/classification"): {
        "override_class": None,
    },
    # Phase C.2. The outage policy router guards every method at the mount, but
    # supply a valid body so a body-first failure cannot mask a missing guard.
    ("PUT", "/api/v1/outage-policy/classes/{source_class}"): {"mode": "ALERT"},
    ("PUT", "/api/v1/outage-policy/sources/{log_source_id}"): {"mode": "ALERT"},
    # UI redesign dashboards: writes check `dashboard:write` in the handler, after validation.
    ("POST", "/api/v1/dashboards"): {
        "name": "g", "layout": {"v": 1, "range": "24h", "widgets": []},
    },
    ("PUT", "/api/v1/dashboards/default"): {"dashboard_id": None},
    ("PUT", "/api/v1/dashboards/{dashboard_id}"): {
        "name": "g", "layout": {"v": 1, "range": "24h", "widgets": []}, "expected_revision": 1,
    },
    ("PATCH", "/api/v1/dashboards/{dashboard_id}"): {"name": "g"},
    # Custom rules check `custom_rule:write` in the handler, after validation.
    # Preview is allowlisted above: it is a read.
    ("POST", "/api/v1/custom-rules"): {
        "name": "guard",
        "subject_kind": "LOG_SOURCE",
        "metric": "event_count",
        "operator": "GT",
        "threshold_value": 1,
        "sustain_buckets": 1,
        "recovery_buckets": 1,
    },
    ("PATCH", "/api/v1/custom-rules/{rule_id}"): {
        "expected_updated_at": "2026-01-01T00:00:00Z",
    },
    # Auth administration. The read-only principal is refused by the router
    # guard; the bodies are valid so a body-first failure cannot hide that.
    ("POST", "/api/v1/auth/password"): {
        "current_password": "current-password",
        "new_password": "new-password-12",
    },
    ("PUT", "/api/v1/auth/ldap-config"): {
        "enabled": False,
        "urls": [],
        "start_tls": False,
        "ca_pem": None,
        "bind_template": "{username}",
        "timeout_seconds": 5,
        "user_search_base": None,
        "version": 0,
    },
    ("POST", "/api/v1/auth/ldap-config/test"): {
        "urls": ["ldaps://127.0.0.1:1"],
        "start_tls": False,
        "bind_template": "{username}",
        "timeout_seconds": 1,
        "username": "ada",
        "password": "not-used",
    },
    ("POST", "/api/v1/users"): {
        "username": "guard-user",
        "auth_source": "LOCAL",
        "password": "guard-password-12",
    },
    ("PUT", "/api/v1/users/{user_id}/roles"): {"roles": []},
    ("POST", "/api/v1/users/{user_id}/reset-password"): {"password": "guard-password-12"},
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


def test_user_roles_list_is_on_the_openapi_guard() -> None:
    """GET /users/roles is read-only and still closed.

    The parametrized walk already demands 401 for every route. This pins the
    new path by name and demands 403 from a read-only principal, which the
    mutating walk does not cover.
    """
    path = "/api/v1/users/roles"
    assert ("GET", path) in ROUTES
    assert path in _client("oidc").app.openapi()["paths"]
    missing = _client("oidc").get(path)
    assert missing.status_code == 401, missing.text
    readonly = _client("local").get(path, headers={"Authorization": "Bearer read-only"})
    assert readonly.status_code == 403, readonly.text
