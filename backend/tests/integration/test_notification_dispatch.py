"""Notification dispatch: enqueue dedup, delivery, retry, dead-letter, recovery.
No real notification is sent (MockNotifier). DB-gated."""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.base import MockNotifier
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
from app.services.locks import collector_lock_key
from app.workers import tasks
from tests.integration.factories import FakeClock, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _policy() -> RoutingPolicy:
    return RoutingPolicy(rules=[RoutingRule(channel=NotificationChannel.SLACK, target="hook")])


async def _open_alert(session):
    svc = AlertService(session)
    r = await svc.open_or_update(
        AlertInput(fingerprint=f"anomaly:{uuid.uuid4().hex[:6]}", title="t",
                   severity=Severity.HIGH, source_type="log_source", source_id=uuid.uuid4(),
                   evidence={"observed_value": 3.0})
    )
    return r.alert


async def test_enqueue_creates_one_row_per_route(db_session) -> None:
    alert = await _open_alert(db_session)
    d = NotificationDispatcher(db_session, {}, _policy())
    created = await d.enqueue(alert, AlertTransition.OPENED)
    assert len(created) == 1
    assert created[0].status == NotificationStatus.PENDING


async def test_enqueue_is_deduplicated_per_transition(db_session) -> None:
    alert = await _open_alert(db_session)
    d = NotificationDispatcher(db_session, {}, _policy())
    await d.enqueue(alert, AlertTransition.OPENED)
    await d.enqueue(alert, AlertTransition.OPENED)  # duplicate transition
    count = await db_session.scalar(
        select(func.count()).select_from(AlertNotification).where(
            AlertNotification.alert_id == alert.id
        )
    )
    assert count == 1


async def test_successful_delivery(db_session) -> None:
    alert = await _open_alert(db_session)
    notifier = MockNotifier(NotificationChannel.SLACK)
    d = NotificationDispatcher(db_session, {NotificationChannel.SLACK: notifier}, _policy())
    await d.enqueue(alert, AlertTransition.OPENED)
    stats = await d.dispatch_due()
    assert stats["sent"] == 1
    assert len(notifier.sent) == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.SENT
    assert row.sent_at is not None


async def test_retry_then_dead_letter(db_session) -> None:
    alert = await _open_alert(db_session)
    # Always fails; max 2 attempts total -> ends in DEAD_LETTER.
    notifier = MockNotifier(NotificationChannel.SLACK, fail_times=99)
    settings = Settings(encryption_key="x" * 44, notify_max_retries=1,
                        notify_retry_base_seconds=1, notify_retry_max_seconds=2)
    clock = FakeClock(utc(), step_seconds=0)
    d = NotificationDispatcher(
        db_session, {NotificationChannel.SLACK: notifier}, _policy(),
        settings=settings, clock=clock,
    )
    await d.enqueue(alert, AlertTransition.OPENED)

    s1 = await d.dispatch_due()          # attempt 1 fails -> FAILED
    assert s1["failed"] == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.FAILED
    assert row.next_attempt_at is not None

    clock.advance(3600)                  # move past backoff
    s2 = await d.dispatch_due()          # attempt 2 exhausts retries -> DEAD_LETTER
    assert s2["dead_letter"] == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.DEAD_LETTER


async def test_permanent_failure_dead_letters_immediately(db_session) -> None:
    alert = await _open_alert(db_session)
    notifier = MockNotifier(NotificationChannel.SLACK, fail_times=99, permanent=True)
    d = NotificationDispatcher(db_session, {NotificationChannel.SLACK: notifier}, _policy())
    await d.enqueue(alert, AlertTransition.OPENED)
    stats = await d.dispatch_due()
    assert stats["dead_letter"] == 1  # 4xx-style permanent -> no retries


async def test_recovery_notification_toggle(db_session) -> None:
    alert = await _open_alert(db_session)
    settings = Settings(encryption_key="x" * 44, notify_send_recovery=False)
    d = NotificationDispatcher(db_session, {}, _policy(), settings=settings)
    created = await d.enqueue(alert, AlertTransition.RESOLVED)
    assert created == []  # recovery disabled -> nothing enqueued


async def test_dispatch_lock_key_is_not_the_anomaly_or_search_key() -> None:
    dispatch = collector_lock_key(tasks.DISPATCH_NOTIFICATIONS_LOCK_ID, "dispatch_notifications")
    anomaly = collector_lock_key(tasks.ANOMALY_EVALUATION_LOCK_ID, "anomaly_evaluation")
    search = collector_lock_key(tasks.SEARCH_SCHEDULER_LOCK_ID, "search_scheduler")
    assert len({dispatch, anomaly, search}) == 3


def _bind(db_schema: str, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    return engine, maker


async def test_two_dispatch_runs_do_not_overlap(
    db_schema: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine, _maker = _bind(db_schema, monkeypatch)
    built = {"n": 0}
    calls = {"n": 0}
    entered, release = asyncio.Event(), asyncio.Event()

    def build_notifiers() -> dict:  # type: ignore[type-arg]
        built["n"] += 1
        return {}

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, int]:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            await release.wait()
        return {"sent": 0, "failed": 0, "dead_letter": 0}

    monkeypatch.setattr(tasks, "build_notifiers", build_notifiers)
    monkeypatch.setattr(NotificationDispatcher, "dispatch_due", dispatch_due)
    caplog.set_level(logging.INFO, logger="app.tasks")
    first: asyncio.Task[dict] | None = None
    try:
        first = asyncio.create_task(tasks.dispatch_notifications())
        await asyncio.wait_for(entered.wait(), 5)
        second = await asyncio.wait_for(tasks.dispatch_notifications(), 5)
        assert second == {"status": "skipped_locked"}
        assert calls["n"] == 1 and built["n"] == 1
        assert any("already in progress" in record.getMessage() for record in caplog.records)
        release.set()
        assert await asyncio.wait_for(first, 5) == {
            "sent": 0,
            "failed": 0,
            "dead_letter": 0,
        }
        assert await tasks.dispatch_notifications() == {
            "sent": 0,
            "failed": 0,
            "dead_letter": 0,
        }
        assert calls["n"] == 2 and built["n"] == 2
    finally:
        release.set()
        if first is not None and not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        await engine.dispose()


async def test_a_failed_dispatch_releases_the_lock(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _maker = _bind(db_schema, monkeypatch)
    state = {"fail": True}

    def build_notifiers() -> dict:  # type: ignore[type-arg]
        return {}

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, int]:  # type: ignore[no-untyped-def]
        if state["fail"]:
            raise RuntimeError("dispatch fault")
        return {"sent": 0, "failed": 0, "dead_letter": 0}

    monkeypatch.setattr(tasks, "build_notifiers", build_notifiers)
    monkeypatch.setattr(NotificationDispatcher, "dispatch_due", dispatch_due)
    try:
        with pytest.raises(RuntimeError, match="dispatch fault"):
            await tasks.dispatch_notifications()
        state["fail"] = False
        assert await tasks.dispatch_notifications() == {
            "sent": 0,
            "failed": 0,
            "dead_letter": 0,
        }
    finally:
        await engine.dispose()
