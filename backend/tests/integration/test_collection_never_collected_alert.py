"""The never-collected state of both collection health monitors.

The first-run grace and the zero-source handling are pinned by the existing
log-source and offense suites. These tests add the state past the grace: a
collector that never completed a run must still be reported, with a reason that
says so instead of reading like an ordinary staleness.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.metric_collector import COLLECTOR_NAME as LOG_SOURCE_COLLECTOR
from app.collectors.offense_collector import COLLECTOR_NAME as OFFENSE_COLLECTOR
from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, Severity
from app.models.monitoring import CollectionWatermark
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NEVER_COLLECTED = "No collection has completed since install"
LOG_SOURCE_STALE_DESCRIPTION = (
    "The last read failed or did not run, so the platform cannot confirm whether "
    "sources are silent. NO_EVENTS alerts are not raised for the missing intervals."
)
OFFENSE_STALE_DESCRIPTION = (
    "The read-only offense collector has not completed within its configured "
    "freshness threshold."
)


@pytest_asyncio.fixture
async def task_maker(db_schema: str, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        yield maker
    finally:
        await engine.dispose()


def _log_source_threshold() -> int:
    return tasks._log_source_stale_threshold_seconds(
        get_settings().collection_interval_seconds
    )


def _offense_threshold() -> int:
    return get_settings().offense_stale_after_seconds


async def _instance(
    maker,
    *,
    monitored: bool = True,
    registered_at: datetime | None = None,
):  # type: ignore[no-untyped-def]
    async with maker() as session:
        instance = await make_instance(session)
        if registered_at is not None:
            instance.created_at = registered_at
        if monitored:
            await make_log_source(session, instance)
        await session.commit()
        return instance


async def _watermark(
    maker,
    instance_id: uuid.UUID,
    collector: str,
    *,
    last_run_at: datetime | None = None,
    consecutive_failures: int = 0,
) -> None:  # type: ignore[no-untyped-def]
    async with maker() as session:
        session.add(
            CollectionWatermark(
                instance_id=instance_id,
                collector=collector,
                last_run_at=last_run_at,
                consecutive_failures=consecutive_failures,
            )
        )
        await session.commit()


async def _alerts(maker) -> list[Alert]:  # type: ignore[no-untyped-def]
    async with maker() as session:
        return list((await session.scalars(select(Alert).order_by(Alert.source_id))).all())


async def test_young_log_source_console_without_watermark_opens_nothing(task_maker) -> None:
    instance = await _instance(task_maker)

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "awaiting_first_run"
    assert await _alerts(task_maker) == []


async def test_old_log_source_console_without_watermark_reports_never_collected(
    task_maker,
) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["judged"] is True
    assert outcome["reason"] == "never_collected"
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].severity == Severity.HIGH
    assert alerts[0].description.startswith(NEVER_COLLECTED)
    # The shared title stays: an existing test pins it for this exact state.
    assert alerts[0].title == f"Log source volume collection is stale for {instance.name}"


async def test_log_source_watermark_row_without_a_run_is_never_collected(task_maker) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )
    await _watermark(task_maker, instance.id, LOG_SOURCE_COLLECTOR)

    result = await tasks.detect_stale_log_source_collection(instance.name)

    assert result["results"][0]["reason"] == "never_collected"
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].description.startswith(NEVER_COLLECTED)


async def test_log_source_console_behind_keeps_the_ordinary_stale_reason(task_maker) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )
    await _watermark(
        task_maker,
        instance.id,
        LOG_SOURCE_COLLECTOR,
        last_run_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["reason"] is None
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].description == LOG_SOURCE_STALE_DESCRIPTION


async def test_log_source_console_without_a_monitored_source_gets_no_verdict(
    task_maker,
) -> None:
    instance = await _instance(
        task_maker,
        monitored=False,
        registered_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "no_monitored_sources"
    assert await _alerts(task_maker) == []


async def test_young_offense_console_without_watermark_opens_nothing(task_maker) -> None:
    instance = await _instance(task_maker)

    result = await tasks.detect_stale_offense_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "awaiting_first_run"
    assert await _alerts(task_maker) == []


async def test_old_offense_console_without_watermark_reports_never_collected(
    task_maker,
) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_offense_threshold() + 60),
    )

    result = await tasks.detect_stale_offense_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["judged"] is True
    assert outcome["reason"] == "never_collected"
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].severity == Severity.HIGH
    assert alerts[0].description.startswith(NEVER_COLLECTED)
    assert alerts[0].title == f"Offense collection is stale for {instance.name}"


async def test_offense_console_behind_keeps_the_ordinary_stale_reason(task_maker) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_offense_threshold() + 60),
    )
    await _watermark(
        task_maker,
        instance.id,
        OFFENSE_COLLECTOR,
        last_run_at=datetime.now(UTC) - timedelta(seconds=_offense_threshold() + 60),
    )

    result = await tasks.detect_stale_offense_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["reason"] is None
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].description == OFFENSE_STALE_DESCRIPTION


async def test_offense_monitor_is_console_level_even_with_no_log_source(task_maker) -> None:
    """Offense collection is not per source: an empty inventory is no reason to stay silent."""
    instance = await _instance(
        task_maker,
        monitored=False,
        registered_at=datetime.now(UTC) - timedelta(seconds=_offense_threshold() + 60),
    )

    result = await tasks.detect_stale_offense_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["judged"] is True
    assert outcome["reason"] == "never_collected"
    assert len(await _alerts(task_maker)) == 1


async def test_never_collected_alert_resolves_after_the_first_successful_run(
    task_maker,
) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )
    await tasks.detect_stale_log_source_collection(instance.name)
    opened = (await _alerts(task_maker))[0]
    assert opened.description.startswith(NEVER_COLLECTED)

    await _watermark(
        task_maker, instance.id, LOG_SOURCE_COLLECTOR, last_run_at=datetime.now(UTC)
    )
    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is False
    assert outcome["reason"] is None
    alert = (await _alerts(task_maker))[0]
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "system:log-source-collection-monitor"
    assert alert.resolution_reason_code == "alert.log_source_collection.recovered"


async def test_failed_runs_without_a_success_keep_the_ordinary_stale_reason(task_maker) -> None:
    """A collector that failed is not 'never collected': it tried and the read failed."""
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_log_source_threshold() + 60),
    )
    await _watermark(task_maker, instance.id, LOG_SOURCE_COLLECTOR, consecutive_failures=2)

    result = await tasks.detect_stale_log_source_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["reason"] is None
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].description == LOG_SOURCE_STALE_DESCRIPTION
