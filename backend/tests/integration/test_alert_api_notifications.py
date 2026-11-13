"""Alert notification enqueueing and API history.

Acknowledging or resolving an alert in the UI is a state change the rest of the
team needs to see; before this was wired, only the anomaly engine could produce
a notification and a manual resolve went out silently.

Enqueue-only is the property under test: the request records intent and returns,
it never blocks on an outbound webhook. Delivery is the dispatch_notifications
worker's job and is covered in test_notification_dispatch.py. Notification
history exposes display labels, never HTTP channel credentials. DB-gated.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.channels import GenericWebhookNotifier
from app.alerts.routing import RoutingPolicy
from app.alerts.service import AlertInput, AlertService
from app.core.database import get_session
from app.main import create_app
from app.models.alert import AlertNotification
from app.models.enums import (
    AlertTransition,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _client(session, principal: Principal) -> httpx.AsyncClient:
    """An in-process async client sharing the test's session.

    Deliberately not the sync TestClient: that drives the app on its own event
    loop, while `db_session` holds an asyncpg connection bound to the test's
    loop, so the shared session would fail with "attached to a different loop".
    ASGITransport runs the app on this loop instead.
    """

    async def _session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _open_alert(session):
    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=f"anomaly:{uuid.uuid4().hex[:8]}", title="volume drop",
            severity=Severity.HIGH, source_type="log_source", source_id=uuid.uuid4(),
            evidence={"observed_value": 3.0},
        )
    )
    await session.commit()
    return result.alert


async def _notifications(session, alert_id) -> list[AlertNotification]:
    rows = await session.scalars(
        select(AlertNotification).where(AlertNotification.alert_id == alert_id)
    )
    return list(rows.all())


async def _add_notification(
    session,
    alert_id,
    *,
    channel: NotificationChannel,
    target: str,
    error_message: str | None = None,
) -> AlertNotification:
    notification = AlertNotification(
        alert_id=alert_id,
        channel=channel,
        target=target,
        transition=AlertTransition.OPENED,
        status=NotificationStatus.PENDING,
        max_attempts=3,
        error_message=error_message,
        payload={
            "title": "volume drop",
            "body": "volume drop",
            "severity": Severity.HIGH.value,
            "transition": AlertTransition.OPENED.value,
            "alert_id": str(alert_id),
            "fingerprint": "anomaly:test",
            "fields": {},
        },
    )
    session.add(notification)
    await session.commit()
    return notification


async def _notification_history(client: httpx.AsyncClient, alert_id) -> list[dict]:
    response = await client.get(f"/api/v1/alerts/{alert_id}/notifications")
    assert response.status_code == 200, response.text
    return response.json()


async def test_acknowledge_enqueues_a_notification(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        resp = await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
    assert resp.status_code == 200, resp.text

    rows = await _notifications(db_session, alert.id)
    assert [r.transition for r in rows] == [AlertTransition.ACKNOWLEDGED]
    # Intent only — the request itself sends nothing.
    assert rows[0].status == NotificationStatus.PENDING
    assert rows[0].sent_at is None


async def test_resolve_enqueues_a_recovery_notification(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        resp = await client.post(
            f"/api/v1/alerts/{alert.id}/resolve", json={"reason": "log source restored"}
        )
    assert resp.status_code == 200, resp.text

    rows = await _notifications(db_session, alert.id)
    assert [r.transition for r in rows] == [AlertTransition.RESOLVED]


async def test_repeated_acknowledge_does_not_enqueue_twice(db_session, monkeypatch) -> None:
    """The second acknowledge is a no-op transition, and a no-op must not page."""
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")
        await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    assert len(await _notifications(db_session, alert.id)) == 1


async def test_no_configured_channel_enqueues_nothing(db_session, monkeypatch) -> None:
    # An unconfigured deployment must not accumulate undeliverable rows.
    for var in (
        "NOTIFY_GENERIC_WEBHOOK_URL", "NOTIFY_TEAMS_WEBHOOK_URL",
        "NOTIFY_SLACK_WEBHOOK_URL", "NOTIFY_SYSLOG_HOST", "NOTIFY_EMAIL_RECIPIENTS",
    ):
        monkeypatch.delenv(var, raising=False)
    alert = await _open_alert(db_session)
    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"admin:*"}))
    ) as client:
        resp = await client.post(f"/api/v1/alerts/{alert.id}/acknowledge")

    assert resp.status_code == 200
    assert await _notifications(db_session, alert.id) == []


@pytest.mark.parametrize(
    ("channel", "target", "expected"),
    [
        (
            NotificationChannel.TEAMS,
            "https://user:password@teams.example.test:443/webhook/teams-secret?key=query-secret",
            "https://teams.example.test",
        ),
        (
            NotificationChannel.SLACK,
            "https://hooks.slack.example.test/services/T000/B000/slack-secret?auth=query-secret",
            "https://hooks.slack.example.test",
        ),
        (
            NotificationChannel.WEBHOOK,
            "http://webhook.example.test:80/callback/webhook-secret?token=query-secret",
            "http://webhook.example.test",
        ),
    ],
)
async def test_notification_history_labels_http_targets_without_credentials(
    db_session,
    channel: NotificationChannel,
    target: str,
    expected: str,
) -> None:
    alert = await _open_alert(db_session)
    await _add_notification(db_session, alert.id, channel=channel, target=target)

    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"read:alerts"}))
    ) as client:
        body = await _notification_history(client, alert.id)

    assert body[0]["target"] == expected
    encoded = str(body)
    assert "password" not in encoded
    assert "secret" not in encoded
    assert "query" not in encoded


@pytest.mark.parametrize(
    ("channel", "target"),
    [
        (NotificationChannel.EMAIL, "analyst@example.test"),
        (NotificationChannel.SYSLOG, "logs.example.test:6514"),
    ],
)
async def test_notification_history_keeps_non_http_targets(
    db_session, channel: NotificationChannel, target: str
) -> None:
    alert = await _open_alert(db_session)
    await _add_notification(db_session, alert.id, channel=channel, target=target)

    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"read:alerts"}))
    ) as client:
        body = await _notification_history(client, alert.id)

    assert body[0]["target"] == target


async def test_notification_history_uses_fallback_for_unparseable_webhook(
    db_session,
) -> None:
    alert = await _open_alert(db_session)
    target = "not a URL/secret-path?token=query-secret"
    await _add_notification(
        db_session, alert.id, channel=NotificationChannel.WEBHOOK, target=target
    )

    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"read:alerts"}))
    ) as client:
        body = await _notification_history(client, alert.id)

    assert body[0]["target"] == "(webhook)"
    assert "secret" not in str(body)


async def test_transport_error_stored_and_returned_without_webhook_url(db_session) -> None:
    alert = await _open_alert(db_session)
    target = "https://hooks.example.test/secret-path?token=query-secret"
    notification = await _add_notification(
        db_session, alert.id, channel=NotificationChannel.WEBHOOK, target=target
    )

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"could not connect to {request.url}", request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as outbound:
        dispatcher = NotificationDispatcher(
            db_session,
            {NotificationChannel.WEBHOOK: GenericWebhookNotifier(outbound)},
            RoutingPolicy(),
        )
        stats = await dispatcher.dispatch_due()
        await db_session.commit()

    await db_session.refresh(notification)
    assert stats == {"sent": 0, "failed": 1, "dead_letter": 0}
    assert notification.error_message == "transport error: ConnectError"
    assert target not in notification.error_message

    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"read:alerts"}))
    ) as client:
        body = await _notification_history(client, alert.id)

    assert body[0]["error_message"] == "transport error: ConnectError"
    assert "secret" not in str(body)


@pytest.mark.parametrize(
    ("channel", "target", "error_message", "expected"),
    [
        (
            NotificationChannel.TEAMS,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error for https://hooks.example.test/SECRET?token=credential",
            "transport error for https://hooks.example.test",
        ),
        (
            NotificationChannel.SLACK,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error for /SECRET?token=credential",
            "transport error for https://hooks.example.test",
        ),
        (
            NotificationChannel.WEBHOOK,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error: ConnectError",
            "transport error: ConnectError",
        ),
        (
            NotificationChannel.EMAIL,
            "analyst@example.test",
            "delivery failed for analyst@example.test",
            "delivery failed for analyst@example.test",
        ),
        (
            NotificationChannel.SYSLOG,
            "logs.example.test:6514",
            "delivery failed for logs.example.test:6514",
            "delivery failed for logs.example.test:6514",
        ),
    ],
)
async def test_notification_history_sanitizes_legacy_error_messages(
    db_session,
    channel: NotificationChannel,
    target: str,
    error_message: str,
    expected: str,
) -> None:
    alert = await _open_alert(db_session)
    await _add_notification(
        db_session,
        alert.id,
        channel=channel,
        target=target,
        error_message=error_message,
    )

    async with _client(
        db_session, Principal(subject="analyst", permissions=frozenset({"read:alerts"}))
    ) as client:
        body = await _notification_history(client, alert.id)

    assert body[0]["error_message"] == expected
