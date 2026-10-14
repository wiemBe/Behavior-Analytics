"""The scheduled IPS pipeline: evaluate every collected bucket, then train.

The order is the point these tests pin: a spiked bucket is judged against the
cell as it stood before the run's training, the evaluation watermark advances
bucket by bucket and never beyond collection, and one source's failure leaves
the other sources evaluated with both of its watermarks untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from app.core.config import Settings, get_settings
from app.ips.engine import IpsAnomalyEngine
from app.models.enums import (
    AnomalyState,
    Criticality,
    IpsAnomalyType,
    IpsDimension,
    IpsProfileValidationStatus,
)
from app.models.ips import (
    IpsAnomaly,
    IpsDetectorState,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.services.ips_pipeline import (
    IpsInstancePipeline,
    collection_collector,
    evaluation_collector,
)
from tests.integration.factories import FakeClock, make_instance
from tests.integration.test_ips_baseline import add_bucket

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Monday 10:00, stepped by one day so every judged bucket shares one cell.
START = datetime(2026, 8, 3, 10, tzinfo=UTC)
DAY = timedelta(days=1)
PORT = IpsAnomalyType.IPS_PORT_RATE_SPIKE


def pipeline_settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        collection_interval_seconds=3600,
        baseline_min_samples=2,
        ips_baseline_lookback_days=28,
        anomaly_open_after_intervals=1,
        anomaly_resolve_after_intervals=2,
    )
    values.update(overrides)
    return Settings(**values)


async def make_source(session, instance, *, qradar_id: int):  # type: ignore[no-untyped-def]
    source = LogSource(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=f"ips-{qradar_id}",
        criticality=Criticality.MEDIUM,
    )
    session.add(source)
    await session.flush()
    profile = IpsSourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=True,
        profile_version=1,
        mapping={
            "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
            "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
            "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
            "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
        },
        action_value_map={},
        validation_status=IpsProfileValidationStatus.VALID,
        validated_profile_version=1,
        validation_report={},
    )
    session.add(profile)
    await session.flush()
    return source, profile


async def add_entity(session, instance, source) -> IpsEntity:
    entity = IpsEntity(
        instance_id=instance.id,
        log_source_id=source.id,
        dimension=IpsDimension.DESTINATION_PORT,
        value_key="443/tcp",
        display_name="443/tcp",
        first_seen_at=START - DAY,
        last_seen_at=START,
        active=True,
        normalizer_version=1,
    )
    session.add(entity)
    await session.flush()
    return entity


async def add_cell(session, entity) -> IpsEntityBaseline:
    cell = IpsEntityBaseline(
        ips_entity_id=entity.id,
        metric_name="entity_events",
        weekday=START.isoweekday(),
        hour=START.hour,
        median=100.0,
        mad=10.0,
        p05=80.0,
        p95=120.0,
        sample_count=4,
        is_reliable=True,
        completeness=1.0,
        observations=[100.0] * 4,
        baseline_version=1,
        metric_semantics="ips/entity_events/v1",
        profile_version=1,
        normalizer_version=1,
    )
    session.add(cell)
    await session.flush()
    return cell


async def add_metric(session, entity, at: datetime, events: int) -> None:
    session.add(
        IpsEntityMetric(
            ips_entity_id=entity.id, bucket_start=at, events=events, bucket_seconds=3600
        )
    )
    await session.flush()


async def seed_source(session, instance, *, qradar_id: int, spikes: list[int]):
    """A source with daily buckets whose entity spikes on the given offsets."""
    source, _profile = await make_source(session, instance, qradar_id=qradar_id)
    entity = await add_entity(session, instance, source)
    cell = await add_cell(session, entity)
    for offset in range(4):
        at = START + DAY * offset
        await add_bucket(session, source.id, at)
        await add_metric(session, entity, at, 600 if offset in spikes else 100)
    # Collected through the third bucket; the fourth exists but is uncollected.
    session.add(
        CollectionWatermark(
            instance_id=instance.id,
            collector=collection_collector(qradar_id),
            watermark_at=START + DAY * 2,
        )
    )
    await session.flush()
    return source, entity, cell


async def pipeline(session, instance, *, settings=None, engine_factory=None):
    return await IpsInstancePipeline(
        session,
        settings=settings or pipeline_settings(),
        clock=FakeClock(START + DAY * 4),
        engine_factory=engine_factory,
    ).run(instance)


async def evaluation_watermark(session, instance, qradar_id: int):
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == evaluation_collector(qradar_id),
        )
    )


async def test_a_spike_is_judged_before_the_run_trains_its_cell(db_session) -> None:
    instance = await make_instance(db_session)
    _source, _entity, cell = await seed_source(
        db_session, instance, qradar_id=412, spikes=[0, 1, 2]
    )
    report = await pipeline(db_session, instance)

    source_report = report.sources[0]
    assert source_report.status == "ok", source_report.error
    assert source_report.evaluated_buckets == [START, START + DAY, START + 2 * DAY]
    # Judged against the cell as it stood: the pre-run median. Had training run
    # first, the three spiked buckets would be the only samples in the lookback
    # window, the median would be 600, and no spike would be left to judge.
    incident = (
        await db_session.scalars(
            select(IpsAnomaly).where(IpsAnomaly.detector_type == PORT.value)
        )
    ).one()
    assert incident.state == AnomalyState.OPEN
    assert incident.expected_value == 100.0
    # Training ran afterwards, over buckets already judged: the whole incident
    # span is exclusion, so the stored cell still stands as it was built.
    await db_session.refresh(cell)
    assert cell.sample_count == 4
    assert cell.observations == [100.0, 100.0, 100.0, 100.0]
    assert source_report.baseline_entities_rebuilt == 1
    assert source_report.baseline_train_through == START + 2 * DAY + timedelta(hours=1)


async def test_the_evaluation_watermark_advances_only_over_evaluated_buckets(
    db_session
) -> None:
    instance = await make_instance(db_session)
    source, entity, _cell = await seed_source(
        db_session, instance, qradar_id=412, spikes=[0, 1]
    )
    bounded = pipeline_settings(ips_max_evaluation_buckets_per_run=2)

    first = await pipeline(db_session, instance, settings=bounded)
    assert first.sources[0].evaluated_buckets == [START, START + DAY]
    wm = await evaluation_watermark(db_session, instance, source.qradar_id)
    assert wm.watermark_at == START + DAY

    second = await pipeline(db_session, instance, settings=bounded)
    assert second.sources[0].evaluated_buckets == [START + 2 * DAY]
    third = await pipeline(db_session, instance, settings=bounded)
    assert third.sources[0].evaluated_buckets == []
    wm = await evaluation_watermark(db_session, instance, source.qradar_id)
    # Never past the collection watermark: the fourth bucket stays unjudged.
    assert wm.watermark_at == START + 2 * DAY
    assert wm.intervals_collected == 3
    state = await db_session.scalar(
        select(IpsDetectorState).where(
            IpsDetectorState.ips_entity_id == entity.id,
            IpsDetectorState.detector_type == PORT.value,
        )
    )
    assert state.last_interval_start == START + 2 * DAY
    folded = await db_session.scalars(
        select(IpsDetectorState.last_interval_start).where(
            IpsDetectorState.last_interval_start == START + 3 * DAY
        )
    )
    assert list(folded) == []


class _FailsForOneSource(IpsAnomalyEngine):
    """An engine that cannot evaluate one source, whatever the reason."""

    def __init__(self, session, *, settings, clock, fail_qradar_id: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(session, settings=settings, clock=clock)
        self._fail_qradar_id = fail_qradar_id

    async def evaluate_bucket(self, source: LogSource, bucket: IpsSourceBucket):  # type: ignore[no-untyped-def]
        if source.qradar_id == self._fail_qradar_id:
            raise RuntimeError("boom")
        return await super().evaluate_bucket(source, bucket)


async def test_one_source_failure_leaves_the_other_sources_evaluated(db_session) -> None:
    instance = await make_instance(db_session)
    failing, _e1, _c1 = await seed_source(db_session, instance, qradar_id=412, spikes=[0])
    healthy, entity, _c2 = await seed_source(db_session, instance, qradar_id=413, spikes=[0])

    def factory(session, *, settings, clock):  # type: ignore[no-untyped-def]
        return _FailsForOneSource(
            session, settings=settings, clock=clock, fail_qradar_id=failing.qradar_id
        )

    report = await pipeline(db_session, instance, engine_factory=factory)

    by_id = {str(item.log_source_id): item for item in report.sources}
    failed = by_id[str(failing.id)]
    assert failed.status == "failed"
    # The sanitized error keeps the class only, never the message.
    assert failed.error == "RuntimeError"

    failed_wm = await evaluation_watermark(db_session, instance, failing.qradar_id)
    assert failed_wm.watermark_at is None
    assert failed_wm.consecutive_failures == 1
    assert failed_wm.last_error == "RuntimeError"
    assert failed_wm.collection_metadata["last_error_class"] == "RuntimeError"

    # The failed source is skipped alone: it is not trained, the healthy one is.
    assert failed.baseline_train_through is None
    assert failed.baseline_skipped is not None

    healthy_report = by_id[str(healthy.id)]
    assert healthy_report.baseline_skipped is None
    assert healthy_report.baseline_train_through == START + 2 * DAY + timedelta(hours=1)
    assert healthy_report.status == "ok"
    assert healthy_report.evaluated_buckets == [START, START + DAY, START + 2 * DAY]
    healthy_wm = await evaluation_watermark(db_session, instance, healthy.qradar_id)
    assert healthy_wm.watermark_at == START + 2 * DAY
    assert healthy_wm.consecutive_failures == 0
    state = await db_session.scalar(
        select(IpsDetectorState).where(
            IpsDetectorState.ips_entity_id == entity.id,
            IpsDetectorState.detector_type == PORT.value,
        )
    )
    assert state.last_interval_start == START + 2 * DAY
    incidents = list(
        (
            await db_session.scalars(
                select(IpsAnomaly).where(IpsAnomaly.detector_type == PORT.value)
            )
        ).all()
    )
    assert [i.ips_entity_id for i in incidents] == [entity.id]


async def test_a_lagging_source_does_not_hold_back_another_sources_training(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await seed_source(db_session, instance, qradar_id=412, spikes=[])
    lagging, _entity, _cell = await seed_source(db_session, instance, qradar_id=413, spikes=[])
    await db_session.execute(
        update(CollectionWatermark)
        .where(CollectionWatermark.collector == collection_collector(lagging.qradar_id))
        .values(watermark_at=START)
    )

    report = await pipeline(db_session, instance)

    assert [len(item.evaluated_buckets) for item in report.sources] == [3, 1]
    # Each source trains through its own evaluated bucket end.
    assert [item.baseline_train_through for item in report.sources] == [
        START + 2 * DAY + timedelta(hours=1),
        START + timedelta(hours=1),
    ]


async def test_a_never_evaluated_source_is_skipped_alone(db_session) -> None:
    instance = await make_instance(db_session)
    await seed_source(db_session, instance, qradar_id=412, spikes=[])
    stalled, _entity, _cell = await seed_source(db_session, instance, qradar_id=413, spikes=[])
    await db_session.execute(
        delete(CollectionWatermark).where(
            CollectionWatermark.collector == collection_collector(stalled.qradar_id)
        )
    )

    report = await pipeline(db_session, instance)

    ok, skipped = report.sources
    assert ok.baseline_train_through is not None and ok.baseline_skipped is None
    assert skipped.baseline_train_through is None
    assert skipped.baseline_skipped == "no bucket has been evaluated"


async def test_training_one_instance_never_rebuilds_another_instances_cells(
    db_session,
) -> None:
    instance_a = await make_instance(db_session)
    instance_b = await make_instance(db_session)
    await seed_source(db_session, instance_a, qradar_id=412, spikes=[])
    _source_b, _entity_b, cell_b = await seed_source(
        db_session, instance_b, qradar_id=412, spikes=[]
    )
    before = (cell_b.computed_at, cell_b.sample_count)

    report = await pipeline(db_session, instance_a)

    assert report.sources[0].baseline_entities_rebuilt == 1
    await db_session.refresh(cell_b)
    assert (cell_b.computed_at, cell_b.sample_count) == before


async def test_a_source_that_failed_this_run_is_not_trained_despite_an_older_watermark(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    failing, _entity, _cell = await seed_source(db_session, instance, qradar_id=412, spikes=[])
    db_session.add(
        CollectionWatermark(
            instance_id=instance.id,
            collector=evaluation_collector(failing.qradar_id),
            watermark_at=START,
        )
    )
    await db_session.flush()

    def factory(session, *, settings, clock):  # type: ignore[no-untyped-def]
        return _FailsForOneSource(
            session, settings=settings, clock=clock, fail_qradar_id=failing.qradar_id
        )

    report = await pipeline(db_session, instance, engine_factory=factory)

    only = report.sources[0]
    assert only.status == "failed"
    assert only.baseline_train_through is None
    assert only.baseline_skipped == "evaluation failed this run"
