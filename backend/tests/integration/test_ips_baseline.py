"""IPS baseline persistence, version isolation, pooled sums and SQL bounds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.ips.baseline import IpsBaselineBuilder
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_ENTITY_EVENTS_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
)
from app.models.enums import IpsDimension, IpsProfileValidationStatus
from app.models.ips import (
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
    IpsSourceProfile,
)
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

START = datetime(2026, 8, 3, 10, tzinfo=UTC)
WEEK = timedelta(days=7)


def settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        collection_interval_seconds=3600,
        ips_baseline_lookback_days=60,
        ips_baseline_rebuild_batch_size=50,
        ips_min_coverage=0.9,
        baseline_min_samples=2,
    )
    values.update(overrides)
    return Settings(**values)


async def add_source(session, *, action_mapping: bool = True):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    source = await make_log_source(session, instance, qradar_id=412, name="ips-412")
    mapping = {
        "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
        "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
        "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
        "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
        "ACTION": (
            {"kind": "CUSTOM", "name": "Firewall Action"} if action_mapping else None
        ),
    }
    profile = IpsSourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=True,
        profile_version=1,
        mapping=mapping,
        action_value_map={"BLOCKED": ["deny"], "ALLOWED": ["allow"]}
        if action_mapping
        else {},
        validation_status=IpsProfileValidationStatus.VALID,
        validated_profile_version=1,
        validation_report={},
    )
    session.add(profile)
    await session.flush()
    return instance, source, profile


async def add_bucket(
    session,
    source_id,
    at: datetime,
    *,
    profile_version: int = 1,
    top1: int = 20,
    source_events: int = 100,
    blocked: int = 10,
    allowed: int = 90,
    unmapped: int = 0,
    fanout: int = 20,
    **overrides: object,
) -> IpsSourceBucket:
    values: dict[str, object] = {
        "log_source_id": source_id,
        "bucket_start": at,
        "bucket_seconds": 3600,
        "total_events": 100,
        "total_records": 100,
        "destination_port_measurement": "EXACT",
        "destination_port_coverage_ratio": 1.0,
        "destination_port_coverage_lower_bound": False,
        "destination_ip_measurement": "EXACT",
        "destination_ip_coverage_ratio": 1.0,
        "destination_ip_coverage_lower_bound": False,
        "signature_measurement": "EXACT",
        "signature_coverage_ratio": 1.0,
        "signature_coverage_lower_bound": False,
        "source_ip_measurement": "EXACT",
        "source_ip_coverage_ratio": 1.0,
        "source_ip_coverage_lower_bound": False,
        "port_fanout_measurement": "EXACT",
        "action_measurement": "EXACT",
        "action_coverage_ratio": 1.0,
        "action_coverage_lower_bound": False,
        "source_events": source_events,
        "distinct_sources": 4,
        "distinct_sources_lower_bound": False,
        "top1_events": top1,
        "top5_events": source_events,
        "max_distinct_ports": fanout,
        "blocked_events": blocked,
        "allowed_events": allowed,
        "unmapped_events": unmapped,
        "contributors": {},
        "admitted": {},
        "below_admission_threshold": {},
        "rejected_over_quota": {},
        "untracked_entities": {},
        "untracked_events": {},
        "admission_truncated": {
            "DESTINATION_PORT": False,
            "DESTINATION_IP": False,
            "SIGNATURE": False,
        },
        "profile_version": profile_version,
        "normalizer_version": 1,
        "query_provenance": {},
        "in_maintenance": False,
        "collected_at": at + timedelta(hours=1),
    }
    values.setdefault("origin", "LIVE")
    values.update(overrides)
    result = IpsSourceBucket(**values)  # type: ignore[arg-type]
    session.add(result)
    await session.flush()
    return result


async def add_entity(session, instance, source, key: str = "443/tcp") -> IpsEntity:
    result = IpsEntity(
        instance_id=instance.id,
        log_source_id=source.id,
        dimension=IpsDimension.DESTINATION_PORT,
        value_key=key,
        display_name=key,
        first_seen_at=START - timedelta(hours=1),
        last_seen_at=START,
        active=True,
        normalizer_version=1,
    )
    session.add(result)
    await session.flush()
    return result


async def test_builder_persists_sparse_zero_and_hand_computed_pooled_sums(db_session) -> None:
    instance, source, profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    await add_bucket(db_session, source.id, START, top1=20, source_events=100)
    await add_bucket(
        db_session,
        source.id,
        START + WEEK,
        top1=60,
        source_events=200,
        blocked=60,
        allowed=140,
        fanout=40,
    )
    db_session.add(
        IpsEntityMetric(
            ips_entity_id=entity.id,
            bucket_start=START,
            events=12,
            bucket_seconds=3600,
        )
    )
    await db_session.flush()
    builder = IpsBaselineBuilder(db_session, settings=settings())
    train_through = START + WEEK + timedelta(hours=1)

    assert await builder.rebuild_entities([entity], train_through=train_through) == {
        entity.id: 1
    }
    assert await builder.rebuild_sources([profile], train_through=train_through) == {
        source.id: 3
    }

    entity_cell = await db_session.scalar(
        select(IpsEntityBaseline).where(
            IpsEntityBaseline.ips_entity_id == entity.id,
            IpsEntityBaseline.metric_name == IPS_ENTITY_EVENTS_METRIC,
        )
    )
    assert entity_cell.observations == [12.0, 0.0]
    assert entity_cell.sample_count == 2
    assert entity_cell.is_reliable is True

    cells = {
        cell.metric_name: cell
        for cell in (
            await db_session.scalars(
                select(IpsSourceBaseline).where(IpsSourceBaseline.log_source_id == source.id)
            )
        ).all()
    }
    concentration = cells[IPS_TOP_SOURCE_SHARE_METRIC]
    assert concentration.observations == [0.2, 0.3]
    assert concentration.numerator_sum == 80
    assert concentration.denominator_sum == 300
    block = cells[IPS_BLOCK_RATIO_METRIC]
    assert block.observations == [0.1, 0.3]
    assert block.numerator_sum == 70
    assert block.denominator_sum == 300
    assert cells[IPS_FANOUT_METRIC].observations == [20.0, 40.0]


async def test_profile_version_bump_relearns_without_mixing_old_buckets(db_session) -> None:
    _instance, source, profile = await add_source(db_session)
    await add_bucket(db_session, source.id, START, top1=10)
    await add_bucket(db_session, source.id, START + WEEK, top1=20)
    builder = IpsBaselineBuilder(db_session, settings=settings(baseline_min_samples=1))
    await builder.rebuild_sources(
        [profile], train_through=START + WEEK + timedelta(hours=1)
    )

    profile.profile_version = 2
    profile.validated_profile_version = 2
    await add_bucket(db_session, source.id, START + 2 * WEEK, profile_version=2, top1=90)
    await builder.rebuild_sources(
        [profile], train_through=START + 2 * WEEK + timedelta(hours=1)
    )

    cell = await db_session.scalar(
        select(IpsSourceBaseline).where(
            IpsSourceBaseline.log_source_id == source.id,
            IpsSourceBaseline.metric_name == IPS_TOP_SOURCE_SHARE_METRIC,
        )
    )
    assert cell.profile_version == 2
    assert cell.observations == [0.9]
    assert cell.sample_count == 1
    assert cell.exclusion_counts["version_mismatch"] == 2


async def test_no_action_mapping_builds_no_block_ratio_cell(db_session) -> None:
    _instance, source, profile = await add_source(db_session, action_mapping=False)
    await add_bucket(db_session, source.id, START)
    await IpsBaselineBuilder(
        db_session, settings=settings(baseline_min_samples=1)
    ).rebuild_sources([profile], train_through=START + timedelta(hours=1))
    metrics = set(
        (
            await db_session.scalars(
                select(IpsSourceBaseline.metric_name).where(
                    IpsSourceBaseline.log_source_id == source.id
                )
            )
        ).all()
    )
    assert IPS_BLOCK_RATIO_METRIC not in metrics
    assert metrics == {IPS_TOP_SOURCE_SHARE_METRIC, IPS_FANOUT_METRIC}


async def test_entity_rebuild_statement_count_grows_only_with_chunks(db_session) -> None:
    instance, source, _profile = await add_source(db_session)
    await add_bucket(db_session, source.id, START)
    entities = [
        await add_entity(db_session, instance, source, key=f"{port}/tcp")
        for port in range(10_000, 10_200)
    ]
    builder = IpsBaselineBuilder(
        db_session,
        settings=settings(baseline_min_samples=1, ips_baseline_rebuild_batch_size=50),
    )
    train_through = START + timedelta(hours=1)

    with recorded_statements(db_session, only="ips_") as small:
        await builder.rebuild_entities(entities[:10], train_through=train_through)
    with recorded_statements(db_session, only="ips_") as large:
        await builder.rebuild_entities(entities, train_through=train_through)

    assert len(small) == 5
    assert len(large) == 14
    assert len(large) - len(small) == 3 * 3


async def test_rebuild_due_scoped_to_one_source_never_touches_another(db_session) -> None:
    instance, first, first_profile = await add_source(db_session)
    second = await make_log_source(db_session, instance, qradar_id=413, name="ips-413")
    second_profile = IpsSourceProfile(
        instance_id=instance.id,
        log_source_id=second.id,
        enabled=True,
        profile_version=1,
        mapping=first_profile.mapping,
        action_value_map=first_profile.action_value_map,
        validation_status=IpsProfileValidationStatus.VALID,
        validated_profile_version=1,
        validation_report={},
    )
    db_session.add(second_profile)
    await db_session.flush()
    entities = {}
    for source in (first, second):
        await add_bucket(db_session, source.id, START)
        entities[source.id] = await add_entity(db_session, instance, source)
    builder = IpsBaselineBuilder(db_session, settings=settings(baseline_min_samples=1))
    train_through = START + timedelta(hours=1)

    rebuilt = await builder.rebuild_due(
        train_through=train_through, now=train_through, log_source_id=first.id
    )

    assert set(rebuilt["entities"]) == {entities[first.id].id}
    assert set(rebuilt["sources"]) == {first.id}
    cell_owners = set(await db_session.scalars(select(IpsEntityBaseline.ips_entity_id)))
    assert cell_owners == {entities[first.id].id}
    source_owners = set(await db_session.scalars(select(IpsSourceBaseline.log_source_id)))
    assert source_owners == {first.id}
