"""First-collection grace and alert lifecycle for the offense health monitor."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.offense_collector import COLLECTOR_NAME
from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, Severity
from app.models.monitoring import CollectionWatermark
from app.workers import tasks
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


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
    return get_settings().offense_stale_after_seconds


async def _instance(
    maker,
    *,
    registered_at: datetime | None = None,
):  # type: ignore[no-untyped-def]
    async with maker() as session:
        instance = await make_instance(session)
        if registered_at is not None:
            instance.created_at = registered_at
        await session.commit()
        return instance


async def _watermark(
    maker,
    instance_id: uuid.UUID,
    *,
    last_run_at: datetime | None = None,
    consecutive_failures: int = 0,
) -> None:  # type: ignore[no-untyped-def]
    async with maker() as session:
        session.add(
            CollectionWatermark(
                instance_id=instance_id,
                collector=COLLECTOR_NAME,
                last_run_at=last_run_at,
                consecutive_failures=consecutive_failures,
            )
        )
        await session.commit()


async def _alerts(maker) -> list[Alert]:  # type: ignore[no-untyped-def]
    async with maker() as session:
        return list((await session.scalars(select(Alert).order_by(Alert.source_id))).all())


async def _record_successful_run(  # type: ignore[no-untyped-def]
    maker, instance_id: uuid.UUID
) -> None:
    """Update the watermark row in place, the way a successful collector run does."""
    async with maker() as session:
        watermark = await session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance_id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        assert watermark is not None
        watermark.last_run_at = datetime.now(UTC)
        watermark.consecutive_failures = 0
        await session.commit()


async def test_missing_watermark_inside_the_grace_is_awaiting_first_run(
    task_maker,
) -> None:
    instance = await _instance(task_maker)

    result = await tasks.detect_stale_offense_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["status"] == "ok"
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "awaiting_first_run"
    assert await _alerts(task_maker) == []


async def test_missing_watermark_grace_boundary_is_strict(
    task_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    threshold = _threshold_seconds()
    frozen = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return frozen

    monkeypatch.setattr(tasks, "datetime", _FrozenDateTime)
    one_second_inside = await _instance(
        task_maker, registered_at=frozen - timedelta(seconds=threshold - 1)
    )
    at_boundary = await _instance(
        task_maker, registered_at=frozen - timedelta(seconds=threshold)
    )

    result = await tasks.detect_stale_offense_collection()
    outcomes = {entry["instance"]: entry for entry in result["results"]}

    # Strictly less than the threshold is the grace; at exactly the threshold the
    # console is judged, the same boundary the log-source monitor uses.
    assert outcomes[one_second_inside.name]["status"] == "ok"
    assert outcomes[one_second_inside.name]["judged"] is False
    assert outcomes[one_second_inside.name]["reason"] == "awaiting_first_run"
    assert outcomes[at_boundary.name]["stale"] is True
    alerts = await _alerts(task_maker)
    assert {alert.source_id for alert in alerts} == {at_boundary.id}


async def test_missing_watermark_after_the_grace_opens_a_high_alert(task_maker) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60),
    )

    result = await tasks.detect_stale_offense_collection(instance.name)

    assert result["results"][0]["stale"] is True
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].severity == Severity.HIGH
    assert alerts[0].source_type == "qradar_instance"
    assert alerts[0].source_id == instance.id
    assert alerts[0].evidence_snapshot["last_run_at"] is None
    assert alerts[0].evidence_snapshot["consecutive_failures"] == 0


async def test_failing_collector_inside_the_grace_is_stale(task_maker) -> None:
    instance = await _instance(task_maker)
    await _watermark(task_maker, instance.id, consecutive_failures=1)

    result = await tasks.detect_stale_offense_collection(instance.name)

    outcome = result["results"][0]
    assert outcome["stale"] is True
    assert outcome["judged"] is True
    assert len(await _alerts(task_maker)) == 1


async def test_fresh_run_resolves_the_alert(task_maker) -> None:
    instance = await _instance(
        task_maker,
        registered_at=datetime.now(UTC) - timedelta(seconds=_threshold_seconds() + 60),
    )
    await tasks.detect_stale_offense_collection(instance.name)
    assert (await _alerts(task_maker))[0].status == AlertStatus.OPEN

    await _watermark(task_maker, instance.id, last_run_at=datetime.now(UTC))
    result = await tasks.detect_stale_offense_collection(instance.name)

    assert result["results"][0]["stale"] is False
    alert = (await _alerts(task_maker))[0]
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "system:offense-collection-monitor"
    assert alert.resolution_reason_code == "alert.offense_collection.recovered"


async def test_recovered_watermark_inside_the_grace_is_judged_fresh(task_maker) -> None:
    """A console inside its first-run window whose collector then succeeds is judged.

    Only a watermark with no run at all is `awaiting_first_run`: once the row carries a
    run time, freshness is measured however young the console is, so the alert the failing
    run opened is resolved as a recovery.
    """
    # Registered now, so the console is inside offense_stale_after_seconds. The grace
    # must not apply once this watermark has a run time.
    instance = await _instance(task_maker, registered_at=datetime.now(UTC))
    await _watermark(task_maker, instance.id, consecutive_failures=1)
    first_run = await tasks.detect_stale_offense_collection(instance.name)

    assert first_run["results"][0]["judged"] is True
    assert first_run["results"][0]["stale"] is True
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].status == AlertStatus.OPEN
    assert alerts[0].severity == Severity.HIGH

    await _record_successful_run(task_maker, instance.id)
    second_run = await tasks.detect_stale_offense_collection(instance.name)

    outcome = second_run["results"][0]
    assert outcome["judged"] is True
    assert outcome["stale"] is False
    alerts = await _alerts(task_maker)
    assert len(alerts) == 1
    assert alerts[0].status == AlertStatus.RESOLVED
    assert alerts[0].resolution_reason_code == "alert.offense_collection.recovered"

