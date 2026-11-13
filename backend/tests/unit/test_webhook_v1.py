"""The versioned generic-webhook body (SOC-12 slice 1, design §4).

Each event's body is pinned as exact bytes, so the key order and the compact
separators -- what the HMAC signs and what makes a retry recognisable -- cannot drift
silently. ``_render`` keeps the column's ``+00:00`` offset; the builder's normalization
to a literal ``Z`` is part of what these tests check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.base import NotificationMessage
from app.alerts.notifiers.channels import GenericWebhookNotifier
from app.alerts.notifiers.webhook_v1 import (
    SIGNATURE_HEADER,
    build_webhook_v1,
    build_webhook_v1_test,
    sign_v1,
)
from app.alerts.routing import RoutingPolicy
from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertTransition, Severity
from app.security.redaction import redact

ALERT_ID = uuid.UUID("0c2792c2-d687-41d7-9c9d-8d5688c2696d")
SOURCE_ID = uuid.UUID("5a6b7c8d-90e1-4f2a-8b3c-4d5e6f708192")
DESTINATION = "dest-1"
ORIGIN = "https://soc.example"
OPENED_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
ACKNOWLEDGED_AT = datetime(2026, 11, 13, 9, 14, tzinfo=UTC)
RESOLVED_AT = datetime(2026, 11, 13, 10, 2, 30, 123456, tzinfo=UTC)
TITLE = "Volume drop on proxy-04"
SUMMARY = "Events fell below the expected band."


def _alert(**overrides: object) -> Alert:
    values: dict[str, object] = {
        "id": ALERT_ID,
        "dedup_key": "anomaly:1f0c2d3e",
        "fingerprint": "anomaly:1f0c2d3e",
        "title": TITLE,
        "description": SUMMARY,
        "severity": Severity.HIGH,
        "status": AlertStatus.OPEN,
        "source_type": "log_source",
        "source_id": SOURCE_ID,
        "context": {"instance": "qradar-lab"},
        "opened_at": OPENED_AT,
        "first_seen_at": OPENED_AT,
        "occurrence_count": 3,
        "evidence_snapshot": {
            "observed_value": 3.0,
            "expected_value": 50.0,
            "deviation_score": -4.2,
            "reason": "below the expected band",
            "paths": "/admin/login",
        },
        "source_anomaly_ids": ["anomaly-1"],
        "acknowledged_at": ACKNOWLEDGED_AT,
        "acknowledged_by": "alice@example.com",
        "resolved_at": RESOLVED_AT,
        "resolved_by": "system:ips-anomaly-engine",
        "resolution_reason": "false positive, checked by the analyst",
        "resolution_reason_i18n": {
            "code": "alert.resolution.false_positive",
            "params": {},
        },
    }
    values.update(overrides)
    return Alert(**values)  # type: ignore[arg-type]


def _stored_payload(alert: Alert, transition: AlertTransition) -> dict:
    """The payload as ``enqueue`` freezes it: rendered, then redacted."""
    dispatcher = NotificationDispatcher(None, {}, RoutingPolicy())  # type: ignore[arg-type]
    return redact(dispatcher._render(alert, transition))


def _opened_payload(**overrides: object) -> dict:
    return _stored_payload(_alert(**overrides), AlertTransition.OPENED)


def _acknowledged_payload(**overrides: object) -> dict:
    return _stored_payload(
        _alert(status=AlertStatus.ACKNOWLEDGED, **overrides),
        AlertTransition.ACKNOWLEDGED,
    )


def _resolved_payload(**overrides: object) -> dict:
    return _stored_payload(
        _alert(status=AlertStatus.RESOLVED, **overrides), AlertTransition.RESOLVED
    )


def _expected(fields: dict[str, object]) -> bytes:
    """The bytes the contract means for a body written in design key order."""
    return json.dumps(fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def test_the_opened_body_is_the_contract_bytes() -> None:
    body, headers = build_webhook_v1(
        _opened_payload(),
        alert_id=ALERT_ID,
        transition=AlertTransition.OPENED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    expected = {
        "schema_version": 1,
        "event": "opened",
        "idempotency_key": f"{ALERT_ID}:OPENED:{DESTINATION}",
        "alert_id": str(ALERT_ID),
        "fingerprint": "anomaly:1f0c2d3e",
        "family": "log_source",
        "source_type": "log_source",
        "source_id": str(SOURCE_ID),
        "severity": "HIGH",
        "subject": TITLE,
        "summary": SUMMARY,
        "status": "OPEN",
        "occurrence_count": 3,
        "opened_at": "2026-11-13T09:00:00Z",
        "occurred_at": "2026-11-13T09:00:00Z",
        "url": f"{ORIGIN}/alerts/{ALERT_ID}",
        "honesty": None,
    }
    assert body == _expected(expected)
    assert headers == {
        "Content-Type": "application/json",
        "Idempotency-Key": f"{ALERT_ID}:OPENED:{DESTINATION}",
    }


def test_the_acknowledged_body_is_the_contract_bytes() -> None:
    payload = _acknowledged_payload()
    body, _ = build_webhook_v1(
        payload,
        alert_id=ALERT_ID,
        transition="ACKNOWLEDGED",
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    expected = {
        "schema_version": 1,
        "event": "acknowledged",
        "idempotency_key": f"{ALERT_ID}:ACKNOWLEDGED:{DESTINATION}",
        "alert_id": str(ALERT_ID),
        "family": "log_source",
        "source_type": "log_source",
        "severity": "HIGH",
        "subject": TITLE,
        "status": "ACKNOWLEDGED",
        "opened_at": "2026-11-13T09:00:00Z",
        "acknowledged_at": "2026-11-13T09:14:00Z",
        "acknowledged_by": "alice@example.com",
        "occurred_at": "2026-11-13T09:14:00Z",
        "url": f"{ORIGIN}/alerts/{ALERT_ID}",
        "honesty": None,
    }
    assert body == _expected(expected)


def test_the_resolved_body_is_the_contract_bytes_with_the_code_only() -> None:
    body, headers = build_webhook_v1(
        _resolved_payload(),
        alert_id=ALERT_ID,
        transition=AlertTransition.RESOLVED,
        destination_id=DESTINATION,
        public_origin=f"{ORIGIN}/",
    )
    expected = {
        "schema_version": 1,
        "event": "resolved",
        "idempotency_key": f"{ALERT_ID}:RESOLVED:{DESTINATION}",
        "alert_id": str(ALERT_ID),
        "family": "log_source",
        "source_type": "log_source",
        "severity": "HIGH",
        "subject": TITLE,
        "status": "RESOLVED",
        "resolved_at": "2026-11-13T10:02:30.123456Z",
        "resolved_by": "system:ips-anomaly-engine",
        "resolution_reason_code": "alert.resolution.false_positive",
        "occurred_at": "2026-11-13T10:02:30.123456Z",
        "url": f"{ORIGIN}/alerts/{ALERT_ID}",
        "honesty": None,
    }
    assert body == _expected(expected)
    # The free-text reason never enters the body; only the code does.
    assert b"checked by the analyst" not in body
    assert headers["Idempotency-Key"] == f"{ALERT_ID}:RESOLVED:{DESTINATION}"


def test_the_test_body_is_the_contract_bytes() -> None:
    body, headers = build_webhook_v1_test(DESTINATION, OPENED_AT)
    expected = {
        "schema_version": 1,
        "event": "test",
        "idempotency_key": f"test:{DESTINATION}:2026-11-13T09:00:00Z",
        "alert_id": None,
        "family": None,
        "severity": "LOW",
        "subject": "Notification test",
        "occurred_at": "2026-11-13T09:00:00Z",
        "url": None,
        "honesty": None,
    }
    assert body == _expected(expected)
    assert headers["Idempotency-Key"] == f"test:{DESTINATION}:2026-11-13T09:00:00Z"


def test_honesty_is_null_and_a_richer_payload_leaks_nothing() -> None:
    payload = _opened_payload()
    payload.update(
        {
            "evidence_snapshot": {"paths": "/admin/login", "source_address": "10.0.0.5"},
            "source_anomaly_ids": ["anomaly-1", "anomaly-2"],
            "target": "https://hook.test/credential",
            "aql": "SELECT * FROM events",
            "secret": "s3cr3t",
            "headers": {"X-Tenant": "tenant-9"},
        }
    )
    body, _ = build_webhook_v1(
        payload,
        alert_id=ALERT_ID,
        transition=AlertTransition.OPENED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    sent = json.loads(body)
    assert sent["honesty"] is None
    text = body.decode()
    for leaked in (
        "/admin/login",
        "10.0.0.5",
        "anomaly-1",
        "https://hook.test/credential",
        "SELECT * FROM events",
        "s3cr3t",
        "tenant-9",
    ):
        assert leaked not in text
    for absent in ("evidence_snapshot", "source_anomaly_ids", "target", "aql", "secret"):
        assert absent not in sent


def test_the_idempotency_key_names_the_transition_and_the_destination() -> None:
    payload = _opened_payload()
    for transition, written in (
        (AlertTransition.OPENED, "OPENED"),
        (AlertTransition.ACKNOWLEDGED, "ACKNOWLEDGED"),
        (AlertTransition.RESOLVED, "RESOLVED"),
    ):
        if transition is AlertTransition.ACKNOWLEDGED:
            payload = _acknowledged_payload()
        elif transition is AlertTransition.RESOLVED:
            payload = _resolved_payload()
        _, headers = build_webhook_v1(
            payload,
            alert_id=ALERT_ID,
            transition=transition,
            destination_id="other-dest",
            public_origin=ORIGIN,
        )
        assert headers["Idempotency-Key"] == f"{ALERT_ID}:{written}:other-dest"
        assert headers["Content-Type"] == "application/json"


def test_the_same_input_yields_the_same_bytes_whatever_the_dict_order() -> None:
    payload = _opened_payload()
    arguments = {
        "alert_id": ALERT_ID,
        "transition": AlertTransition.OPENED,
        "destination_id": DESTINATION,
        "public_origin": ORIGIN,
    }
    first, _ = build_webhook_v1(payload, **arguments)  # type: ignore[arg-type]
    second, _ = build_webhook_v1(payload, **arguments)  # type: ignore[arg-type]

    # Same content, different insertion order: the builder's key order is its own.
    reordered = dict(reversed(list(payload.items())))
    third, _ = build_webhook_v1(reordered, **arguments)  # type: ignore[arg-type]

    assert first == second == third


def test_times_are_one_style_z_and_keep_the_fraction_they_were_given() -> None:
    payload = _opened_payload(opened_at=datetime(2026, 11, 13, 9, 0, 0, 250000, tzinfo=UTC))
    body, _ = build_webhook_v1(
        payload,
        alert_id=ALERT_ID,
        transition=AlertTransition.OPENED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    sent = json.loads(body)
    assert sent["opened_at"] == "2026-11-13T09:00:00.250000Z"
    assert sent["opened_at"].endswith("Z")
    assert "+00:00" not in body.decode()


def test_sign_v1_is_the_hmac_of_the_body_as_computed_here() -> None:
    body, _ = build_webhook_v1_test(DESTINATION, OPENED_AT)
    expected = "v1=" + hmac.new(b"top-secret", body, hashlib.sha256).hexdigest()
    assert sign_v1(body, "top-secret") == expected
    assert SIGNATURE_HEADER == "X-QRadar-Dash-Signature"
    # A different secret or a different body must not verify.
    assert sign_v1(body, "another-secret") != expected
    assert sign_v1(body + b" ", "top-secret") != expected


@pytest.mark.parametrize(
    "field, value",
    [
        ("severity", None),
        ("title", None),
        ("status", None),
        ("alert_type", None),
    ],
)
def test_a_payload_missing_a_required_field_is_refused(field: str, value: object) -> None:
    payload = _opened_payload()
    payload[field] = value
    with pytest.raises(ValueError, match=field):
        build_webhook_v1(
            payload,
            alert_id=ALERT_ID,
            transition=AlertTransition.OPENED,
            destination_id=DESTINATION,
            public_origin=ORIGIN,
        )


def test_a_body_without_its_transition_time_is_refused() -> None:
    payload = _acknowledged_payload()
    payload["acknowledged_at"] = None
    with pytest.raises(ValueError, match="acknowledged_at"):
        build_webhook_v1(
            payload,
            alert_id=ALERT_ID,
            transition=AlertTransition.ACKNOWLEDGED,
            destination_id=DESTINATION,
            public_origin=ORIGIN,
        )


def test_a_naive_time_is_never_read_as_utc() -> None:
    payload = _opened_payload()
    payload["opened_at"] = "2026-11-13T09:00:00"
    with pytest.raises(ValueError, match="timezone"):
        build_webhook_v1(
            payload,
            alert_id=ALERT_ID,
            transition=AlertTransition.OPENED,
            destination_id=DESTINATION,
            public_origin=ORIGIN,
        )


def test_an_unversioned_transition_name_is_refused() -> None:
    with pytest.raises(ValueError):
        build_webhook_v1(
            _opened_payload(),
            alert_id=ALERT_ID,
            transition="REOPENED",
            destination_id=DESTINATION,
            public_origin=ORIGIN,
        )


@pytest.mark.parametrize("stored", [f"urn:uuid:{SOURCE_ID}", str(SOURCE_ID)])
def test_the_source_id_leaves_as_the_bare_uuid(stored: str) -> None:
    payload = _opened_payload()
    payload["source_id"] = stored
    body, _ = build_webhook_v1(
        payload,
        alert_id=ALERT_ID,
        transition=AlertTransition.OPENED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    assert json.loads(body)["source_id"] == str(SOURCE_ID)


def test_an_alert_without_a_source_id_sends_null() -> None:
    body, _ = build_webhook_v1(
        _opened_payload(source_id=None),
        alert_id=ALERT_ID,
        transition=AlertTransition.OPENED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    assert json.loads(body)["source_id"] is None


def test_a_malformed_source_id_is_refused() -> None:
    payload = _opened_payload()
    payload["source_id"] = "not-a-uuid"
    with pytest.raises(ValueError, match="source_id"):
        build_webhook_v1(
            payload,
            alert_id=ALERT_ID,
            transition=AlertTransition.OPENED,
            destination_id=DESTINATION,
            public_origin=ORIGIN,
        )


def test_a_producer_outside_the_family_map_sends_a_null_family() -> None:
    body, _ = build_webhook_v1(
        _opened_payload(source_type="unknown_producer"),
        alert_id=ALERT_ID,
        transition=AlertTransition.OPENED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    assert json.loads(body)["family"] is None


def test_a_token_shaped_principal_survives_and_leaves_unprefixed() -> None:
    """``redact`` masks a 24+ token-shaped value, so ``_render`` freezes a principal
    with a scheme; the body carries the principal itself."""
    payload = _acknowledged_payload(acknowledged_by="security-operations-lead")
    assert payload["acknowledged_by"] == "principal:security-operations-lead"
    body, _ = build_webhook_v1(
        payload,
        alert_id=ALERT_ID,
        transition=AlertTransition.ACKNOWLEDGED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    assert json.loads(body)["acknowledged_by"] == "security-operations-lead"
    assert "principal:" not in body.decode()


@pytest.mark.parametrize("stored", ["principal:", 17])
def test_a_malformed_principal_is_refused(stored: object) -> None:
    payload = _acknowledged_payload()
    payload["acknowledged_by"] = stored
    with pytest.raises(ValueError, match="acknowledged_by"):
        build_webhook_v1(
            payload,
            alert_id=ALERT_ID,
            transition=AlertTransition.ACKNOWLEDGED,
            destination_id=DESTINATION,
            public_origin=ORIGIN,
        )


def test_a_resolution_without_a_principal_or_a_code_sends_nulls() -> None:
    body, _ = build_webhook_v1(
        _resolved_payload(resolved_by=None, resolution_reason_i18n=None),
        alert_id=ALERT_ID,
        transition=AlertTransition.RESOLVED,
        destination_id=DESTINATION,
        public_origin=ORIGIN,
    )
    sent = json.loads(body)
    assert sent["resolved_by"] is None
    assert sent["resolution_reason_code"] is None


@respx.mock
async def test_the_env_webhook_post_body_is_unchanged() -> None:
    """The unversioned channel keeps its exact body: nothing sends v1 yet."""
    route = respx.post("https://hook.test/x").mock(return_value=httpx.Response(200))
    message = NotificationMessage(
        title=TITLE,
        body=SUMMARY,
        severity=Severity.HIGH,
        transition=AlertTransition.OPENED,
        alert_id=str(ALERT_ID),
        fingerprint="anomaly:1f0c2d3e",
        fields={"occurrences": 3, "observed_value": 3.0, "expected_value": 50.0},
    )
    async with httpx.AsyncClient() as client:
        await GenericWebhookNotifier(client).send("https://hook.test/x", message)
    assert json.loads(route.calls[0].request.content) == {
        "title": TITLE,
        "body": SUMMARY,
        "severity": "HIGH",
        "transition": "OPENED",
        "alert_id": str(ALERT_ID),
        "fingerprint": "anomaly:1f0c2d3e",
        "fields": {"occurrences": 3, "observed_value": 3.0, "expected_value": 50.0},
    }
