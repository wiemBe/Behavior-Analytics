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
