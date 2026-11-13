"""The alert id a notifier sends is the real one, not the masked payload copy (MAIL-1).

``redact()`` masks a UUID as a token-shaped value, so ``alert_notification.payload`` stores
``alert_id`` as ``***redacted***``. The dispatcher builds the delivered message from the
``alert_notification.alert_id`` column instead. Pre-existing bug, fixed with MAIL-1; the
generic webhook is the channel that carries the id (Teams, Slack and syslog do not).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.channels import (
    GenericWebhookNotifier,
    SlackNotifier,
    SyslogNotifier,
    TeamsNotifier,
)
from app.alerts.routing import RoutingPolicy
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, AlertTransition, NotificationChannel, Severity
from app.security.redaction import redact

pytestmark = pytest.mark.asyncio

ALERT_ID = uuid.UUID("0c2792c2-d687-41d7-9c9d-8d5688c2696d")
SOURCE_ID = uuid.UUID("5a6b7c8d-90e1-4f2a-8b3c-4d5e6f708192")


def _notification() -> AlertNotification:
    now = datetime.now(UTC)
    alert = Alert(
        id=ALERT_ID, dedup_key="anomaly:abc", fingerprint="anomaly:abc", title="Spike on fw-01",
        severity=Severity.HIGH, status=AlertStatus.OPEN, source_type="log_source",
        source_id=uuid.uuid4(), opened_at=now, first_seen_at=now, occurrence_count=1, context={},
    )
    dispatcher = NotificationDispatcher(None, {}, RoutingPolicy())  # type: ignore[arg-type]
    payload = redact(dispatcher._render(alert, AlertTransition.OPENED))
    return AlertNotification(
        alert_id=ALERT_ID, channel=NotificationChannel.WEBHOOK, target="https://hook.test/x",
        transition=AlertTransition.OPENED, payload=payload,
    )


def _message():  # type: ignore[no-untyped-def]
    notification = _notification()
    # The stored copy really is masked; that is the premise of the fix.
    assert notification.payload["alert_id"] == "***redacted***"
    dispatcher = NotificationDispatcher(None, {}, RoutingPolicy())  # type: ignore[arg-type]
    return dispatcher._message_from_payload(notification)


@respx.mock
async def test_the_generic_webhook_carries_the_real_alert_id() -> None:
    route = respx.post("https://hook.test/x").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await GenericWebhookNotifier(client).send("https://hook.test/x", _message())
    body = json.loads(route.calls[0].request.content)
    assert body["alert_id"] == str(ALERT_ID)
    assert "redacted" not in json.dumps(body)


@respx.mock
async def test_teams_and_slack_payloads_never_carry_the_masked_id() -> None:
    teams = respx.post("https://teams.test/hook").mock(return_value=httpx.Response(200))
    slack = respx.post("https://slack.test/hook").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await TeamsNotifier(client).send("https://teams.test/hook", _message())
        await SlackNotifier(client).send("https://slack.test/hook", _message())
    for route in (teams, slack):
        text = route.calls[0].request.content.decode()
        assert "redacted" not in text
        assert str(ALERT_ID) not in text  # these two cards have never carried the id


async def test_the_message_and_syslog_line_use_the_real_id(monkeypatch) -> None:
    message = _message()
    assert message.alert_id == str(ALERT_ID)
    sent: list[bytes] = []

    class FakeSock:
        def __init__(self, *a, **k) -> None: ...
        def sendto(self, data: bytes, addr: tuple) -> None:
            sent.append(data)
        def close(self) -> None: ...

    monkeypatch.setattr("socket.socket", lambda *a, **k: FakeSock())
    await SyslogNotifier().send("logs.internal:514", message)
    assert b"redacted" not in sent[0]


async def test_the_render_is_additive_and_freezes_the_v1_inputs() -> None:
    """SOC-12a. Every older key keeps its name and value, so the channels that read
    this payload today send the same bytes; the new keys are the versioned webhook's
    frozen inputs, stored in a form ``redact`` leaves alone."""
    opened = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
    acknowledged = datetime(2026, 11, 13, 9, 14, tzinfo=UTC)
    resolved = datetime(2026, 11, 13, 10, 2, tzinfo=UTC)
    alert = Alert(
        id=ALERT_ID, dedup_key="anomaly:abc", fingerprint="anomaly:abc",
        title="Volume drop on proxy-04", description="Events fell below the band.",
        severity=Severity.HIGH, status=AlertStatus.RESOLVED, source_type="waf_endpoint",
        source_id=SOURCE_ID, opened_at=opened, first_seen_at=opened, occurrence_count=4,
        context={"instance": "qradar-lab"},
        evidence_snapshot={"reason": "below band", "observed_value": 3.0},
        acknowledged_at=acknowledged, acknowledged_by="security-operations-lead",
        resolved_at=resolved, resolved_by="system:waf-anomaly-engine",
        resolution_reason="scanned by the analyst",
        resolution_reason_i18n={"code": "alert.resolution.false_positive", "params": {}},
    )
    dispatcher = NotificationDispatcher(None, {}, RoutingPolicy())  # type: ignore[arg-type]
    payload = dispatcher._render(alert, AlertTransition.RESOLVED)

    assert payload == {
        # Pre-existing keys, unchanged.
        "title": "Volume drop on proxy-04",
        "body": "Events fell below the band.",
        "severity": "HIGH",
        "transition": "RESOLVED",
        "alert_id": str(ALERT_ID),
        "fingerprint": "anomaly:abc",
        "alert_type": "waf_endpoint",
        "opened_at": "2026-11-13T09:00:00+00:00",
        "instance": "qradar-lab",
        "fields": {
            "occurrences": 4,
            "first_seen": "2026-11-13T09:00:00+00:00",
            "reason": "below band",
            "observed_value": 3.0,
        },
        # SOC-12a additions.
        "family": "waf",
        "source_id": f"urn:uuid:{SOURCE_ID}",
        "status": "RESOLVED",
        "acknowledged_at": "2026-11-13T09:14:00+00:00",
        "acknowledged_by": "principal:security-operations-lead",
        "resolved_at": "2026-11-13T10:02:00+00:00",
        "resolved_by": "principal:system:waf-anomaly-engine",
        "resolution_reason_code": "alert.resolution.false_positive",
    }
    # The code only: the resolution's free text is not part of the payload.
    assert "resolution_reason" not in payload

    stored = redact(payload)
    # `alert_id` was always masked; the new inputs survive to delivery time.
    assert stored["alert_id"] == "***redacted***"
    assert stored["source_id"] == f"urn:uuid:{SOURCE_ID}"
    assert stored["family"] == "waf"
    assert stored["status"] == "RESOLVED"
    assert stored["acknowledged_at"] == "2026-11-13T09:14:00+00:00"
    assert stored["acknowledged_by"] == "principal:security-operations-lead"
    assert stored["resolved_at"] == "2026-11-13T10:02:00+00:00"
    assert stored["resolved_by"] == "principal:system:waf-anomaly-engine"
    assert stored["resolution_reason_code"] == "alert.resolution.false_positive"
