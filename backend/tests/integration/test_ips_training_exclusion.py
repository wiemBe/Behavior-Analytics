"""SOC-13g: IPS baseline training honours analyst exclusions.

The rebuild under test is `IpsBaselineBuilder`. `IpsInstancePipeline.run` is the
scheduled caller: it trains through `rebuild_due`, which loads both scopes in
one read. Entity incidents use `ips:entity`; source incidents use `ips:source`.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly import training_exclusions as te
from app.anomaly.seasonal import seasonal_key
from app.core.config import Settings
from app.history.origin import HistoryOrigin
from app.ips.baseline import IpsBaselineBuilder
from app.ips.behavior import detect_source_concentration_spike
from app.ips.detector import IpsDetectionContext, IpsThresholds, detect_port_rate_spike
from app.ips.engine import IpsAnomalyEngine
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_ENTITY_EVENTS_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
    IpsEntityFacts,
    IpsObservation,
    IpsSourceBucketFacts,
    observe_entity,
)
from app.models.enums import (
    AnomalyState,
    DetectorSignal,
    IpsAnomalyType,
    IpsDimension,
    Severity,
)
from app.models.identity import AuditLog
from app.models.ips import (
    IPS_NORMALIZER_VERSION,
    IpsAnomaly,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
)
from app.models.monitoring import CollectionWatermark
from app.security.rbac import Principal
from app.services.ips_pipeline import (
    IpsInstancePipeline,
    collection_collector,
    evaluation_collector,
)
from tests.integration.factories import FakeClock
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_ips_baseline import (
    START,
    WEEK,
    add_bucket,
    add_entity,
    add_source,
    settings,
)
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration]

HOUR = timedelta(hours=1)
STEP = timedelta(seconds=300)

ENTITY_DETECTORS = (
    IpsAnomalyType.IPS_PORT_RATE_SPIKE,
    IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE,
    IpsAnomalyType.IPS_SIGNATURE_BURST,
    IpsAnomalyType.IPS_NEW_DESTINATION_PORT,
)
SOURCE_DETECTORS = (
    IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
    IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
    IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
)

TRIAGER = Principal("ada", permissions=frozenset({"read:anomalies", "anomaly:triage"}))
READER = Principal("rita", permissions=frozenset({"read:*"}))


def cfg(**overrides: object) -> Settings:
    return settings(baseline_min_samples=1, **overrides)


async def plant_source(session, source_id, starts, *, outlier: datetime | None = None) -> None:  # type: ignore[no-untyped-def]
    for start in starts:
        if start == outlier:
            await add_bucket(
                session, source_id, start,
                top1=80, source_events=100, blocked=50, allowed=50, fanout=90,
            )
        else:
            await add_bucket(session, source_id, start)


async def plant_metrics(session, entity_id, starts, events) -> None:  # type: ignore[no-untyped-def]
    for start, count in zip(starts, events, strict=True):
        session.add(IpsEntityMetric(
            ips_entity_id=entity_id,
            bucket_start=start,
            events=count,
            bucket_seconds=3600,
        ))
    await session.flush()


async def exclude(session, *, kind, subject_id, scope, start, end):  # type: ignore[no-untyped-def]
    result = await te.create(
        session,
        origin_family="ips",
        origin_anomaly_id=uuid.uuid4(),
        subject_kind=kind,
        subject_id=subject_id,
        scope_key=scope,
        starts_at=start,
        ends_at=end,
        reason="analyst removed this window",
        created_by="ada",
        lookback_start=start - timedelta(days=30),
    )
    return result.row


def entity_incident(entity_id, at: datetime) -> IpsAnomaly:
    return IpsAnomaly(
        subject_kind="IPS_ENTITY",
        ips_entity_id=entity_id,
        detector_type=IpsAnomalyType.IPS_PORT_RATE_SPIKE,
        fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.HIGH,
        state=AnomalyState.RESOLVED,
        detected_at=at,
        resolved_at=at,
        profile_version=1,
        normalizer_version=1,
        details={},
    )


def source_incident(source_id, at: datetime) -> IpsAnomaly:
    return IpsAnomaly(
        subject_kind="IPS_SOURCE",
        log_source_id=source_id,
        detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.HIGH,
        state=AnomalyState.RESOLVED,
        detected_at=at,
        resolved_at=at,
        profile_version=1,
        normalizer_version=1,
        details={},
    )


async def entity_cell(session, entity_id, start):  # type: ignore[no-untyped-def]
    weekday, hour = seasonal_key(start)
    return await session.scalar(
        select(IpsEntityBaseline).where(
            IpsEntityBaseline.ips_entity_id == entity_id,
            IpsEntityBaseline.metric_name == IPS_ENTITY_EVENTS_METRIC,
            IpsEntityBaseline.weekday == weekday,
            IpsEntityBaseline.hour == hour,
        )
    )


async def source_cell(session, source_id, start, metric):  # type: ignore[no-untyped-def]
    weekday, hour = seasonal_key(start)
    return await session.scalar(
        select(IpsSourceBaseline).where(
            IpsSourceBaseline.log_source_id == source_id,
            IpsSourceBaseline.metric_name == metric,
            IpsSourceBaseline.weekday == weekday,
            IpsSourceBaseline.hour == hour,
        )
    )


async def rebuild_entities(session, entities, now, **overrides):  # type: ignore[no-untyped-def]
    builder = IpsBaselineBuilder(session, settings=cfg(**overrides))
    return await builder.rebuild_entities(list(entities), train_through=now, now=now)


async def rebuild_sources(session, profiles, now, **overrides):  # type: ignore[no-untyped-def]
    builder = IpsBaselineBuilder(session, settings=cfg(**overrides))
    return await builder.rebuild_sources(list(profiles), train_through=now, now=now)


def _mentions(statements: list[str], needle: str) -> int:
    return sum(1 for sql in statements if needle in sql.lower())


def _updates(statements: list[str], table: str) -> int:
    prefix = f"UPDATE {table.upper()}"
    return sum(
        1
        for sql in statements
        if " ".join(sql.split()).upper().startswith(prefix)
    )


def _anchor() -> datetime:
    """A recent hour: inside the default lookback, and aligned to 300s and to 3600s."""
    return (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _body(start: datetime, end: datetime, reason: str = "change window") -> dict:
    return {"starts_at": _iso(start), "ends_at": _iso(end), "reason": reason}


def _url(anomaly_id: uuid.UUID, family: str = "ips") -> str:
    return f"/api/v1/anomalies/{family}/{anomaly_id}/training-exclusions"


def _code(response) -> str:  # type: ignore[no-untyped-def]
    body = response.json()
    detail = body.get("detail")
    if isinstance(detail, dict) and "code" in detail:
        return str(detail["code"])
    return str(body["detail_code"])


async def _seed_ips(  # type: ignore[no-untyped-def]
    session,
    detector: IpsAnomalyType,
    *,
    state: AnomalyState = AnomalyState.RESOLVED,
    buckets: bool = True,
    detected: datetime | None = None,
):
    detected = detected or _anchor()
    resolved = detected if state is AnomalyState.RESOLVED else None
    instance, source, _profile = await add_source(session)
    entity_subject = detector in ENTITY_DETECTORS
    entity_id = None
    if entity_subject:
        entity = await add_entity(session, instance, source)
        entity_id = entity.id
    anomaly = IpsAnomaly(
        subject_kind="IPS_ENTITY" if entity_subject else "IPS_SOURCE",
        ips_entity_id=entity_id,
        log_source_id=None if entity_subject else source.id,
        detector_type=detector,
        fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.MEDIUM,
        state=state,
        detected_at=detected,
        resolved_at=resolved,
        profile_version=1,
        normalizer_version=1,
        details={},
    )
    session.add(anomaly)
    if buckets:
        await add_bucket(session, source.id, detected, bucket_seconds=300)
    await session.flush()
    subject_id = entity_id if entity_subject else source.id
    return anomaly.id, subject_id, source.id


def seed_ips(detector: IpsAnomalyType, **kw):  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        return await _seed_ips(session, detector, **kw)

    return run(work)


def _entity_facts(entity) -> IpsEntityFacts:  # type: ignore[no-untyped-def]
    return IpsEntityFacts(
        dimension=IpsDimension.DESTINATION_PORT,
        value_key=entity.value_key,
        display_name=entity.display_name,
        first_seen_at=entity.first_seen_at,
        active=True,
        last_seen_at=entity.last_seen_at,
        normalizer_version=IPS_NORMALIZER_VERSION,
    )


def _port_verdict(bucket, entity, cell, events: int):  # type: ignore[no-untyped-def]
    return detect_port_rate_spike(IpsDetectionContext(
        observation=observe_entity(
            _entity_facts(entity),
            IpsSourceBucketFacts.of(bucket),
            events,
            min_coverage=0.9,
        ),
        baseline=IpsAnomalyEngine._cell(cell),
        thresholds=IpsThresholds(baseline_min_samples=1),
    ))


def _concentration_verdict(bucket, cell, *, top1: int | None = None):  # type: ignore[no-untyped-def]
    facts = IpsSourceBucketFacts.of(bucket)
    if top1 is not None:
        facts = replace(facts, top1_events=top1)
    return detect_source_concentration_spike(IpsDetectionContext(
        observation=IpsObservation(bucket=facts),
        baseline=IpsAnomalyEngine._cell(cell),
        thresholds=IpsThresholds(baseline_min_samples=1),
    ))


# --- training ----------------------------------------------------------------------


async def test_excluded_interval_changes_the_entity_and_source_bands(db_session) -> None:
    """An entity exclusion leaves the source band, and a source exclusion the entity band."""
    _instance_a, source_a, profile_a = await add_source(db_session)
    entity_a = await add_entity(db_session, _instance_a, source_a, key="443/tcp")
    _instance_b, source_b, profile_b = await add_source(db_session)
    entity_b = await add_entity(db_session, _instance_b, source_b, key="443/tcp")
    starts = [START + i * WEEK for i in range(4)]
    events = [50, 52, 48, 900]
    for source, entity in ((source_a, entity_a), (source_b, entity_b)):
        await plant_source(db_session, source.id, starts, outlier=starts[-1])
        await plant_metrics(db_session, entity.id, starts, events)
    await exclude(
        db_session, kind="entity", subject_id=entity_a.id, scope=te.IPS_ENTITY_SCOPE,
        start=starts[-1], end=starts[-1] + HOUR,
    )
    await exclude(
        db_session, kind="source", subject_id=source_b.id, scope=te.IPS_SOURCE_SCOPE,
        start=starts[-1], end=starts[-1] + HOUR,
    )
    now = starts[-1] + HOUR
    await rebuild_entities(db_session, (entity_a, entity_b), now)
    await rebuild_sources(db_session, (profile_a, profile_b), now)

    only_entity = await entity_cell(db_session, entity_a.id, starts[0])
    with_source = await entity_cell(db_session, entity_b.id, starts[0])
    assert 900.0 not in only_entity.observations
    assert only_entity.sample_count == 3
    assert only_entity.exclusion_counts["analyst_exclusion"] == 1
    assert only_entity.excluded_sample_count == sum(only_entity.exclusion_counts.values())
    assert 900.0 in with_source.observations
    assert "analyst_exclusion" not in with_source.exclusion_counts
    assert only_entity.p95 < with_source.p95

    untouched_share = await source_cell(
        db_session, source_a.id, starts[0], IPS_TOP_SOURCE_SHARE_METRIC
    )
    dropped_share = await source_cell(
        db_session, source_b.id, starts[0], IPS_TOP_SOURCE_SHARE_METRIC
    )
    dropped_block = await source_cell(db_session, source_b.id, starts[0], IPS_BLOCK_RATIO_METRIC)
    dropped_fanout = await source_cell(db_session, source_b.id, starts[0], IPS_FANOUT_METRIC)
    untouched_fanout = await source_cell(db_session, source_a.id, starts[0], IPS_FANOUT_METRIC)
    assert 0.8 in untouched_share.observations
    assert "analyst_exclusion" not in untouched_share.exclusion_counts
    assert 0.8 not in dropped_share.observations
    assert 0.5 not in dropped_block.observations
    assert 90.0 not in dropped_fanout.observations
    assert 90.0 in untouched_fanout.observations
    for cell in (dropped_share, dropped_block, dropped_fanout):
        assert cell.exclusion_counts["analyst_exclusion"] == 1
        assert cell.sample_count == 3


async def test_revoked_exclusion_trains_as_if_it_had_never_been_there(db_session) -> None:
    instance, source, profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    starts = [START + i * WEEK for i in range(4)]
    await plant_source(db_session, source.id, starts, outlier=starts[-1])
    await plant_metrics(db_session, entity.id, starts, [50, 52, 48, 900])
    entity_row = await exclude(
        db_session, kind="entity", subject_id=entity.id, scope=te.IPS_ENTITY_SCOPE,
        start=starts[-1], end=starts[-1] + HOUR,
    )
    source_row = await exclude(
        db_session, kind="source", subject_id=source.id, scope=te.IPS_SOURCE_SCOPE,
        start=starts[-1], end=starts[-1] + HOUR,
    )
    now = starts[-1] + HOUR
    await rebuild_entities(db_session, (entity,), now)
    await rebuild_sources(db_session, (profile,), now)
    assert 900.0 not in (await entity_cell(db_session, entity.id, starts[0])).observations
    assert 0.8 not in (
        await source_cell(db_session, source.id, starts[0], IPS_TOP_SOURCE_SHARE_METRIC)
    ).observations

    await te.revoke(db_session, entity_row.id, revoked_by="ada", reason="put the bucket back")
    await te.revoke(db_session, source_row.id, revoked_by="ada", reason="put the bucket back")
    await rebuild_entities(db_session, (entity,), now)
    await rebuild_sources(db_session, (profile,), now)
    restored_entity = await entity_cell(db_session, entity.id, starts[0])
    restored_share = await source_cell(
        db_session, source.id, starts[0], IPS_TOP_SOURCE_SHARE_METRIC
    )
    assert 900.0 in restored_entity.observations
    assert 0.8 in restored_share.observations
    assert "analyst_exclusion" not in restored_entity.exclusion_counts
    assert "analyst_exclusion" not in restored_share.exclusion_counts


async def test_a_fully_excluded_cell_is_insufficient_for_the_detector(db_session) -> None:
    instance, source, profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    dropped = [START + i * WEEK for i in range(3)]
    kept = [START.replace(hour=11) + i * WEEK for i in range(3)]
    starts = sorted(dropped + kept)
    await plant_source(db_session, source.id, starts)
    await plant_metrics(db_session, entity.id, starts, [40, 42, 38, 40, 42, 38])
    now = kept[-1] + HOUR
    await rebuild_entities(db_session, (entity,), now)
    await rebuild_sources(db_session, (profile,), now)
    before = await entity_cell(db_session, entity.id, dropped[0])
    kept_before = await entity_cell(db_session, entity.id, kept[0])
    share_before = await source_cell(db_session, source.id, dropped[0], IPS_TOP_SOURCE_SHARE_METRIC)
    share_kept = await source_cell(db_session, source.id, kept[0], IPS_TOP_SOURCE_SHARE_METRIC)
    assert before.is_reliable and kept_before.is_reliable
    assert share_before.is_reliable and share_kept.is_reliable
    median_before = before.median
    version_before = before.baseline_version
    counts_before = dict(before.exclusion_counts)
    kept_version = kept_before.baseline_version
    kept_observations = list(kept_before.observations)
    share_version = share_kept.baseline_version
    share_observations = list(share_kept.observations)
    share_counts = dict(share_before.exclusion_counts)

    for start in dropped:
        await exclude(
            db_session, kind="entity", subject_id=entity.id, scope=te.IPS_ENTITY_SCOPE,
            start=start, end=start + HOUR,
        )
        await exclude(
            db_session, kind="source", subject_id=source.id, scope=te.IPS_SOURCE_SCOPE,
            start=start, end=start + HOUR,
        )
    # A point span per bucket. One span across the hour would also cover later
    # buckets of that hour, and those would no longer be analyst exclusions.
    for start in kept:
        db_session.add(entity_incident(entity.id, start))
        db_session.add(source_incident(source.id, start))
    await db_session.flush()
    await rebuild_entities(db_session, (entity,), now)
    await rebuild_sources(db_session, (profile,), now)

    emptied = await entity_cell(db_session, entity.id, dropped[0])
    untouched = await entity_cell(db_session, entity.id, kept[0])
    assert emptied.is_reliable is False
    assert emptied.sample_count == 0
    assert emptied.observations == []
    assert emptied.p05 is None and emptied.p95 is None
    assert emptied.completeness == 0
    assert emptied.baseline_version == version_before + 1
    assert emptied.median == median_before
    assert emptied.exclusion_counts == counts_before
    assert emptied.normalizer_version == IPS_NORMALIZER_VERSION
    dropped_bucket = await db_session.get(IpsSourceBucket, (source.id, dropped[-1]))
    kept_bucket = await db_session.get(IpsSourceBucket, (source.id, kept[0]))
    unknown = _port_verdict(dropped_bucket, entity, emptied, 900)
    assert unknown.signal is DetectorSignal.UNKNOWN
    assert unknown.evidence.insufficient_data is True
    assert unknown.evidence.expected_value is None
    assert untouched.is_reliable is True
    assert untouched.baseline_version == kept_version
    assert untouched.observations == kept_observations
    healthy = _port_verdict(kept_bucket, entity, untouched, 15)
    assert healthy.signal is DetectorSignal.HEALTHY
    assert healthy.evidence.insufficient_data is False

    emptied_share = await source_cell(
        db_session, source.id, dropped[0], IPS_TOP_SOURCE_SHARE_METRIC
    )
    kept_share = await source_cell(db_session, source.id, kept[0], IPS_TOP_SOURCE_SHARE_METRIC)
    assert emptied_share.is_reliable is False
    assert emptied_share.sample_count == 0
    assert emptied_share.observations == []
    assert emptied_share.p05 is None and emptied_share.p95 is None
    assert emptied_share.numerator_sum is None
    assert emptied_share.denominator_sum is None
    assert emptied_share.exclusion_counts == share_counts
    unknown_share = _concentration_verdict(dropped_bucket, emptied_share, top1=70)
    assert unknown_share.signal is DetectorSignal.UNKNOWN
    assert unknown_share.evidence.insufficient_data is True
    assert unknown_share.evidence.expected_value is None
    # The concentration incident covers only that metric. The kept cell stays
    # as the first rebuild wrote it. Fan-out of the same hour still trains.
    assert kept_share.is_reliable is True
    assert kept_share.baseline_version == share_version
    assert kept_share.observations == share_observations
    healthy_share = _concentration_verdict(kept_bucket, kept_share)
    assert healthy_share.signal is DetectorSignal.HEALTHY
    assert healthy_share.evidence.insufficient_data is False
    for metric in (IPS_FANOUT_METRIC, IPS_BLOCK_RATIO_METRIC):
        emptied_metric = await source_cell(db_session, source.id, dropped[0], metric)
        kept_metric = await source_cell(db_session, source.id, kept[0], metric)
        assert emptied_metric.is_reliable is False
        assert emptied_metric.sample_count == 0
        assert emptied_metric.numerator_sum is None
        assert kept_metric.is_reliable is True
        assert kept_metric.sample_count == 3


async def test_automatic_and_analyst_overlap_is_counted_once(db_session) -> None:
    instance, source, profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    starts = [START + i * WEEK for i in range(6)]
    await plant_source(db_session, source.id, starts)
    await plant_metrics(db_session, entity.id, starts, [50, 51, 49, 52, 48, 50])
    db_session.add(entity_incident(entity.id, starts[0]))
    db_session.add(source_incident(source.id, starts[0]))
    await db_session.flush()
    for start in (starts[0], starts[1]):
        await exclude(
            db_session, kind="entity", subject_id=entity.id, scope=te.IPS_ENTITY_SCOPE,
            start=start, end=start + HOUR,
        )
        await exclude(
            db_session, kind="source", subject_id=source.id, scope=te.IPS_SOURCE_SCOPE,
            start=start, end=start + HOUR,
        )
    now = starts[-1] + HOUR
    await rebuild_entities(db_session, (entity,), now)
    await rebuild_sources(db_session, (profile,), now)

    cell = await entity_cell(db_session, entity.id, starts[0])
    assert cell.exclusion_counts["known_anomaly"] == 1
    assert cell.exclusion_counts["analyst_exclusion"] == 1
    assert cell.sample_count == 4
    assert cell.excluded_sample_count == sum(cell.exclusion_counts.values())
    share = await source_cell(db_session, source.id, starts[0], IPS_TOP_SOURCE_SHARE_METRIC)
    assert share.exclusion_counts["known_anomaly"] == 1
    assert share.exclusion_counts["analyst_exclusion"] == 1
    assert share.sample_count == 4
    # The concentration incident does not cover fan-out or block ratio, so both
    # analyst buckets are analyst exclusions and known_anomaly stays zero.
    for metric in (IPS_FANOUT_METRIC, IPS_BLOCK_RATIO_METRIC):
        other = await source_cell(db_session, source.id, starts[0], metric)
        assert other.exclusion_counts["known_anomaly"] == 0
        assert other.exclusion_counts["analyst_exclusion"] == 2
        assert other.sample_count == 4


async def test_rebuild_reads_exclusions_once_and_invalidates_once(db_session) -> None:
    instance, source, profile = await add_source(db_session)
    entities = [
        await add_entity(db_session, instance, source, key=f"{port}/tcp")
        for port in range(2000, 2004)
    ]
    starts = [START, START + WEEK]
    await plant_source(db_session, source.id, starts)
    for entity in entities:
        await plant_metrics(db_session, entity.id, starts, [40, 41])
    now = starts[-1] + HOUR
    batch = {"ips_baseline_rebuild_batch_size": 1}

    with recorded_statements(db_session) as empty_calls:
        assert await rebuild_entities(db_session, [], now) == {}
        assert await rebuild_sources(db_session, [], now) == {}
    assert _mentions(empty_calls, "baseline_training_exclusion") == 0

    _quiet_instance, quiet_source, quiet_profile = await add_source(db_session)
    quiet_profile.enabled = False
    await db_session.flush()
    with recorded_statements(db_session) as no_subjects:
        await IpsBaselineBuilder(db_session, settings=cfg()).rebuild_due(
            train_through=now, now=now, log_source_id=quiet_source.id,
        )
    assert _mentions(no_subjects, "baseline_training_exclusion") == 0

    with recorded_statements(db_session) as first:
        await rebuild_entities(db_session, entities, now, **batch)
    assert _mentions(first, "baseline_training_exclusion") == 1
    assert _updates(first, "ips_entity_baseline") == 0

    for entity in entities:
        for start in starts:
            await exclude(
                db_session, kind="entity", subject_id=entity.id, scope=te.IPS_ENTITY_SCOPE,
                start=start, end=start + HOUR,
            )
    with recorded_statements(db_session) as one_entity:
        await rebuild_entities(db_session, entities[:1], now, **batch)
    assert _mentions(one_entity, "baseline_training_exclusion") == 1
    assert _updates(one_entity, "ips_entity_baseline") == 1

    with recorded_statements(db_session) as due:
        rebuilt = await IpsBaselineBuilder(db_session, settings=cfg(**batch)).rebuild_due(
            train_through=now, now=now, limit=50, log_source_id=source.id,
        )
    assert _mentions(due, "baseline_training_exclusion") == 1
    assert _updates(due, "ips_entity_baseline") == 1
    assert _updates(due, "ips_source_baseline") == 0
    assert len(rebuilt["entities"]) == len(entities)
    assert len(rebuilt["sources"]) == 1
    for entity in entities:
        cell = await entity_cell(db_session, entity.id, starts[0])
        assert cell.is_reliable is False
        assert cell.sample_count == 0
    share = await source_cell(db_session, source.id, starts[0], IPS_TOP_SOURCE_SHARE_METRIC)
    assert share.is_reliable is True

    _other_instance, other_source, other_profile = await add_source(db_session)
    await plant_source(db_session, other_source.id, [START])
    with recorded_statements(db_session) as sources_only:
        await rebuild_sources(db_session, (profile, other_profile), now, **batch)
    assert _mentions(sources_only, "baseline_training_exclusion") == 1
    assert _updates(sources_only, "ips_source_baseline") == 0


async def test_scheduled_run_trains_with_the_exclusion(db_session) -> None:
    """`IpsInstancePipeline.run` trains when collection and evaluation are caught up.

    No provider is constructed. Evaluation finds no newer bucket, and `_train`
    still rebuilds through that bucket's end.
    """
    instance, source, _profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    starts = [START + i * WEEK for i in range(4)]
    last = starts[-1]
    await plant_source(db_session, source.id, starts, outlier=last)
    await plant_metrics(db_session, entity.id, starts, [40, 42, 38, 900])
    for collector in (
        collection_collector(source.qradar_id),
        evaluation_collector(source.qradar_id),
    ):
        db_session.add(CollectionWatermark(
            instance_id=instance.id, collector=collector, watermark_at=last,
        ))
    await db_session.flush()
    pipeline = IpsInstancePipeline(
        db_session, settings=cfg(), clock=FakeClock(last + timedelta(hours=2)),
    )

    first = await pipeline.run(instance)
    assert len(first.sources) == 1
    report = first.sources[0]
    assert report.status == "ok", report.error
    assert report.evaluated_buckets == []
    assert report.baseline_entities_rebuilt == 1
    assert report.baseline_sources_rebuilt == 1
    trained = await entity_cell(db_session, entity.id, starts[0])
    assert 900.0 in trained.observations

    await exclude(
        db_session, kind="entity", subject_id=entity.id, scope=te.IPS_ENTITY_SCOPE,
        start=last, end=last + HOUR,
    )
    second = await pipeline.run(instance)
    assert second.sources[0].status == "ok", second.sources[0].error
    retrained = await entity_cell(db_session, entity.id, starts[0])
    assert 900.0 not in retrained.observations
    assert retrained.exclusion_counts["analyst_exclusion"] == 1


# --- API ---------------------------------------------------------------------------


@pytest.mark.parametrize("detector", ENTITY_DETECTORS)
def test_api_accepts_a_resolved_entity_anomaly(db_schema, detector: IpsAnomalyType) -> None:
    anomaly_id, entity_id, _source_id = seed_ips(detector)
    start = _anchor()
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin_family"] == "ips"
    assert body["subject_kind"] == "entity"
    assert body["subject_id"] == str(entity_id)
    assert body["scope_key"] == "ips:entity"
    assert body["state"] == "PENDING_REBUILD"
    assert body["created_by"] == "ada"
    again = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


@pytest.mark.parametrize("detector", SOURCE_DETECTORS)
def test_api_accepts_a_resolved_source_anomaly(db_schema, detector: IpsAnomalyType) -> None:
    anomaly_id, source_id, _same = seed_ips(detector)
    start = _anchor()
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin_family"] == "ips"
    assert body["subject_kind"] == "source"
    assert body["subject_id"] == str(source_id)
    assert body["scope_key"] == "ips:source"
    assert body["state"] == "PENDING_REBUILD"


def test_ips_guards_match_phase_a(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    open_id, _, _ = seed_ips(IpsAnomalyType.IPS_PORT_RATE_SPIKE, state=AnomalyState.OPEN)
    assert _code(Client(TRIAGER).post(_url(open_id), json=_body(start, start + STEP))) == (
        "baseline_exclusion.anomaly_active"
    )
    missing = Client(TRIAGER).post(_url(uuid.uuid4()), json=_body(start, start + STEP))
    assert missing.status_code == 404
    assert _code(missing) == "baseline_exclusion.not_found"

    no_entity, _, _ = seed_ips(IpsAnomalyType.IPS_PORT_RATE_SPIKE, buckets=False)
    no_source, _, _ = seed_ips(IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT, buckets=False)
    for anomaly_id in (no_entity, no_source):
        assert _code(Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))) == (
            "baseline_exclusion.interval_invalid"
        )

    anomaly_id, _, _ = seed_ips(IpsAnomalyType.IPS_PORT_RATE_SPIKE)
    refused = Client(READER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert refused.status_code == 403
    blank = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP, reason="  "))
    assert _code(blank) == "baseline_exclusion.reason_invalid"
    # Family is refused before the reason is read, so a blank reason cannot hide it.
    # SOC-13h accepts `property_watch` and `rule`, so the probe is not a family.
    early = Client(TRIAGER).post(
        _url(uuid.uuid4(), "nonsense"), json=_body(start, start + STEP, reason="  ")
    )
    assert early.status_code == 422
    assert _code(early) == "baseline_exclusion.family_unsupported"

    narrowed = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start + STEP, start + 2 * STEP)
    )
    assert _code(narrowed) == "baseline_exclusion.interval_not_widened"
    too_long = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start, start + timedelta(days=7) + STEP)
    )
    assert _code(too_long) == "baseline_exclusion.interval_too_long"

    created = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert created.status_code == 201
    overlap = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + 2 * STEP))
    assert overlap.status_code == 409
    assert _code(overlap) == "baseline_exclusion.overlap"

    audits = run(_audits)
    assert audits
    assert all("change window" not in str(row.detail) for row in audits)
    assert any(row.detail.get("scope_key") == "ips:entity" for row in audits)


async def _audits(session):  # type: ignore[no-untyped-def]
    return list((await session.scalars(
        select(AuditLog).where(AuditLog.action == "baseline_exclusion.create")
    )).all())


# SOC-13h accepts `property_watch` and `rule`; their family file pins that.
@pytest.mark.parametrize("family", ["nonsense"])
def test_other_families_stay_unsupported(db_schema, family: str) -> None:
    start = _anchor()
    response = Client(TRIAGER).post(_url(uuid.uuid4(), family), json=_body(start, start + STEP))
    assert response.status_code == 422
    assert _code(response) == "baseline_exclusion.family_unsupported"


@pytest.mark.parametrize(
    "detector",
    [IpsAnomalyType.IPS_PORT_RATE_SPIKE, IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE],
)
def test_bucket_width_comes_from_the_live_row(db_schema, detector: IpsAnomalyType) -> None:
    detected = _anchor()
    history_only, _subject, source_id = seed_ips(detector, buckets=False, detected=detected)

    async def history(session):  # type: ignore[no-untyped-def]
        await add_bucket(
            session, source_id, detected,
            bucket_seconds=3600, origin=HistoryOrigin.HISTORY.value,
        )

    run(history)
    # Hour + 300s is a 300-second boundary and not a 3600-second one.
    asked = _body(detected, detected + STEP)
    assert _code(Client(TRIAGER).post(_url(history_only), json=asked)) == (
        "baseline_exclusion.interval_invalid"
    )

    anomaly_id, _subject, source_id = seed_ips(detector, buckets=False, detected=detected)

    async def both(session):  # type: ignore[no-untyped-def]
        await add_bucket(
            session, source_id, detected - STEP,
            bucket_seconds=300, origin=HistoryOrigin.LIVE.value,
        )
        await add_bucket(
            session, source_id, detected,
            bucket_seconds=3600, origin=HistoryOrigin.HISTORY.value,
        )

    run(both)
    accepted = Client(TRIAGER).post(_url(anomaly_id), json=asked)
    assert accepted.status_code == 201, accepted.text


def test_applied_follows_the_matching_baseline_computed_at(db_schema) -> None:  # type: ignore[no-untyped-def]
    entity_anomaly, entity_id, _entity_source = seed_ips(IpsAnomalyType.IPS_PORT_RATE_SPIKE)
    source_anomaly, source_id, _source = seed_ips(
        IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE
    )
    start = _anchor()
    entity_created = Client(TRIAGER).post(
        _url(entity_anomaly), json=_body(start, start + STEP)
    )
    source_created = Client(TRIAGER).post(
        _url(source_anomaly), json=_body(start, start + STEP)
    )
    assert entity_created.status_code == 201, entity_created.text
    assert source_created.status_code == 201, source_created.text
    entity_exclusion = entity_created.json()["id"]
    source_exclusion = source_created.json()["id"]
    entity_at = datetime.fromisoformat(entity_created.json()["created_at"])
    source_at = datetime.fromisoformat(source_created.json()["created_at"])

    run(lambda session: _stamp_entity(session, entity_id, entity_at - timedelta(hours=1)))
    assert [item["id"] for item in _listed("entity", entity_id, "PENDING_REBUILD")] == [
        entity_exclusion
    ]
    assert _listed("entity", entity_id, "APPLIED") == []

    run(lambda session: _stamp_entity(session, entity_id, entity_at + timedelta(minutes=1)))
    applied = _listed("entity", entity_id, "APPLIED")
    assert [item["id"] for item in applied] == [entity_exclusion]
    assert applied[0]["state"] == "APPLIED"
    assert _listed("entity", entity_id, "PENDING_REBUILD") == []
    # An entity cell must not mark a source exclusion applied.
    assert _listed("source", source_id, "APPLIED") == []
    assert [item["id"] for item in _listed("source", source_id, "PENDING_REBUILD")] == [
        source_exclusion
    ]

    run(lambda session: _stamp_source(session, source_id, source_at + timedelta(minutes=1)))
    source_applied = _listed("source", source_id, "APPLIED")
    assert [item["id"] for item in source_applied] == [source_exclusion]
    assert source_applied[0]["state"] == "APPLIED"
    assert _listed("source", source_id, "PENDING_REBUILD") == []

    revoked = Client(TRIAGER).post(
        f"/api/v1/behavior/training-exclusions/{entity_exclusion}/revoke",
        json={"reason": "no longer wanted"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "REVOKED"
    assert _listed("entity", entity_id, "APPLIED") == []
    assert [item["id"] for item in _listed("source", source_id, "APPLIED")] == [source_exclusion]


async def _stamp_entity(session, entity_id, at: datetime) -> None:  # type: ignore[no-untyped-def]
    cell = await session.scalar(
        select(IpsEntityBaseline).where(IpsEntityBaseline.ips_entity_id == entity_id)
    )
    if cell is None:
        session.add(IpsEntityBaseline(
            ips_entity_id=entity_id,
            metric_name=IPS_ENTITY_EVENTS_METRIC,
            weekday=1,
            hour=10,
            median=40.0,
            profile_version=1,
            normalizer_version=IPS_NORMALIZER_VERSION,
            computed_at=at,
        ))
    else:
        cell.computed_at = at


async def _stamp_source(session, source_id, at: datetime) -> None:  # type: ignore[no-untyped-def]
    cell = await session.scalar(
        select(IpsSourceBaseline).where(IpsSourceBaseline.log_source_id == source_id)
    )
    if cell is None:
        session.add(IpsSourceBaseline(
            log_source_id=source_id,
            metric_name=IPS_TOP_SOURCE_SHARE_METRIC,
            weekday=1,
            hour=10,
            median=0.2,
            profile_version=1,
            normalizer_version=IPS_NORMALIZER_VERSION,
            computed_at=at,
        ))
    else:
        cell.computed_at = at


def _listed(kind: str, subject_id, state: str) -> list[dict]:
    response = Client(TRIAGER).get(
        "/api/v1/behavior/training-exclusions",
        params={"subject_kind": kind, "subject_id": str(subject_id), "state": state},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]
