"""SOC-12d: rules enqueue `destination:<id>` rows; the dispatcher delivers them (mocked)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC

import httpx
import pytest
from sqlalchemy import event, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.webhook_v1 import SIGNATURE_HEADER
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
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from tests.integration.factories import FakeClock, utc
from tests.integration.test_fleet_api import ADMIN, Client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PUBLIC = "93.184.216.34"
SECRET = "delivery-secret-4410"
PATH_TOKEN = "path-token-90aa"
URL = f"https://hooks.example/{PATH_TOKEN}"


def _policy() -> RoutingPolicy:
    return RoutingPolicy(rules=[RoutingRule(channel=NotificationChannel.SLACK, target="hook")])


def _resolver(host: str, port: int) -> tuple[str, ...]:
    return (PUBLIC,)


def _transport(handler):  # type: ignore[no-untyped-def]
    return lambda: httpx.MockTransport(handler)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(204)


async def _alert(session, severity=Severity.HIGH, source_type="log_source", **context):  # type: ignore[no-untyped-def]
    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=f"anomaly:{uuid.uuid4().hex[:8]}",
            title="Volume drop",
            severity=severity,
            source_type=source_type,
            source_id=uuid.uuid4(),
            context=context,
            evidence={"observed_value": 3.0},
        )
    )
    return result.alert


async def _destination(session, name="SOAR", **over):  # type: ignore[no-untyped-def]
    values = dict(
        name=name,
        enabled=True,
        url=URL,
        allow_http=False,
        allow_private=False,
        hmac_secret=SECRET,
        headers=json.dumps({"X-SOC-Key": "hv-77"}),
        created_by="t",
        updated_by="t",
    )
    values.update(over)
    row = NotificationDestination(**values)
    session.add(row)
    await session.flush()
    return row


async def _rule(session, *destinations, name="r", **over):  # type: ignore[no-untyped-def]
    values = dict(
        name=name,
        enabled=True,
        priority=100,
        min_severity="LOW",
        destination_ids=[d.id for d in destinations],
        stop_default=False,
        created_by="t",
        updated_by="t",
    )
    values.update(over)
    row = NotificationRule(**values)
    session.add(row)
    await session.flush()
    return row


def _dispatcher(session, handler=_ok, **kw):  # type: ignore[no-untyped-def]
    return NotificationDispatcher(
        session,
        {},
        _policy(),
        destination_resolver=_resolver,
        destination_transport=_transport(handler),
        **kw,
    )


async def _rows(session):  # type: ignore[no-untyped-def]
    return list(
        (
            await session.scalars(
                select(AlertNotification).order_by(
                    AlertNotification.channel, AlertNotification.target
                )
            )
        ).all()
    )


def _pairs(rows):  # type: ignore[no-untyped-def]
    return [(str(r.channel), r.target) for r in rows]


async def test_no_rules_enqueues_exactly_todays_rows(db_session) -> None:
    alert = await _alert(db_session)
    created = await _dispatcher(db_session).enqueue(alert, AlertTransition.OPENED)
    assert _pairs(created) == [("SLACK", "hook")]


async def test_matching_rule_adds_one_row_per_destination_by_id(db_session) -> None:
    d1 = await _destination(db_session, "A")
    d2 = await _destination(db_session, "B")
    await _rule(db_session, d1, d2)
    alert = await _alert(db_session)
    created = await _dispatcher(db_session).enqueue(alert, AlertTransition.OPENED)
    targets = sorted(r.target for r in created)
    assert targets == sorted(["hook", f"destination:{d1.id}", f"destination:{d2.id}"])
    assert all(URL not in r.target and PATH_TOKEN not in r.target for r in created)
    again = await _dispatcher(db_session).enqueue(alert, AlertTransition.OPENED)
    # every (alert, transition, channel, target) is already queued
    assert again == []


async def test_stop_default_suppresses_default_rows_only_for_a_matching_rule(db_session) -> None:
    d = await _destination(db_session)
    await _rule(db_session, d, stop_default=True, min_severity="CRITICAL")
    low = await _alert(db_session, Severity.HIGH)
    created = await _dispatcher(db_session).enqueue(low, AlertTransition.OPENED)
    assert _pairs(created) == [("SLACK", "hook")]  # the rule did not match: defaults stay
    high = await _alert(db_session, Severity.CRITICAL)
    created = await _dispatcher(db_session).enqueue(high, AlertTransition.OPENED)
    assert _pairs(created) == [("WEBHOOK", f"destination:{d.id}")]


async def test_a_rule_without_stop_default_keeps_the_default_rows(db_session) -> None:
    d = await _destination(db_session)
    await _rule(db_session, d)
    alert = await _alert(db_session)
    created = await _dispatcher(db_session).enqueue(alert, AlertTransition.OPENED)
    assert ("SLACK", "hook") in _pairs(created) and len(created) == 2


async def test_non_matching_rules_enqueue_no_destination_rows(db_session) -> None:
    d = await _destination(db_session)
    alert = await _alert(db_session, Severity.MEDIUM)
    minute = alert.opened_at.astimezone(UTC).hour * 60 + alert.opened_at.astimezone(UTC).minute
    await _rule(db_session, d, name="sev", min_severity="CRITICAL")
    await _rule(db_session, d, name="fam", families=["waf"])
    await _rule(
        db_session,
        d,
        name="win",
        window_start_minute=(minute + 10) % 1440,
        window_end_minute=(minute + 20) % 1440,
    )
    created = await _dispatcher(db_session).enqueue(alert, AlertTransition.OPENED)
    assert _pairs(created) == [("SLACK", "hook")]


async def test_delivery_sends_exact_v1_bytes_signed_with_idempotency_key(db_session) -> None:
    d = await _destination(db_session)
    await _rule(db_session, d)
    alert = await _alert(db_session)
    dispatcher_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        dispatcher_seen.append(request)
        return httpx.Response(204)

    disp = _dispatcher(db_session, handler)
    await disp.enqueue(alert, AlertTransition.OPENED)
    stats = await disp.dispatch_due()
    # the SLACK row has no notifier registered: it dead-letters; the destination row is sent
    assert stats["sent"] == 1
    [request] = dispatcher_seen
    body = request.content
    event_body = json.loads(body)
    assert event_body["schema_version"] == 1 and event_body["event"] == "opened"
    assert event_body["alert_id"] == str(alert.id)
    key = f"{alert.id}:OPENED:{d.id}"
    assert event_body["idempotency_key"] == key
    assert request.headers["Idempotency-Key"] == key
    assert request.headers["X-SOC-Key"] == "hv-77"
    assert request.headers["Host"] == "hooks.example"
    expected = "v1=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert request.headers[SIGNATURE_HEADER] == expected
    assert request.url.host == PUBLIC  # pinned to the checked address
    row = next(r for r in await _rows(db_session) if r.target.startswith("destination:"))
    assert row.status == NotificationStatus.SENT and row.error_message is None


async def _one_failure(db_session, handler, *, resolver=_resolver, dest=None, mutate=None):  # type: ignore[no-untyped-def]
    d = dest or await _destination(db_session)
    await _rule(db_session, d, stop_default=True)
    alert = await _alert(db_session)
    settings = Settings(
        encryption_key="x" * 44,
        notify_max_retries=2,
        notify_retry_base_seconds=1,
        notify_retry_max_seconds=2,
    )
    clock = FakeClock(utc(), step_seconds=0)
    disp = NotificationDispatcher(
        db_session,
        {},
        _policy(),
        settings=settings,
        clock=clock,
        destination_resolver=resolver,
        destination_transport=_transport(handler),
    )
    [row] = await disp.enqueue(alert, AlertTransition.OPENED)
    if mutate:
        mutate(row)
    return disp, row, clock


async def test_5xx_and_429_retry_then_dead_letter_after_max_attempts(db_session) -> None:
    for code in (503, 429):
        await db_session.rollback()
        disp, row, clock = await _one_failure(db_session, lambda r, c=code: httpx.Response(c))
        for expected in (NotificationStatus.FAILED, NotificationStatus.FAILED):
            await disp.dispatch_due()
            assert row.status == expected and row.next_attempt_at is not None
            clock.advance(3600)
        stats = await disp.dispatch_due()
        assert stats["dead_letter"] == 1
        assert row.status == NotificationStatus.DEAD_LETTER and row.attempts == 3
        assert row.error_message == f"http_status {code}"
        await db_session.rollback()


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (lambda r: httpx.Response(404), "http_status 404"),
        (lambda r: httpx.Response(302, headers={"Location": "https://x.example/"}),
         "redirect_refused 302"),
    ],
)
async def test_4xx_and_3xx_are_permanent_on_the_first_attempt(db_session, handler, message) -> None:
    disp, row, _ = await _one_failure(db_session, handler)
    stats = await disp.dispatch_due()
    assert stats["dead_letter"] == 1 and row.attempts == 1
    assert row.status == NotificationStatus.DEAD_LETTER
    assert row.error_message == message
    assert PATH_TOKEN not in row.error_message and "hooks.example" not in row.error_message


async def test_timeout_and_transport_errors_retry(db_session) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t")

    disp, row, _ = await _one_failure(db_session, boom)
    await disp.dispatch_due()
    assert row.status == NotificationStatus.FAILED and row.error_message == "timeout"


async def test_guard_refusal_is_permanent(db_session) -> None:
    disp, row, _ = await _one_failure(db_session, _ok, resolver=lambda h, p: ("10.0.0.5",))
    stats = await disp.dispatch_due()
    assert stats["dead_letter"] == 1 and row.attempts == 1
    assert row.error_message == "ssrf_refused"


async def test_builder_error_is_permanent_not_six_retries(db_session) -> None:
    disp, row, _ = await _one_failure(db_session, _ok, mutate=lambda r: setattr(r, "payload", {}))
    stats = await disp.dispatch_due()
    assert stats["dead_letter"] == 1 and row.attempts == 1
    assert row.error_message == "payload_invalid"


async def test_disabled_or_missing_destination_dead_letters_no_destination(db_session) -> None:
    disp, row, _ = await _one_failure(db_session, _ok, dest=await _destination(db_session, "Off"))
    off = await db_session.get(NotificationDestination, uuid.UUID(row.target.split(":")[1]))
    off.enabled = False
    await db_session.flush()
    await disp.dispatch_due()
    assert row.status == NotificationStatus.DEAD_LETTER and row.error_message == "no_destination"
    assert row.attempts == 1

    # a destination deleted after enqueue
    other = AlertNotification(
        alert_id=row.alert_id,
        channel=NotificationChannel.WEBHOOK,
        target=f"destination:{uuid.uuid4()}",
        transition=AlertTransition.OPENED,
        status=NotificationStatus.PENDING,
        max_attempts=3,
        next_attempt_at=utc(),
        payload={},
    )
    db_session.add(other)
    await db_session.flush()
    await _dispatcher(db_session).dispatch_due()
    assert other.status == NotificationStatus.DEAD_LETTER
    assert other.error_message == "no_destination"


async def test_statement_counts_one_rules_load_and_one_destination_load(db_session) -> None:
    d = await _destination(db_session)
    await _rule(db_session, d)
    alerts = [await _alert(db_session) for _ in range(5)]
    seen: list[str] = []

    @event.listens_for(db_session.bind.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, *a):  # type: ignore[no-untyped-def]
        seen.append(statement)

    disp = _dispatcher(db_session)
    for alert in alerts:
        await disp.enqueue(alert, AlertTransition.OPENED)
    assert sum("FROM notification_rule" in s for s in seen) == 1
    assert sum("FROM notification_destination" in s for s in seen) == 1  # the loader's own

    seen.clear()
    fresh = _dispatcher(db_session)
    stats = await fresh.dispatch_due()
    assert stats["sent"] == 5
    assert sum("FROM notification_destination" in s for s in seen) == 1


async def test_history_shows_the_destination_name_never_the_url(db_session) -> None:
    d = await _destination(db_session, "Pager SOAR")
    await _rule(db_session, d, stop_default=True)
    alert = await _alert(db_session)
    disp = _dispatcher(db_session)
    await disp.enqueue(alert, AlertTransition.OPENED)
    await disp.dispatch_due()
    await db_session.commit()
    admin = Client(ADMIN)
    listed = admin.get(f"/api/v1/alerts/{alert.id}/notifications")
    assert listed.status_code == 200, listed.text
    [entry] = listed.json()
    assert entry["target"] == "Pager SOAR"
    assert PATH_TOKEN not in listed.text and "hooks.example" not in listed.text

    await db_session.delete(await db_session.get(NotificationDestination, d.id))
    await db_session.commit()
    gone = admin.get(f"/api/v1/alerts/{alert.id}/notifications")
    assert gone.json()[0]["target"] == "(deleted destination)"
    assert str(d.id) not in gone.text
