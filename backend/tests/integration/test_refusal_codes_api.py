"""MSG-1: the refusals an analyst sees carry a message code beside their English sentence.

Every assertion reads the JSON body a client receives: `detail` as before, and the code
siblings (`detail_code` / `detail_params`, `detail_codes`, or `code` / `params` inside an
object detail).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.models.identity import User
from app.security.rbac import Principal
from tests.integration.test_auth_api import PASSWORD, _add_user, _login, api  # noqa: F401
from tests.integration.test_dashboards_api import (
    ALICE,
    ALICE_SHARING,
    BASE,
    BOB,
    NOTHING,
    create,
    layout,
    put,
    share,
)
from tests.integration.test_fleet_api import Client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the refusal-code integration tests",
    ),
]


@pytest.fixture(autouse=True)
def _schema(db_schema):  # type: ignore[no-untyped-def]
    return db_schema


def _coded(body: dict[str, Any], code: str, params: dict[str, Any] | None = None) -> None:
    assert body["detail_code"] == code
    assert body["detail_params"] == (params or {})


# --- the permission refusal ----------------------------------------------------------------


def test_the_permission_guard_of_a_router_names_the_permission() -> None:
    response = Client(NOTHING).get("/api/v1/log-sources")
    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "missing required permission: read:log_sources"
    _coded(body, "auth.permission.missing", {"permission": "read:log_sources"})


def test_a_route_level_guard_names_the_permission() -> None:
    reader = Client(Principal(subject="r", permissions=frozenset({"read:*"})))
    response = reader.post(BASE, json={"name": "x", "layout": layout()})
    assert response.status_code == 403
    _coded(response.json(), "auth.permission.missing", {"permission": "dashboard:write"})


def test_a_guard_that_resolves_an_alias_names_the_permission_it_checked() -> None:
    response = Client(NOTHING).get("/api/v1/custom-rules")
    assert response.status_code == 403
    body = response.json()
    assert body["detail_code"] == "auth.permission.missing"
    assert body["detail"] == f"missing required permission: {body['detail_params']['permission']}"


# --- dashboards ----------------------------------------------------------------------------


def test_dashboard_refusals_are_coded() -> None:
    alice = Client(ALICE)
    missing = alice.get(f"{BASE}/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "dashboard not found"
    _coded(missing.json(), "dashboard.not_found")

    made = create(alice, "Main")
    again = alice.post(BASE, json={"name": "Main", "layout": layout()})
    assert again.status_code == 409
    assert again.json()["detail"] == "you already have a dashboard named 'Main'"
    _coded(again.json(), "dashboard.name_taken", {"name": "Main"})

    stale = put(alice, made, name="Renamed")
    assert stale.status_code == 200
    changed = put(alice, made)  # the old revision
    assert changed.status_code == 409
    detail = changed.json()["detail"]
    assert detail["message"] == "this dashboard changed since you opened it"
    assert detail["current_revision"] == 2
    assert detail["code"] == "dashboard.changed_since_opened" and detail["params"] == {}
    assert "detail_code" not in changed.json()

    plain = alice.patch(f"{BASE}/{made['id']}", json={"visibility": "SHARED"})
    assert plain.status_code == 403
    _coded(
        plain.json(),
        "dashboard.visibility_needs_permission",
        {"permission": "admin:dashboards"},
    )


def test_the_owner_only_and_limit_refusals_are_coded() -> None:
    alice = Client(ALICE)
    made = create(alice, "Mine")
    share(Client(ALICE_SHARING), made)
    shared = Client(BOB).get(f"{BASE}/{made['id']}").json()
    refused = put(Client(BOB), shared)
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "only the owner or an administrator can change this dashboard"
    )
    _coded(refused.json(), "dashboard.owner_only")

    for index in range(19):
        create(alice, f"d{index}")
    over = alice.post(BASE, json={"name": "one-too-many", "layout": layout()})
    assert over.status_code == 409
    assert over.json()["detail"] == "the limit is 20 dashboards per owner"
    _coded(over.json(), "dashboard.owner_limit", {"limit": 20})


# --- accounts, sign-in, the session --------------------------------------------------------


async def test_sign_in_and_session_refusals_are_coded(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "ada")
    wrong = _login(client, "ada", "not-the-password")
    assert wrong.status_code == 401
    _coded(wrong.json(), "auth.login.invalid_credentials")
    client.cookies.clear()
    anonymous = client.get("/api/v1/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "Authentication required."
    _coded(anonymous.json(), "auth.session.required")


async def test_the_cross_site_refusal_is_coded(api) -> None:  # noqa: F811
    client, _maker = api
    login = {"username": "a", "password": "b"}
    missing = TestClient(client.app).post("/api/v1/auth/login", json=login)
    assert missing.status_code == 403
    _coded(missing.json(), "auth.csrf.origin_missing")
    foreign = TestClient(client.app, headers={"Origin": "https://evil.example"}).post(
        "/api/v1/auth/login", json=login
    )
    assert foreign.status_code == 403
    _coded(foreign.json(), "auth.csrf.origin_mismatch", {"origin": "https://evil.example"})


async def test_the_forced_password_change_gate_is_coded(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"], must_change=True)
    assert _login(client, "ada", PASSWORD).status_code == 200
    denied = client.get("/api/v1/offenses")
    assert denied.status_code == 403
    _coded(denied.json(), "auth.session.must_change_password")


async def test_a_viewer_is_refused_an_admin_route_with_the_permission_named(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "bea", roles=["viewer"])
    assert _login(client, "bea", PASSWORD).status_code == 200
    refused = client.get("/api/v1/users")
    assert refused.status_code == 403
    _coded(refused.json(), "auth.permission.missing", {"permission": "admin:users"})


async def test_the_password_policy_is_a_coded_list_on_every_path(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    short = client.post(
        "/api/v1/users",
        json={"username": "bea", "auth_source": "LOCAL", "password": "short", "roles": ["viewer"]},
    )
    assert short.status_code == 422
    assert short.json()["detail"] == ["password must be at least 12 characters"]
    assert short.json()["detail_codes"] == [{"code": "auth.password.too_short", "params": {}}]
    assert "detail_code" not in short.json()

    same = client.post(
        "/api/v1/users",
        json={"username": "x" * 12, "auth_source": "LOCAL", "password": "x" * 12},
    )
    assert same.json()["detail_codes"] == [{"code": "auth.password.equals_username", "params": {}}]

    bea = await _add_user(maker, "bea", roles=["viewer"])
    reset = client.post(f"/api/v1/users/{bea.id}/reset-password", json={"password": "tiny"})
    assert reset.status_code == 422
    assert reset.json()["detail_codes"][0]["code"] == "auth.password.too_short"

    changed = client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "tiny"},
    )
    assert changed.status_code == 422
    assert changed.json()["detail_codes"] == [{"code": "auth.password.too_short", "params": {}}]
    wrong = client.post(
        "/api/v1/auth/password",
        json={"current_password": "not-it", "new_password": "Replacement-Password-12"},
    )
    assert wrong.status_code == 401
    _coded(wrong.json(), "auth.password.current_wrong")


async def test_account_administration_refusals_are_coded(api) -> None:  # noqa: F811
    client, maker = api
    ada = await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    last = client.post(f"/api/v1/users/{ada.id}/disable")
    assert last.status_code == 409
    _coded(last.json(), "auth.user.last_admin")
    unknown = client.post(
        "/api/v1/users",
        json={"username": "bea", "auth_source": "LOCAL", "password": "Another-Password-12",
              "roles": ["nope", "also-nope"]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail_codes"] == [
        {"code": "auth.user.role_unknown", "params": {"name": "nope"}},
        {"code": "auth.user.role_unknown", "params": {"name": "also-nope"}},
    ]
    shape = client.post(
        "/api/v1/users",
        json={"username": "has space", "auth_source": "LOCAL", "password": "Another-Password-12"},
    )
    _coded(shape.json(), "auth.username.shape")
    ldap = client.post(
        "/api/v1/users",
        json={"username": "cara", "auth_source": "LDAP", "password": "nope-nope-nope"},
    )
    _coded(ldap.json(), "auth.user.ldap_no_password")
    assert client.post(
        "/api/v1/users", json={"username": "cara", "auth_source": "LDAP"}
    ).status_code == 201
    again = client.post("/api/v1/users", json={"username": "cara", "auth_source": "LDAP"})
    assert again.status_code == 409
    _coded(again.json(), "auth.user.username_taken")
    gone = client.post("/api/v1/users/00000000-0000-0000-0000-000000000001/enable")
    assert gone.status_code == 404
    _coded(gone.json(), "auth.user.not_found")


async def test_an_ldap_account_is_told_to_change_its_password_in_the_directory(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    async with maker() as session:
        await session.execute(
            update(User)
            .where(User.username == "ada")
            .values(auth_source="LDAP", hashed_password=None)
        )
        await session.commit()
    refused = client.post(
        "/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "x" * 14}
    )
    assert refused.status_code == 403
    _coded(refused.json(), "auth.password.ldap_directory")


async def test_directory_configuration_refusals_are_coded(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    assert _login(client, "ada", PASSWORD).status_code == 200
    base = {"enabled": False, "urls": [], "start_tls": False, "bind_template": "{username}",
            "timeout_seconds": 5}
    stale = client.put("/api/v1/auth/ldap-config", json={**base, "version": 3})
    assert stale.status_code == 409
    _coded(stale.json(), "auth.directory.stale")
    invalid = client.put(
        "/api/v1/auth/ldap-config",
        json={**base, "enabled": True, "urls": ["ldap://dc.example"], "bind_template": "cn=user",
              "timeout_seconds": 0, "ca_pem": "junk", "version": 0},
    )
    assert invalid.status_code == 422
    assert [item["code"] for item in invalid.json()["detail_codes"]] == [
        "auth.directory.ldap_needs_start_tls",
        "auth.directory.template_username",
        "auth.directory.timeout_range",
        "auth.directory.ca_invalid",
    ]
    assert len(invalid.json()["detail"]) == 4
    urls = client.put(
        "/api/v1/auth/ldap-config",
        json={**base, "urls": [f"ldaps://d{i}" for i in range(9)], "version": 0},
    )
    assert urls.json()["detail_codes"][0] == {
        "code": "auth.directory.urls_max",
        "params": {"max": 8},
    }
    probe = client.post(
        "/api/v1/auth/ldap-config/test",
        json={**base, "username": "ada", "password": PASSWORD},
    )
    assert probe.status_code == 422
    assert probe.json()["detail_codes"] == [{"code": "auth.directory.test_needs_url", "params": {}}]
