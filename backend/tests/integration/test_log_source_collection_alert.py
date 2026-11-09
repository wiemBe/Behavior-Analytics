"""Lifecycle, not-judged states and locking for the log-source metric collection health alert."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.metric_collector import COLLECTOR_NAME
from app.core.config import get_settings
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, AlertTransition, Severity
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.services.locks import CollectorAdvisoryLock
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source

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


def _threshold_seconds() -> int:
    return tasks._log_source_stale_threshold_seconds(
        get_settings().collection_interval_seconds
    )


async def _instance(
    maker,
    *,
    name: str | None = None,
    monitored: bool = True,
    registered_at: datetime | None = None,
):  # type: ignore[no-untyped-def]
    async with maker() as session:
        instance = await make_instance(session)
        if name is not None:
            instance.name = name
        if registered_at is not None:
            instance.created_at = registered_at
        if monitored:
            await make_log_source(session, instance)
        await session.commit()
        return instance


async def _monitor(maker, instance_id: uuid.UUID, *, enabled: bool) -> None:  # type: ignore[no-untyped-def]
    async with maker() as session:
        sources = (
            await session.scalars(
                select(LogSource).where(LogSource.instance_id == instance_id)
            )
        ).all()
        for source in sources:
            source.monitoring_enabled = enabled
        await session.commit()


async def _watermark(
    maker,
    instance_id: uuid.UUID,
    *,
    last_run_at: datetime | None = None,
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
    # Setup only: a console registered longer ago than the stale threshold, so its
    # missing watermark is judged rather than inside the first-run grace window.
    instance = await _instance(
        task_maker,
        name="Lab console",
        registered_at=datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60),
    )

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
    # Setup only: past the first-run grace window, so the missing watermark is judged.
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60),
    )

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
    # Setup only: past the first-run grace window, so both missing watermarks are judged.
    registered_at = datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60)
    first = await _instance(task_maker, registered_at=registered_at)
    second = await _instance(task_maker, registered_at=registered_at)

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


async def test_new_console_without_watermark_is_not_judged(task_maker) -> None:
    instance = await _instance(task_maker)

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "awaiting_first_run"
    assert await _alerts(task_maker) == []
    assert await _notifications(task_maker) == []


async def test_console_past_the_threshold_without_watermark_is_stale(task_maker) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60),
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["judged"] is True
    assert len(await _alerts(task_maker)) == 1


@pytest.mark.parametrize("inventory", ["empty", "all_unmonitored"])
async def test_console_without_monitored_source_is_not_judged(
    task_maker, inventory: str
) -> None:
    if inventory == "empty":
        instance = await _instance(task_maker, monitored=False)
    else:
        # The console is fresh and has no watermark, so both guards apply: the
        # no-source verdict is the one the operator needs, not the first-run grace.
        instance = await _instance(task_maker)
        await _monitor(task_maker, instance.id, enabled=False)

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "no_monitored_sources"
    assert await _alerts(task_maker) == []
    assert await _notifications(task_maker) == []


async def test_open_alert_resolves_when_the_last_monitored_source_is_switched_off(
    task_maker,
) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60),
    )
    await tasks.detect_stale_log_source_collection(instance.name)
    assert (await _alerts(task_maker))[0].status == AlertStatus.OPEN

    await _monitor(task_maker, instance.id, enabled=False)
    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["judged"] is False
    assert outcome["reason"] == "no_monitored_sources"
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].status == AlertStatus.RESOLVED
    assert alerts[0].resolved_by == "system:log-source-collection-monitor"


async def test_failing_collector_inside_the_grace_period_is_stale(task_maker) -> None:
    instance = await _instance(task_maker)
    await _watermark(
        task_maker,
        instance.id,
        consecutive_failures=1,
        last_error="QRadar search failed (sanitised)",
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["judged"] is True
    assert outcome["consecutive_failures"] == 1
    assert len(await _alerts(task_maker)) == 1


async def test_statement_count_is_constant_for_one_and_twenty_monitored_sources(
    db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    old = datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60)
    single = await _instance(maker, registered_at=old)
    many = await _instance(maker, registered_at=old)
    async with maker() as session:
        for index in range(19):
            await make_log_source(session, many, qradar_id=2001 + index)
        await session.commit()

    statements: list[str] = []

    def record(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await tasks.detect_stale_log_source_collection(single.name)
        single_count = len(statements)
        statements.clear()
        await tasks.detect_stale_log_source_collection(many.name)
        many_count = len(statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        await engine.dispose()

    assert single_count == many_count
    assert single_count > 0
