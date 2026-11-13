"""Notification rule admin API, the destination-in-use guard and the loader."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select, update

from app.alerts.notification_rules import RuleView, load_rules, match_rules
from app.api.routes import notification_destinations as dest_routes
from app.models.identity import AuditLog
from app.models.notification_destination import NotificationDestination
from tests.integration.test_fleet_api import ADMIN, NOTHING, VIEWER, Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

RULES = "/api/v1/admin/notifications/rules"
DESTS = "/api/v1/admin/notifications/destinations"
MISSING = "00000000-0000-0000-0000-000000000000"
SECRET_URL_TOKEN = "path-secret-5521"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dest_routes, "_resolver", lambda host, port: ("93.184.216.34",))


def _dest(client: Client, name: str = "SOAR", **over: object) -> str:
    body = {"name": name, "url": f"https://hooks.example/{SECRET_URL_TOKEN}", **over}
    response = client.post(DESTS, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _rule(client: Client, dest: str, name: str = "R", **over: object):  # type: ignore[no-untyped-def]
    body = {"name": name, "min_severity": "HIGH", "destination_ids": [dest], **over}
    return client.post(RULES, json=body)


def _audits() -> list[AuditLog]:
    async def query(session):  # type: ignore[no-untyped-def]
        rows = await session.scalars(
            select(AuditLog)
            .where(AuditLog.action.like("notification_rule.%"))
            .order_by(AuditLog.created_at, AuditLog.action)
        )
        return list(rows.all())

    return run(query)


def _code(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["detail_code"]


def test_every_route_needs_admin_notifications(db_schema) -> None:
    admin = Client(ADMIN)
    dest = _dest(admin)
    for principal in (VIEWER, NOTHING):
        client = Client(principal)
        checks = [
            client.get(RULES),
            client.post(
                RULES, json={"name": "g", "min_severity": "LOW", "destination_ids": [dest]}
            ),
            client.get(f"{RULES}/{MISSING}"),
            client.patch(f"{RULES}/{MISSING}", json={"enabled": False}),
            client.delete(f"{RULES}/{MISSING}"),
        ]
        assert {r.status_code for r in checks} == {403}
        assert all("admin:notifications" in r.text for r in checks)
    assert admin.get(RULES).json() == []
    assert _audits() == []


def test_unauthenticated_is_401(db_schema) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get(RULES).status_code == 401


def test_crud_roundtrip_names_destinations_and_audits_without_secrets(db_schema) -> None:
    admin = Client(ADMIN)
    d1 = _dest(admin, "SOAR", hmac_secret="hmac-secret-zz91")
    d2 = _dest(admin, "Pager")
    created = _rule(
        admin,
        d1,
        "Critical to SOAR",
        min_severity="CRITICAL",
        priority=5,
        families=["waf", "waf", "ips"],
        owners=["payments"],
        source_classes=[],
        group_ids=[7, 9],
        window_start_minute=1320,
        window_end_minute=360,
        destination_ids=[d1, d2, d1],
        stop_default=True,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["destinations"] == [{"id": d1, "name": "SOAR"}, {"id": d2, "name": "Pager"}]
    assert body["families"] == ["waf", "ips"] and body["source_classes"] is None
    assert body["stop_default"] is True and body["group_ids"] == [7, 9]
    assert "destination_ids" not in body
    rule_id = body["id"]

    assert admin.get(f"{RULES}/{rule_id}").json() == body
    assert admin.get(RULES).json() == [body]

    patched = admin.patch(
        f"{RULES}/{rule_id}",
        json={
            "enabled": False,
            "owners": None,
            "window_start_minute": None,
            "window_end_minute": None,
            "destination_ids": [d2],
            "min_severity": "LOW",
        },
    )
    assert patched.status_code == 200, patched.text
    p = patched.json()
    assert p["enabled"] is False and p["owners"] is None and p["window_start_minute"] is None
    assert p["destinations"] == [{"id": d2, "name": "Pager"}] and p["min_severity"] == "LOW"
    assert p["families"] == ["waf", "ips"]  # untouched fields are kept

    assert admin.delete(f"{RULES}/{rule_id}").status_code == 204
    assert admin.get(f"{RULES}/{rule_id}").status_code == 404
    assert _code(admin.get(f"{RULES}/{rule_id}")) == "notifications.rule_unknown"

    audits = _audits()
    assert [a.action for a in audits] == [
        "notification_rule.create",
        "notification_rule.delete",
        "notification_rule.patch",
    ] or [a.action for a in audits] == [
        "notification_rule.create",
        "notification_rule.patch",
        "notification_rule.delete",
    ]
    text = json.dumps([a.detail for a in audits])
    for forbidden in ("hmac-secret-zz91", SECRET_URL_TOKEN, "hooks.example"):
        assert forbidden not in text
    create = next(a for a in audits if a.action.endswith(".create"))
    assert create.object_id == rule_id
    assert create.detail["destinations"][0] == {"id": f"urn:uuid:{d1}", "name": "SOAR"}


def test_validation_refusals(db_schema) -> None:
    admin = Client(ADMIN)
    d = _dest(admin)
    ok = _rule(admin, d, "first")
    assert ok.status_code == 201

    dup = _rule(admin, d, "first")
    assert dup.status_code == 409 and _code(dup) == "notifications.rule_name_taken"

    unknown = _rule(admin, d, "u", destination_ids=[d, MISSING])
    assert unknown.status_code == 422 and _code(unknown) == "notifications.destination_unknown"

    for start, end in ((60, 60), (60, None), (None, 60), (-1, 5), (5, 1440)):
        r = _rule(admin, d, "w", window_start_minute=start, window_end_minute=end)
        assert r.status_code == 422, (start, end)
        assert _code(r) == "notifications.rule_window_invalid", (start, end)

    assert _rule(admin, d, "s", min_severity="INFO").status_code == 422
    assert _rule(admin, d, "f", families=["nope"]).status_code == 422
    assert _rule(admin, d, "e", bogus=1).status_code == 422
    assert _rule(admin, d, "n", destination_ids=[]).status_code == 422
    many = [str(uuid.uuid4()) for _ in range(11)]
    assert _rule(admin, d, "m", destination_ids=many).status_code == 422

    rule_id = ok.json()["id"]
    assert admin.patch(f"{RULES}/{rule_id}", json={"enabled": None}).status_code == 422
    assert admin.patch(f"{RULES}/{rule_id}", json={"nope": 1}).status_code == 422
    same = {"window_start_minute": 10, "window_end_minute": 10}
    bad = admin.patch(f"{RULES}/{rule_id}", json=same)
    assert _code(bad) == "notifications.rule_window_invalid"
    # a patch that leaves only one half of the window is refused too
    half = admin.patch(f"{RULES}/{rule_id}", json={"window_start_minute": 10})
    assert _code(half) == "notifications.rule_window_invalid"
    gone = admin.patch(f"{RULES}/{rule_id}", json={"destination_ids": [MISSING]})
    assert _code(gone) == "notifications.destination_unknown"
    taken = _rule(admin, d, "second")
    clash = admin.patch(f"{RULES}/{taken.json()['id']}", json={"name": "first"})
    assert clash.status_code == 409 and _code(clash) == "notifications.rule_name_taken"
    assert admin.patch(f"{RULES}/{MISSING}", json={"enabled": True}).status_code == 404
    assert admin.delete(f"{RULES}/{MISSING}").status_code == 404


def test_the_51st_rule_is_refused(db_schema) -> None:
    admin = Client(ADMIN)
    d = _dest(admin)
    for i in range(50):
        assert _rule(admin, d, f"rule-{i}").status_code == 201
    over = _rule(admin, d, "rule-50")
    assert over.status_code == 422 and _code(over) == "notifications.rule_limit"
    assert len(admin.get(RULES).json()) == 50


def test_destination_in_use_cannot_be_deleted(db_schema) -> None:
    admin = Client(ADMIN)
    used = _dest(admin, "Used")
    free = _dest(admin, "Free")
    rule = _rule(admin, used, "r").json()["id"]
    blocked = admin.delete(f"{DESTS}/{used}")
    assert blocked.status_code == 409 and _code(blocked) == "notifications.destination_in_use"
    assert "Used" in blocked.json()["detail"]
    assert admin.get(f"{DESTS}/{used}").status_code == 200
    assert admin.delete(f"{DESTS}/{free}").status_code == 204
    assert admin.delete(f"{RULES}/{rule}").status_code == 204
    assert admin.delete(f"{DESTS}/{used}").status_code == 204


def test_loader_uses_one_query_and_skips_disabled(db_schema) -> None:
    admin = Client(ADMIN)
    live = _dest(admin, "Live")
    off = _dest(admin, "Off", enabled=False)
    for i in range(6):
        assert _rule(admin, live, f"r{i}", priority=i).status_code == 201
    assert _rule(admin, live, "disabled", enabled=False).status_code == 201
    assert _rule(admin, off, "only-off", stop_default=True).status_code == 201
    mixed = _rule(admin, live, "mixed", destination_ids=[live, off], priority=50)
    assert mixed.status_code == 201

    counted: list[str] = []

    async def work(session):  # type: ignore[no-untyped-def]
        @event.listens_for(session.bind.sync_engine, "before_cursor_execute")
        def _count(conn, cursor, statement, *a):  # type: ignore[no-untyped-def]
            counted.append(statement)

        return await load_rules(session)

    views: list[RuleView] = run(work)
    assert len(counted) == 1
    names = {v.priority for v in views}
    assert len(views) == 7 and 50 in names  # disabled and only-off rules are gone
    mixed_view = next(v for v in views if v.priority == 50)
    assert [str(d) for d in mixed_view.destination_ids] == [live]
    alert = type("A", (), {"severity": "HIGH", "source_type": "log_source", "context": {}})()
    result = match_rules(views, alert, at=datetime(2026, 11, 13, 9, 0, tzinfo=UTC))
    assert result.stop_default is False and [str(d) for d in result.destination_ids] == [live]

    # disabling the live destination leaves nothing to deliver: no rules at all
    async def disable(session):  # type: ignore[no-untyped-def]
        await session.execute(
            update(NotificationDestination)
            .where(NotificationDestination.id == uuid.UUID(live))
            .values(enabled=False)
        )
        await session.commit()

    run(disable)
    counted.clear()
    assert run(work) == []


def test_loader_without_rules_is_one_query(db_schema) -> None:
    counted: list[str] = []

    async def work(session):  # type: ignore[no-untyped-def]
        @event.listens_for(session.bind.sync_engine, "before_cursor_execute")
        def _count(conn, cursor, statement, *a):  # type: ignore[no-untyped-def]
            counted.append(statement)

        return await load_rules(session)

    assert run(work) == []
    assert len(counted) == 1
