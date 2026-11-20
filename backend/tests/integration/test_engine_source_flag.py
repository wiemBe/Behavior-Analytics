"""P5: the stored QRadar `enabled` flag gates anomaly evaluation (D-94/D-95).

Stale inventory never gates anything: the stored flag is the input. With the
D-52 switch off both engine entries and the task's source query keep today's
behaviour exactly. Real Postgres, real engine and real task body.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.anomaly.engine import SKIPPED_DISABLED_IN_QRADAR, AnomalyEngine
from app.collectors.log_source_collector import COLLECTOR_NAME, INVENTORY_RUN_KEY
from app.core.config import Settings
from app.models.alert import Alert
from app.models.base import utcnow
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceDetectorState,
)
from app.models.monitoring import (
    AnomalyEvaluationFault,
    AnomalyEvaluationWatermark,
    CollectionWatermark,
)
from app.services.feature_switches import UNREADABLE_REASON
from app.workers import tasks
from tests.integration.factories import add_metric, make_instance, make_log_source, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings(*, pause: bool) -> Settings:
    return Settings(encryption_key="x" * 44, qradar_source_pause_enabled=pause)


async def _stale_inventory(session, instance) -> None:
    """An old, failed last inventory pass: display evidence, never an evaluation gate."""
    session.add(
        CollectionWatermark(
            instance_id=instance.id,
            collector=COLLECTOR_NAME,
            watermark_at=utc() - timedelta(hours=48),
            last_run_at=utc() - timedelta(hours=48),
            last_error="time_limit",
            consecutive_failures=3,
            collection_metadata={INVENTORY_RUN_KEY: {"status": "FAILED"}},
        )
    )
    await session.flush()


async def _assert_nothing_written(session, *sources) -> None:
    """No incident, transition, alert, detector-state or watermark write."""
    ids = [source.id for source in sources]
    for model, column in (
        (AnomalyEvaluationWatermark, AnomalyEvaluationWatermark.log_source_id),
        (AnomalyEvaluationFault, AnomalyEvaluationFault.log_source_id),
        (LogSourceAnomaly, LogSourceAnomaly.log_source_id),
        (LogSourceDetectorState, LogSourceDetectorState.log_source_id),
    ):
        count = await session.scalar(
            select(func.count()).select_from(model).where(column.in_(ids))
        )
        assert count == 0, model.__tablename__
    transitions = await session.scalar(
        select(func.count())
        .select_from(AnomalyStateTransition)
        .join(LogSourceAnomaly, AnomalyStateTransition.anomaly_id == LogSourceAnomaly.id)
        .where(LogSourceAnomaly.log_source_id.in_(ids))
    )
    assert transitions == 0
    alerts = await session.scalar(
        select(func.count()).select_from(Alert).where(Alert.source_id.in_(ids))
    )
    assert alerts == 0


async def test_both_entries_disabled_skip_any_protocol(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    inst = await make_instance(db_session)
    bucket = utc(h=10)
    # The guard does not look at protocol: one Ariel source and one JDBC source.
    ariel = await make_log_source(
        db_session, inst, qradar_id=1, name="ariel", enabled=False,
        protocol_type_id=None,
    )
    jdbc = await make_log_source(
        db_session, inst, qradar_id=2, name="jdbc", enabled=False,
        protocol_type_id=101,
    )
    ariel_metric = await add_metric(
        db_session, ariel, bucket, collection_source="ariel_aggregate"
    )
    jdbc_metric = await add_metric(db_session, jdbc, bucket, collection_source="jdbc")

    reports = [
        await engine.evaluate_pending(ariel),
        await engine.evaluate_pending(jdbc),
        await engine.evaluate_interval(ariel, ariel_metric),
        await engine.evaluate_interval(jdbc, jdbc_metric),
    ]

    for report in reports:
        assert report.skipped_reason == SKIPPED_DISABLED_IN_QRADAR
        assert report.opened == [] and report.resolved == [] and report.states == {}
    await _assert_nothing_written(db_session, ariel, jdbc)


async def test_stale_stored_true_evaluates(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, enabled=True, monitoring_enabled=True)
    await _stale_inventory(db_session, inst)
    bucket = utc(h=10)
    await add_metric(db_session, ls, bucket, collection_source="mock")

    report = await engine.evaluate_pending(ls)

    assert report.skipped_reason is None
    watermark = await db_session.get(AnomalyEvaluationWatermark, ls.id)
    assert watermark is not None and watermark.evaluated_through == bucket
    stale = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == inst.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )
    assert stale is not None and stale.watermark_at == utc() - timedelta(hours=48)


async def test_stale_stored_false_skips(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, enabled=False, monitoring_enabled=True)
    await _stale_inventory(db_session, inst)
    bucket = utc(h=10)
    await add_metric(db_session, ls, bucket, collection_source="mock")

    report = await engine.evaluate_pending(ls)

    assert report.skipped_reason == SKIPPED_DISABLED_IN_QRADAR
    assert await db_session.get(AnomalyEvaluationWatermark, ls.id) is None
    await _assert_nothing_written(db_session, ls)


class TaskRig:
    """A real engine/maker on the test schema, with the task's globals pointed at it."""

    def __init__(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: self.maker)

    def base(self, **env: bool) -> None:
        settings = Settings(_env_file=None, **env)
        self.monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    async def row(self, key: str, enabled: bool) -> None:
        async with self.maker() as session:
            session.add(
                FeatureSwitch(
                    key=key, enabled=enabled, updated_at=utcnow(), updated_by="alice"
                )
            )
            await session.commit()


@pytest.fixture
async def rig(db_schema, monkeypatch: pytest.MonkeyPatch):
    task_rig = TaskRig(db_schema, monkeypatch)
    try:
        yield task_rig
    finally:
        await task_rig.engine.dispose()


@pytest.mark.parametrize("row", [None, False])
async def test_policy_off_preserves_existing_behavior(rig: TaskRig, row: bool | None) -> None:
    """Switch off: a stored-false source is still evaluated, as it is today."""
    rig.base(qradar_source_pause_enabled=False)
    if row is not None:
        await rig.row("qradar_source_pause_enabled", row)
    async with rig.maker() as session:
        inst = await make_instance(session)
        ls = await make_log_source(session, inst, enabled=False, monitoring_enabled=True)
        bucket = utc(h=10)
        await add_metric(session, ls, bucket, collection_source="mock")
        await session.commit()

    result = await tasks.evaluate_anomalies()

    assert result["sources"] == 1, result
    async with rig.maker() as session:
        watermark = await session.get(AnomalyEvaluationWatermark, ls.id)
        assert watermark is not None and watermark.evaluated_through == bucket


async def test_task_selection_uses_shared_predicate(rig: TaskRig) -> None:
    """The DB row turns the switch on over an env-off base: the query filters."""
    rig.base(qradar_source_pause_enabled=False)
    await rig.row("qradar_source_pause_enabled", True)
    async with rig.maker() as session:
        inst = await make_instance(session)
        disabled = await make_log_source(
            session, inst, qradar_id=1, name="disabled", enabled=False,
            monitoring_enabled=True,
        )
        enabled = await make_log_source(
            session, inst, qradar_id=2, name="enabled", enabled=True,
            monitoring_enabled=True,
        )
        bucket = utc(h=10)
        await add_metric(session, disabled, bucket, collection_source="mock")
        await add_metric(session, enabled, bucket, collection_source="mock")
        await session.commit()

    result = await tasks.evaluate_anomalies()

    assert result["sources"] == 1, result
    async with rig.maker() as session:
        judged = await session.get(AnomalyEvaluationWatermark, enabled.id)
        assert judged is not None and judged.evaluated_through == bucket
        assert await session.get(AnomalyEvaluationWatermark, disabled.id) is None
        await _assert_nothing_written(session, disabled)

    # An unreadable switch table still disables the whole task, as today.
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))
    unreadable = await tasks.evaluate_anomalies()
    assert unreadable == {
        "status": "disabled",
        "reason": UNREADABLE_REASON,
    }, unreadable
