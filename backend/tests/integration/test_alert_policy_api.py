"""The SOC-17c alert-policy API: permissions, validation, audit and digest status.

The list is a read of the alerts surface (`read:alerts`); every write needs
`admin:outage`, the same capability as the outage policy. A body names the full
row identity, and the detector is validated against the family's own enum.
DB-gated.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.alert_policy import AlertDigestRun
from app.models.email import EmailRecipient
from app.models.enums import AlertDigestState
from app.models.property_watch import PropertyWatch
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
        reason="set TEST_DATABASE_URL to run alert policy integration tests",
    ),
]

BASE = "/api/v1/alert-policy"


def _body(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "scope_kind": "CLASS",
        "scope_value": "FIREWALL",
        "family": "log_source",
        "detector_type": None,
        "mode": "RECORD_ONLY",
    }
    values.update(overrides)
    return values


async def _no_session():  # type: ignore[no-untyped-def]
    yield None


def test_alert_policy_read_needs_read_alerts_and_writes_need_admin_outage(
    db_schema,
) -> None:
    assert Client(ADMIN).get(BASE).status_code == 200
    # A viewer may read the list (it explains what pages) but may not change it.
    assert Client(VIEWER).get(BASE).status_code == 200
    assert Client(VIEWER).request("PUT", BASE, json=_body()).status_code == 403
    assert Client(VIEWER).delete(f"{BASE}/{MISSING_ID}").status_code == 403
    assert Client(NOTHING).get(BASE).status_code == 403
    assert Client(NOTHING).get(f"{BASE}/digest").status_code == 403
    assert audit_rows("alert_policy.upsert") == []


def test_without_a_token_is_401() -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = _no_session
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/digest").status_code == 401


def test_upsert_and_delete_audit_before_and_after(db_schema) -> None:
    admin = Client(ADMIN)
    created = admin.request("PUT", BASE, json=_body())
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["scope_kind"] == "CLASS" and body["scope_value"] == "FIREWALL"
    assert body["family"] == "log_source" and body["detector_type"] is None
    assert body["mode"] == "RECORD_ONLY" and body["updated_by"] == "admin"

    [upsert] = audit_rows("alert_policy.upsert")
    assert upsert.actor == "admin" and upsert.object_type == "alert_policy"
    assert upsert.detail["before"] is None
    assert upsert.detail["after"]["mode"] == "RECORD_ONLY"

    changed = admin.request("PUT", BASE, json=_body(mode="ALERT"))
    assert changed.status_code == 200
    second = audit_rows("alert_policy.upsert")[1]
    assert second.detail["before"]["mode"] == "RECORD_ONLY"
    assert second.detail["after"]["mode"] == "ALERT"

    # A detector-specific row is a different identity, not an update.
    other = admin.request(
        "PUT", BASE, json=_body(detector_type="NO_EVENTS", mode="DIGEST")
    )
    assert other.status_code == 200
    rows = admin.get(BASE).json()
    assert len(rows) == 2 and {r["detector_type"] for r in rows} == {None, "NO_EVENTS"}

    deleted = admin.delete(f"{BASE}/{other.json()['id']}")
    assert deleted.status_code == 204
    [removal] = audit_rows("alert_policy.delete")
    assert removal.detail["before"]["detector_type"] == "NO_EVENTS"
    assert removal.detail["after"] is None
    assert admin.delete(f"{BASE}/{other.json()['id']}").status_code == 404


def test_validation_refuses_an_unknown_detector_class_or_source(db_schema) -> None:
    admin = Client(ADMIN)

    def put(body: dict) -> int:
        return admin.request("PUT", BASE, json=body).status_code

    # A detector from another family, and an invented one.
    assert put(_body(detector_type="WAF_REQUEST_RATE_SPIKE")) == 422
    assert put(_body(detector_type="MADE_UP")) == 422
    assert put(_body(detector_type="waf_request_rate_spike")) == 422
    # Family and scope validation.
    assert put(_body(family="waf", detector_type="NO_EVENTS")) == 422
    assert put(_body(scope_value="firewall")) == 422
    assert put(_body(scope_value="NOPE")) == 422
    assert put(_body(family="property_watch")) == 422  # no class scope
    assert put(_body(family="property_watch", scope_kind="SOURCE", scope_value=MISSING_ID)) == 422
    assert put(_body(family="made_up")) == 422
    assert put(_body(scope_kind="SOURCE", scope_value="not-a-uuid")) == 422
    assert put(_body(mode="NOPE")) == 422
    assert put(_body(instance_id=MISSING_ID)) == 422
    assert put({**_body(), "surprise": 1}) == 422
    assert audit_rows("alert_policy.upsert") == []

    # A valid detector of that family is accepted.
    assert put(_body(detector_type="VOLUME_SPIKE")) == 200


def test_a_source_row_is_named_and_an_instance_row_is_separate(db_schema) -> None:
    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1, name="fw-edge-01")
        session.add(classification(source, "FIREWALL"))
        watch = PropertyWatch(
            instance_id=instance.id,
            name="auth-watch",
            scope_kind="LOG_SOURCE",
            log_source_id=source.id,
            properties=[{"kind": "NORMALIZED", "name": "username"}],
            detectors=["PW_VALUE_SPIKE"],
            enabled=True,
        )
        session.add(watch)
        await session.flush()
        return str(instance.id), instance.name, str(source.id), str(watch.id)

    instance_id, instance_name, source_id, watch_id = run(setup)
    admin = Client(ADMIN)
    assert admin.request(
        "PUT",
        BASE,
        json=_body(
            scope_kind="SOURCE", scope_value=source_id, detector_type="VOLUME_SPIKE",
            instance_id=instance_id,
        ),
    ).status_code == 200
    assert admin.request(
        "PUT",
        BASE,
        json=_body(
            scope_kind="SOURCE", scope_value=watch_id, family="property_watch",
            detector_type="PW_VALUE_SPIKE",
        ),
    ).status_code == 200

    rows = {row["scope_value"]: row for row in admin.get(BASE).json()}
    assert rows[source_id]["source_name"] == "fw-edge-01"
    assert rows[source_id]["source_class"] == "FIREWALL"
    assert rows[source_id]["instance_id"] == instance_id
    assert rows[source_id]["instance_name"] == instance_name
    assert rows[watch_id]["source_name"] == "auth-watch"
    assert rows[watch_id]["source_class"] is None
    assert rows[watch_id]["instance_id"] is None


def test_a_gone_source_leaves_its_name_and_class_null(db_schema) -> None:
    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1, name="retired")
        await session.flush()
        return str(source.id)

    source_id = run(setup)
    admin = Client(ADMIN)
    assert admin.request(
        "PUT",
        BASE,
        json=_body(scope_kind="SOURCE", scope_value=source_id),
    ).status_code == 200

    async def drop(session):  # type: ignore[no-untyped-def]
        from app.models.log_source import LogSource

        await session.delete(await session.get(LogSource, uuid.UUID(source_id)))

    run(drop)
    [row] = admin.get(BASE).json()
    assert row["source_name"] is None and row["source_class"] is None


def test_get_statement_count_does_not_grow_with_the_rows(db_schema) -> None:
    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        return str(instance.id)

    instance_id = run(setup)
    admin = Client(ADMIN)
    counts = []
    for total in (1, 6):
        for index in range(total):
            klass = ["FIREWALL", "IPS", "WAF", "PROXY", "DNS", "OTHER"][index]
            admin.request(
                "PUT",
                BASE,
                json=_body(scope_value=klass, mode="RECORD_ONLY", instance_id=instance_id),
            )
        rows = admin.get(BASE).json()
        assert len(rows) == total
        counts.append(admin.statements)
    assert counts[0] == counts[1], counts


def test_digest_status_reports_due_hour_last_run_and_recipients(db_schema) -> None:
    settings = get_settings()

    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add(
            EmailRecipient(address="soc@example.org", min_severity="LOW", enabled=True)
        )
        session.add(
            AlertDigestRun(
                instance_id=instance.id,
                digest_date=date(2026, 11, 5),
                state=AlertDigestState.SUCCESS.value,
                item_count=4,
                recipient_count=2,
                error_code=None,
                sent_at=datetime(2026, 11, 5, 7, 5, tzinfo=UTC),
            )
        )
        await session.flush()
        return instance.name

    instance_name = run(setup)
    body = Client(ADMIN).get(f"{BASE}/digest").json()
    assert body["digest_hour_utc"] == settings.digest_hour_utc
    assert body["email_configured"] == settings.smtp_configured
    assert body["recipient_count"] >= 1
    assert body["last_run"]["state"] == "SUCCESS"
    assert body["last_run"]["item_count"] == 4
    assert body["last_run"]["recipient_count"] == 2
    assert body["last_run"]["instance_name"] == instance_name
    [instance] = body["instances"]
    assert instance["instance_name"] == instance_name
    assert instance["last_run"]["state"] == "SUCCESS"


def test_digest_status_with_no_run_has_no_invented_zero(db_schema) -> None:
    async def setup(session):  # type: ignore[no-untyped-def]
        return (await make_instance(session)).name

    run(setup)
    body = Client(ADMIN).get(f"{BASE}/digest").json()
    assert body["last_run"] is None
    [instance] = body["instances"]
    assert instance["last_run"] is None
