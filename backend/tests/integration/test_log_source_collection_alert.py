"""Lifecycle and locking for the log-source metric collection health alert."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.metric_collector import COLLECTOR_NAME
from app.core.config import get_settings
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, AlertTransition, Severity
from app.models.monitoring import CollectionWatermark
from app.services.locks import CollectorAdvisoryLock
from app.workers import tasks
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EVIDENCE_KEYS = {
    "last_run_at",
    "age_seconds",
    "threshold_seconds",
    "consecutive_failures",
    "watermark_at",
    "lag_seconds",
    "last_error",
}


@pytest_asyncio.fixture
async def task_maker(db_schema: str, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _instance(maker, *, name: str | None = None):  # type: ignore[no-untyped-def]
    async with maker() as session:
        instance = await make_instance(session)
        if name is not None:
            instance.name = name
        await session.commit()
        return instance


async def _watermark(
    maker,
    instance_id: uuid.UUID,
    *,
    last_run_at: datetime,
    consecutive_failures: int = 0,
    watermark_at: datetime | None = None,
    lag_seconds: int | None = None,
    last_error: str | None = None,
) -> None:  # type: ignore[no-untyped-def]
    async with maker() as session:
        session.add(
            CollectionWatermark(
                instance_id=instance_id,
                collector=COLLECTOR_NAME,
                last_run_at=last_run_at,
                consecutive_failures=consecutive_failures,
                watermark_at=watermark_at,
                lag_seconds=lag_seconds,
                last_error=last_error,
            )
        )
        await session.commit()


async def _alerts(maker) -> list[Alert]:  # type: ignore[no-untyped-def]
    async with maker() as session:
        return list((await session.scalars(select(Alert).order_by(Alert.source_id))).all())


async def _notifications(maker) -> list[AlertNotification]:  # type: ignore[no-untyped-def]
    async with maker() as session:
        return list(
            (
                await session.scalars(
                    select(AlertNotification).order_by(AlertNotification.created_at)
                )
            ).all()
        )


async def test_missing_watermark_opens_high_alert_with_complete_evidence(task_maker) -> None:
    instance = await _instance(task_maker, name="Lab console")

    result = await tasks.detect_stale_log_source_collection(instance.name)

    assert result["results"][0]["stale"] is True
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].title == "Log source volume collection is stale for Lab console"
    assert alerts[0].severity == Severity.HIGH
    assert alerts[0].source_type == "qradar_instance"
    assert alerts[0].source_id == instance.id
    assert set(alerts[0].evidence_snapshot) == EVIDENCE_KEYS


async def test_fresh_failed_run_is_stale_and_keeps_stored_error_text(task_maker) -> None:
    instance = await _instance(task_maker)
    stored_error = "QRadar search failed (sanitised)"
    await _watermark(
        task_maker,
        instance.id,
        last_run_at=datetime.now(UTC),
        consecutive_failures=1,
        watermark_at=datetime.now(UTC) - timedelta(minutes=10),
        lag_seconds=600,
        last_error=stored_error,
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    assert result["results"][0]["stale"] is True
    alerts = await _alerts(task_maker)
    assert alerts[0].evidence_snapshot["consecutive_failures"] == 1
    assert alerts[0].evidence_snapshot["last_error"] == stored_error


@pytest.mark.parametrize(
    ("age_offset_seconds", "expected_stale"),
    [(120, True), (0, False), (1, True)],
    ids=["old", "exactly-at-threshold", "one-second-over"],
)
async def test_last_run_age_boundary_controls_staleness(
    task_maker, age_offset_seconds: int, expected_stale: bool
) -> None:
    instance = await _instance(task_maker)
    threshold = tasks._log_source_stale_threshold_seconds(
        get_settings().collection_interval_seconds
    )
    await _watermark(
        task_maker,
        instance.id,
        last_run_at=datetime.now(UTC) - timedelta(seconds=threshold + age_offset_seconds),
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    assert result["results"][0]["stale"] is expected_stale
    assert len(await _alerts(task_maker)) == int(expected_stale)


async def test_large_lag_does_not_make_a_fresh_successful_run_stale(task_maker) -> None:
    instance = await _instance(task_maker)
    old_watermark = datetime.now(UTC) - timedelta(days=30)
    await _watermark(
        task_maker,
        instance.id,
        last_run_at=datetime.now(UTC),
        watermark_at=old_watermark,
        lag_seconds=30 * 24 * 60 * 60,
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    assert result["results"][0]["stale"] is False
    assert await _alerts(task_maker) == []


async def test_refresh_deduplicates_and_success_resolves_with_transition_notifications(
    task_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://notify.invalid/collector")
    instance = await _instance(task_maker)

    await tasks.detect_stale_log_source_collection(instance.name)
    await tasks.detect_stale_log_source_collection(instance.name)

    open_alerts = await _alerts(task_maker)
    assert len(open_alerts) == 1
    assert open_alerts[0].status == AlertStatus.OPEN
    assert open_alerts[0].occurrence_count == 2
    opened = await _notifications(task_maker)
    assert [item.transition for item in opened] == [AlertTransition.OPENED]

    await _watermark(
        task_maker,
        instance.id,
        last_run_at=datetime.now(UTC),
        watermark_at=datetime.now(UTC) - timedelta(minutes=5),
        lag_seconds=0,
    )
    await tasks.detect_stale_log_source_collection(instance.name)

    resolved = await _alerts(task_maker)
    assert len(resolved) == 1
    assert resolved[0].status == AlertStatus.RESOLVED
    assert resolved[0].resolved_by == "system:log-source-collection-monitor"
    notifications = await _notifications(task_maker)
    assert [item.transition for item in notifications] == [
        AlertTransition.OPENED,
        AlertTransition.RESOLVED,
    ]


async def test_two_instances_have_independent_fingerprints(task_maker) -> None:
    first = await _instance(task_maker)
    second = await _instance(task_maker)

    await tasks.detect_stale_log_source_collection()

    alerts = await _alerts(task_maker)
    assert {alert.source_id for alert in alerts} == {first.id, second.id}
    assert len({alert.fingerprint for alert in alerts}) == 2


async def test_held_detection_lock_skips_without_writing(task_maker) -> None:
    instance = await _instance(task_maker)
    async with task_maker() as lock_session, CollectorAdvisoryLock(
        lock_session,
        get_settings(),
        instance.id,
        "log_source_stale_detection",
    ) as acquired:
        assert acquired is True
        result = await tasks.detect_stale_log_source_collection(instance.name)

    assert result["results"][0]["status"] == "skipped_locked"
    assert await _alerts(task_maker) == []
