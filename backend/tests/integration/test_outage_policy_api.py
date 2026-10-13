"""The outage policy API: `admin:outage`, upsert, delete, audit and the GET count.

Every route needs `admin:outage`, applied at the router mount. `read:*` does not
satisfy it, because the policy decides which silences page a human. DB-gated.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import (
    ADMIN,
    MISSING_ID,
    NOTHING,
    VIEWER,
    Client,
    audit_rows,
    classification,
    run,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run outage policy integration tests",
    ),
]

POLICY = "/api/v1/outage-policy"


async def _no_session():  # type: ignore[no-untyped-def]
    yield None


def test_outage_policy_requires_admin_outage_and_not_merely_read(db_schema) -> None:
    assert Client(ADMIN).get(POLICY).status_code == 200
    # `read:*` is not enough: this is operational control, not a view.
    assert Client(VIEWER).get(POLICY).status_code == 403
    assert Client(NOTHING).get(POLICY).status_code == 403
    assert Client(VIEWER).request(
        "PUT", f"{POLICY}/classes/FIREWALL", json={"mode": "ALERT"}
    ).status_code == 403
    assert Client(VIEWER).delete(f"{POLICY}/classes/FIREWALL").status_code == 403
    assert Client(VIEWER).request(
        "PUT", f"{POLICY}/sources/{MISSING_ID}", json={"mode": "ALERT"}
    ).status_code == 403
    assert audit_rows("outage_policy.upsert") == []


def test_outage_policy_without_a_token_is_401() -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = _no_session
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(POLICY).status_code == 401


def test_upsert_and_delete_audit_before_and_after(db_schema) -> None:
    admin = Client(ADMIN)
    created = admin.request(
        "PUT",
        f"{POLICY}/classes/FIREWALL",
        json={"mode": "RECORD_ONLY", "silence_grace_buckets": 6},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["scope_kind"] == "CLASS" and body["scope_value"] == "FIREWALL"
    assert body["mode"] == "RECORD_ONLY" and body["silence_grace_buckets"] == 6
    assert body["instance_id"] is None and body["updated_by"] == "admin"

    upserts = audit_rows("outage_policy.upsert")
    assert len(upserts) == 1
    assert upserts[0].actor == "admin"
    assert upserts[0].object_type == "outage_alert_policy"
    assert upserts[0].object_id == "CLASS:FIREWALL"
    assert upserts[0].detail["before"] is None
    assert upserts[0].detail["after"]["mode"] == "RECORD_ONLY"
    assert upserts[0].detail["after"]["silence_grace_buckets"] == 6

    changed = admin.request(
        "PUT", f"{POLICY}/classes/FIREWALL", json={"mode": "ALERT", "silence_grace_buckets": None}
    )
    assert changed.status_code == 200
    second = audit_rows("outage_policy.upsert")[1]
    assert second.detail["before"]["mode"] == "RECORD_ONLY"
    assert second.detail["after"]["mode"] == "ALERT"
    assert second.detail["after"]["silence_grace_buckets"] is None

    deleted = admin.delete(f"{POLICY}/classes/FIREWALL")
    assert deleted.status_code == 204
    removal = audit_rows("outage_policy.delete")
    assert len(removal) == 1 and removal[0].actor == "admin"
    assert removal[0].detail["before"]["mode"] == "ALERT"
    assert removal[0].detail["after"] is None
    assert admin.delete(f"{POLICY}/classes/FIREWALL").status_code == 404


def test_source_policy_upsert_requires_an_existing_source(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        return str((await make_log_source(s, inst, qradar_id=1, name="fw-a")).id)

    source_id = run(setup)
    admin = Client(ADMIN)
    created = admin.request(
        "PUT", f"{POLICY}/sources/{source_id}", json={"mode": "RECORD_ONLY"}
    )
    assert created.status_code == 200, created.text
    assert created.json()["scope_kind"] == "SOURCE"
    assert created.json()["scope_value"] == source_id

    rows = audit_rows("outage_policy.upsert")
    assert rows[0].detail["after"]["scope_kind"] == "SOURCE"
    assert admin.delete(f"{POLICY}/sources/{source_id}").status_code == 204
    assert audit_rows("outage_policy.delete")[0].object_id == f"SOURCE:{source_id}"
    assert admin.delete(f"{POLICY}/sources/{source_id}").status_code == 404


def test_upsert_validation(db_schema) -> None:
    admin = Client(ADMIN)

    def put(path: str, body: dict) -> int:
        return admin.request("PUT", path, json=body).status_code

    assert put(f"{POLICY}/classes/NOPE", {"mode": "ALERT"}) == 422
    assert put(f"{POLICY}/classes/firewall", {"mode": "ALERT"}) == 422
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "NOPE"}) == 422
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "ALERT", "silence_grace_buckets": 49}) == 422
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "ALERT", "silence_grace_buckets": -1}) == 422
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "ALERT", "surprise": 1}) == 422
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "ALERT", "instance_id": MISSING_ID}) == 422
    assert put(f"{POLICY}/sources/{MISSING_ID}", {"mode": "ALERT"}) == 404
    assert put(f"{POLICY}/sources/not-a-uuid", {"mode": "ALERT"}) == 422
    # Nothing above reached the table.
    assert audit_rows("outage_policy.upsert") == []

    # The bounds are inclusive.
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "ALERT", "silence_grace_buckets": 0}) == 200
    assert put(f"{POLICY}/classes/FIREWALL", {"mode": "ALERT", "silence_grace_buckets": 48}) == 200
    assert len(audit_rows("outage_policy.upsert")) == 2


def test_get_reports_governed_monitored_sources_and_skips_overrides(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="fw-a")
        b = await make_log_source(s, inst, qradar_id=2, name="fw-b")
        c = await make_log_source(s, inst, qradar_id=3, name="fw-c")
        off = await make_log_source(s, inst, qradar_id=4, name="fw-off", enabled=False)
        s.add_all([classification(x, "FIREWALL") for x in (a, b, c, off)])
        await s.flush()
        return str(b.id)

    overridden = run(setup)
    admin = Client(ADMIN)
    admin.request("PUT", f"{POLICY}/classes/FIREWALL", json={"mode": "RECORD_ONLY"})
    admin.request("PUT", f"{POLICY}/sources/{overridden}", json={"mode": "ALERT"})

    rows = {r["scope_kind"]: r for r in admin.get(POLICY).json()}
    # Three monitored FIREWALL sources, one governed by its own SOURCE row.
    assert rows["CLASS"]["governed_monitored_sources"] == 2
    assert rows["SOURCE"]["governed_monitored_sources"] is None
    assert rows["CLASS"]["mode"] == "RECORD_ONLY"
    assert rows["SOURCE"]["scope_value"] == overridden


def test_instance_rows_are_separate_and_delete_is_scoped_to_them(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        return str((await make_instance(s)).id)

    instance_id = run(setup)
    admin = Client(ADMIN)
    assert admin.request(
        "PUT", f"{POLICY}/classes/FIREWALL", json={"mode": "RECORD_ONLY"}
    ).status_code == 200
    assert admin.request(
        "PUT",
        f"{POLICY}/classes/FIREWALL",
        json={"mode": "ALERT", "instance_id": instance_id},
    ).status_code == 200

    rows = admin.get(POLICY).json()
    assert len(rows) == 2
    assert {r["instance_id"] for r in rows} == {None, instance_id}

    # Deleting the instance row leaves the global one untouched.
    assert admin.delete(f"{POLICY}/classes/FIREWALL?instance_id={instance_id}").status_code == 204
    assert [r["instance_id"] for r in admin.get(POLICY).json()] == [None]
    assert admin.delete(f"{POLICY}/classes/FIREWALL").status_code == 204
    assert admin.get(POLICY).json() == []
