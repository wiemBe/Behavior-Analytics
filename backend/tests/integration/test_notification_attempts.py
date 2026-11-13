"""SOC-12e: one attempt row per delivery attempt, every channel, capped at eight."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, select, text

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.base import MockNotifier, Notifier, NotifierError
from app.alerts.notifiers.channels import GenericWebhookNotifier, SlackNotifier, TeamsNotifier
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.alerts.service import AlertInput, AlertService
from app.core.config import Settings
from app.models.alert import AlertNotification
from app.models.enums import (
    AlertTransition,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from app.models.notification_attempt import MAX_ATTEMPTS_KEPT, NotificationAttempt
from app.models.notification_destination import NotificationDestination
from tests.integration.factories import FakeClock, utc
from tests.integration.test_fleet_api import ADMIN, Client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECRET_URL = "https://hooks.example/secret-path-7781?token=abc"
CHANNELS = list(NotificationChannel)


def _policy(channel: NotificationChannel, target: str = SECRET_URL) -> RoutingPolicy:
    return RoutingPolicy(rules=[RoutingRule(channel=channel, target=target)])


async def _alert(session):  # type: ignore[no-untyped-def]
    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=f"anomaly:{uuid.uuid4().hex[:8]}",
            title="Volume drop",
            severity=Severity.HIGH,
            source_type="log_source",
            source_id=uuid.uuid4(),
            evidence={"observed_value": 3.0},
        )
    )
    return result.alert


def _settings(retries: int = 20) -> Settings:
    return Settings(
        encryption_key="x" * 44,
        notify_max_retries=retries,
        notify_retry_base_seconds=1,
        notify_retry_max_seconds=2,
    )


class _Raises(Notifier):
    def __init__(self, channel: NotificationChannel, message: str, permanent: bool = False):
        self.channel = channel
        self._message = message
        self._permanent = permanent

    async def send(self, target, message):  # type: ignore[no-untyped-def]
        raise NotifierError(self._message, permanent=self._permanent)


async def _attempts(session, notification=None):  # type: ignore[no-untyped-def]
    statement = select(NotificationAttempt).order_by(
        NotificationAttempt.attempted_at, NotificationAttempt.id
    )
    if notification is not None:
        statement = statement.where(NotificationAttempt.notification_id == notification.id)
    return list((await session.scalars(statement)).all())


@pytest.mark.parametrize("channel", CHANNELS)
async def test_every_channel_writes_an_ok_attempt(db_session, channel) -> None:
    alert = await _alert(db_session)
    disp = NotificationDispatcher(
        db_session, {channel: MockNotifier(channel)}, _policy(channel), settings=_settings()
    )
    [notif] = await disp.enqueue(alert, AlertTransition.OPENED)
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, notif)
    assert (attempt.error_class, attempt.http_status) == ("ok", None)


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize(
    ("message", "permanent", "expected"),
    [
        ("retryable status 503", False, ("http_status", 503)),
        ("permanent status 404", True, ("http_status", 404)),
        ("transport error: ConnectError", False, ("transport", None)),
        ("SMTP host not configured", True, ("permanent", None)),
    ],
)
async def test_every_channel_writes_a_failure_attempt(
    db_session, channel, message, permanent, expected
) -> None:
    alert = await _alert(db_session)
    notifier = _Raises(channel, message, permanent)
    disp = NotificationDispatcher(
        db_session, {channel: notifier}, _policy(channel), settings=_settings()
    )
    [notif] = await disp.enqueue(alert, AlertTransition.OPENED)
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, notif)
    assert (attempt.error_class, attempt.http_status) == expected


async def test_real_http_notifiers_produce_the_classified_status(db_session) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(int(request.url.path.strip("/")))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    for notifier_cls, channel in (
        (TeamsNotifier, NotificationChannel.TEAMS),
        (SlackNotifier, NotificationChannel.SLACK),
        (GenericWebhookNotifier, NotificationChannel.WEBHOOK),
    ):
        for code, expected in ((502, ("http_status", 502)), (403, ("http_status", 403))):
            alert = await _alert(db_session)
            disp = NotificationDispatcher(
                db_session,
                {channel: notifier_cls(client)},
                _policy(channel, f"https://h.example/{code}"),
                settings=_settings(),
            )
            [notif] = await disp.enqueue(alert, AlertTransition.OPENED)
            await disp.dispatch_due()
            [attempt] = await _attempts(db_session, notif)
            assert (attempt.error_class, attempt.http_status) == expected
    await client.aclose()


async def test_no_notifier_and_destination_classes(db_session) -> None:
    alert = await _alert(db_session)
    disp = NotificationDispatcher(db_session, {}, _policy(NotificationChannel.SLACK))
    [notif] = await disp.enqueue(alert, AlertTransition.OPENED)
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, notif)
    assert attempt.error_class == "no_notifier"

    missing = AlertNotification(
        alert_id=alert.id,
        channel=NotificationChannel.WEBHOOK,
        target=f"destination:{uuid.uuid4()}",
        transition=AlertTransition.OPENED,
        status=NotificationStatus.PENDING,
        max_attempts=3,
        next_attempt_at=utc(),
        payload={},
    )
    db_session.add(missing)
    await db_session.flush()
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, missing)
    assert attempt.error_class == "no_destination"

    dest = NotificationDestination(
        name="D", enabled=True, url=SECRET_URL, allow_http=False, allow_private=False,
        hmac_secret="s3cret-xyz", headers="{}", created_by="t", updated_by="t",
    )  # fmt: skip
    db_session.add(dest)
    await db_session.flush()
    bad = AlertNotification(
        alert_id=alert.id,
        channel=NotificationChannel.WEBHOOK,
        target=f"destination:{dest.id}",
        transition=AlertTransition.RESOLVED,
        status=NotificationStatus.PENDING,
        max_attempts=3,
        next_attempt_at=utc(),
        payload={},
    )
    db_session.add(bad)
    await db_session.flush()
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, bad)
    assert attempt.error_class == "payload_invalid"


async def test_a_stored_forbidden_header_records_payload_invalid(db_session) -> None:
    dest = NotificationDestination(
        name="D2", enabled=True, url=SECRET_URL, allow_http=False, allow_private=False,
        hmac_secret="s3cret-xyz",
        headers=json.dumps({"Transfer-Encoding": "chunked"}),
        created_by="t", updated_by="t",
    )  # fmt: skip
    db_session.add(dest)
    await db_session.flush()
    alert = await _alert(db_session)
    notif = AlertNotification(
        alert_id=alert.id,
        channel=NotificationChannel.WEBHOOK,
        target=f"destination:{dest.id}",
        transition=AlertTransition.OPENED,
        status=NotificationStatus.PENDING,
        max_attempts=3,
        next_attempt_at=utc(),
        payload={
            "title": "t", "alert_type": "log_source", "severity": "HIGH", "status": "OPEN",
            "opened_at": "2026-11-13T09:00:00+00:00",
        },
    )  # fmt: skip
    db_session.add(notif)
    await db_session.flush()
    def no_transport() -> httpx.AsyncBaseTransport:
        raise AssertionError("a refused header must not reach the transport")

    disp = NotificationDispatcher(
        db_session,
        {},
        _policy(NotificationChannel.SLACK),
        destination_resolver=lambda h, p: ("93.184.216.34",),
        destination_transport=no_transport,
    )
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, notif)
    assert (attempt.error_class, attempt.http_status) == ("payload_invalid", None)
    assert notif.status == NotificationStatus.DEAD_LETTER


async def test_destination_attempt_records_sender_class_and_status(db_session) -> None:
    from sqlalchemy import update  # noqa: F401

    dest = NotificationDestination(
        name="D", enabled=True, url=SECRET_URL, allow_http=False, allow_private=False,
        hmac_secret="s3cret-xyz", headers="{}", created_by="t", updated_by="t",
    )  # fmt: skip
    db_session.add(dest)
    await db_session.flush()
    alert = await _alert(db_session)
    notif = AlertNotification(
        alert_id=alert.id,
        channel=NotificationChannel.WEBHOOK,
        target=f"destination:{dest.id}",
        transition=AlertTransition.OPENED,
        status=NotificationStatus.PENDING,
        max_attempts=3,
        next_attempt_at=utc(),
        payload={
            "title": "t", "alert_type": "log_source", "severity": "HIGH", "status": "OPEN",
            "opened_at": "2026-11-13T09:00:00+00:00",
        },
    )  # fmt: skip
    db_session.add(notif)
    await db_session.flush()
    disp = NotificationDispatcher(
        db_session,
        {},
        _policy(NotificationChannel.SLACK),
        destination_resolver=lambda h, p: ("93.184.216.34",),
        destination_transport=lambda: httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    await disp.dispatch_due()
    [attempt] = await _attempts(db_session, notif)
    assert (attempt.error_class, attempt.http_status) == ("http_status", 503)
    # nothing but class and status is stored
    columns = {c.name for c in NotificationAttempt.__table__.columns}
    assert columns == {"id", "notification_id", "attempted_at", "http_status", "error_class"}
    stored = " ".join(str(getattr(attempt, c)) for c in columns)
    for forbidden in ("hooks.example", "secret-path", "s3cret-xyz", "token=abc"):
        assert forbidden not in stored


async def _run_failing(db_session, n_runs: int):  # type: ignore[no-untyped-def]
    """One SLACK notification failing with 500+i on run i; returns (notification, clock)."""
    alert = await _alert(db_session)
    state = {"i": 0}

    class Counting(Notifier):
        channel = NotificationChannel.SLACK

        async def send(self, target, message):  # type: ignore[no-untyped-def]
            code = 500 + state["i"]
            state["i"] += 1
            raise NotifierError(f"retryable status {code}")

    clock = FakeClock(utc(), step_seconds=0)
    disp = NotificationDispatcher(
        db_session,
        {NotificationChannel.SLACK: Counting()},
        _policy(NotificationChannel.SLACK),
        settings=_settings(),
        clock=clock,
    )
    [notif] = await disp.enqueue(alert, AlertTransition.OPENED)
    for _ in range(n_runs):
        clock.advance(10)
        notif.next_attempt_at = clock() - timedelta(seconds=1)
        await disp.dispatch_due()
    return notif


async def test_the_ninth_attempt_drops_the_oldest(db_session) -> None:
    notif = await _run_failing(db_session, MAX_ATTEMPTS_KEPT + 1)
    rows = await _attempts(db_session, notif)
    assert [r.http_status for r in rows] == list(range(501, 509))
    assert len(rows) == MAX_ATTEMPTS_KEPT


async def test_attempt_writes_do_not_grow_with_the_number_of_notifications(db_session) -> None:
    seen: list[str] = []

    @event.listens_for(db_session.bind.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, *a):  # type: ignore[no-untyped-def]
        seen.append(statement)

    def attempt_statements() -> int:
        return sum("notification_attempt" in s for s in seen)

    counts = []
    for n in (1, 6):
        notifier = MockNotifier(NotificationChannel.SLACK)
        disp = NotificationDispatcher(
            db_session, {NotificationChannel.SLACK: notifier}, _policy(NotificationChannel.SLACK)
        )
        for _ in range(n):
            await disp.enqueue(await _alert(db_session), AlertTransition.OPENED)
        seen.clear()
        stats = await disp.dispatch_due()
        assert stats["sent"] == n
        counts.append(attempt_statements())
    assert counts[0] == counts[1] == 2  # one INSERT and one trim, whatever N is


async def test_attempts_cascade_with_the_notification(db_session) -> None:
    notif = await _run_failing(db_session, 2)
    assert len(await _attempts(db_session, notif)) == 2
    await db_session.execute(text("DELETE FROM alert_notification"))
    assert await _attempts(db_session) == []


async def test_api_returns_attempts_newest_first_capped(db_session) -> None:
    notif = await _run_failing(db_session, MAX_ATTEMPTS_KEPT + 3)
    await db_session.commit()
    admin = Client(ADMIN)
    response = admin.get(f"/api/v1/alerts/{notif.alert_id}/notifications")
    assert response.status_code == 200, response.text
    [entry] = response.json()
    log = entry["attempt_log"]
    assert [a["http_status"] for a in log] == list(range(510, 502, -1))
    assert set(log[0]) == {"attempted_at", "http_status", "error_class"}
    assert entry["attempts"] == MAX_ATTEMPTS_KEPT + 3  # the count field is unchanged
    assert "secret-path" not in response.text and "token=abc" not in response.text
