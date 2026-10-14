"""IPS evaluation issues a bounded number of SQL statements, whatever the entity count.

Mirrors tests/integration/test_waf_scale.py: every statement the driver executes
during one bucket evaluation is counted at two sizes. A per-entity or
per-entity-per-detector query makes the counts unequal across sizes long before
any absolute bound is breached.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.ips.baseline import IPS_SOURCE_METRIC_SEMANTICS
from app.ips.behavior import IPS_DETECTOR_FUNCTIONS
from app.ips.engine import IpsAnomalyEngine
from app.models.alert import Alert
from app.models.enums import AlertStatus, IpsDimension, IpsProfileValidationStatus
from app.models.ips import (
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
    IpsSourceProfile,
)
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_ips_baseline import add_bucket, settings
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Monday 10:00 and 11:00: two judged hours of one weekday, one cell each.
H0 = datetime(2026, 8, 3, 10, tzinfo=UTC)
H1 = H0 + timedelta(hours=1)

ENTITY_SEMANTICS = "ips/entity_events/v1"


async def seeded(session, instance, *, qradar_id: int, n: int):  # type: ignore[no-untyped-def]
    """A validated source with an ACTION mapping and `n` entities across the
    three dimensions, each with a cell and a normal row in both buckets."""
    source = await make_log_source(session, instance, qradar_id=qradar_id)
    session.add(
        IpsSourceProfile(
            instance_id=instance.id,
            log_source_id=source.id,
            enabled=True,
            profile_version=1,
            mapping={
                "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
                "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
                "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
                "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
                "ACTION": {"kind": "CUSTOM", "name": "Firewall Action"},
            },
            action_value_map={"BLOCKED": ["deny"], "ALLOWED": ["allow"]},
            validation_status=IpsProfileValidationStatus.VALID,
            validated_profile_version=1,
            validation_report={},
        )
    )
    dimensions = [IpsDimension.DESTINATION_PORT] * (n - 2) + [
        IpsDimension.DESTINATION_IP,
        IpsDimension.SIGNATURE,
    ]
    keys = [f"{10000 + i}/tcp" for i in range(n - 2)] + ["10.10.20.15", "qid:5000123"]
    # First seen one bucket back: every port stays inside its novelty window,
    # so the novelty bulk loads run at both sizes too.
    first_seen = H0 - timedelta(hours=1)
    entities = [
        IpsEntity(
            instance_id=instance.id,
            log_source_id=source.id,
            dimension=dimension,
            value_key=key,
            display_name=key,
            first_seen_at=first_seen,
            last_seen_at=H1,
            active=True,
            normalizer_version=1,
        )
        for dimension, key in zip(dimensions, keys, strict=True)
    ]
    session.add_all(entities)
    await session.flush()
    for at in (H0, H1):
        await add_bucket(session, source.id, at)
        session.add_all(
            [
                IpsEntityMetric(
                    ips_entity_id=entity.id,
                    bucket_start=at,
                    events=100,
                    bucket_seconds=3600,
                )
                for entity in entities
            ]
        )
        session.add_all(
            [
                IpsEntityBaseline(
                    ips_entity_id=entity.id,
                    metric_name="entity_events",
                    weekday=at.isoweekday(),
                    hour=at.hour,
                    median=100.0,
                    mad=10.0,
                    p05=80.0,
                    p95=120.0,
                    sample_count=10,
                    is_reliable=True,
                    completeness=1.0,
                    observations=[100.0] * 10,
                    baseline_version=1,
                    metric_semantics=ENTITY_SEMANTICS,
                    profile_version=1,
                    normalizer_version=1,
                )
                for entity in entities
            ]
        )
        session.add_all(
            [
                IpsSourceBaseline(
                    log_source_id=source.id,
                    metric_name=metric,
                    weekday=at.isoweekday(),
                    hour=at.hour,
                    median=(0.2 if metric != "max_distinct_ports" else 20.0),
                    mad=(0.02 if metric != "max_distinct_ports" else 2.0),
                    p05=(0.18 if metric != "max_distinct_ports" else 18.0),
                    p95=(0.25 if metric != "max_distinct_ports" else 25.0),
                    sample_count=10,
                    is_reliable=True,
                    completeness=1.0,
                    observations=[0.2] * 10,
                    baseline_version=1,
                    metric_semantics=IPS_SOURCE_METRIC_SEMANTICS[metric],
                    profile_version=1,
                    normalizer_version=1,
                    numerator_sum=(80.0 if metric != "max_distinct_ports" else None),
                    denominator_sum=(400.0 if metric != "max_distinct_ports" else None),
                )
                for metric in IPS_SOURCE_METRIC_SEMANTICS
            ]
        )
    await session.flush()
    return source, entities


async def evaluate(session, source, at: datetime, settings):  # type: ignore[no-untyped-def]
    bucket = await session.get(IpsSourceBucket, (source.id, at))
    assert bucket is not None
    engine = IpsAnomalyEngine(
        session, settings=settings, clock=lambda: at + timedelta(hours=1)
    )
    # Every registered detector, as a scheduled run uses them.
    assert set(engine.detectors) == set(IPS_DETECTOR_FUNCTIONS)
    with recorded_statements(session) as statements:
        result = await engine.evaluate_bucket(source, bucket)
    return result, len(statements)


async def test_evaluation_statement_count_is_constant_in_entities(db_session) -> None:
    test_settings = settings()
    instance = await make_instance(db_session)
    seeded_sources = {
        size: await seeded(db_session, instance, qradar_id=qradar_id, n=size)
        for size, qradar_id in ((5, 412), (200, 413))
    }

    first: dict[int, int] = {}
    steady: dict[int, int] = {}
    for size, (source, entities) in seeded_sources.items():
        result, first[size] = await evaluate(db_session, source, H0, test_settings)
        assert len(result.subject_reports) == size + 1  # every entity, plus the source
        assert len(entities) == size
        result, steady[size] = await evaluate(db_session, source, H1, test_settings)
        assert len(result.subject_reports) == size + 1

    # Forty times the entities, the same statements, on the first run (state
    # creation included) and in steady state.
    assert first[5] == first[200], (first, steady)
    assert steady[5] == steady[200], (first, steady)
    assert steady[200] <= 12, steady


async def test_scoped_training_statement_count_is_constant_in_entities(db_session) -> None:
    from app.ips.baseline import IpsBaselineBuilder

    test_settings = settings(baseline_min_samples=1, ips_baseline_rebuild_batch_size=50)
    instance = await make_instance(db_session)
    seeded_sources = {
        size: await seeded(db_session, instance, qradar_id=qradar_id, n=size)
        for size, qradar_id in ((5, 412), (40, 413))
    }
    counts: dict[int, int] = {}
    for size, (source, _entities) in seeded_sources.items():
        builder = IpsBaselineBuilder(db_session, settings=test_settings)
        with recorded_statements(db_session) as statements:
            rebuilt = await builder.rebuild_due(
                train_through=H1 + timedelta(hours=1),
                now=H1 + timedelta(hours=1),
                limit=50,
                log_source_id=source.id,
            )
        assert len(rebuilt["entities"]) == size
        counts[size] = len(statements)
    # Eight times the entities, the same statements per source.
    assert counts[5] == counts[40], counts


async def _alerting_cost(db_session, second_bucket_events: int) -> dict[int, tuple[int, int]]:
    """(open cost, second cost) per entity count, every entity spiking at H0.

    With 100 as the second value every incident resolves at H1; with 600 each
    alert is refreshed instead.
    """
    test_settings = settings(
        ips_alerting_enabled=True,
        anomaly_open_after_intervals=1,
        anomaly_resolve_after_intervals=1,
    )
    instance = await make_instance(db_session)
    costs: dict[int, tuple[int, int]] = {}
    for size, qradar_id in ((5, 412), (200, 413)):
        source, entities = await seeded(db_session, instance, qradar_id=qradar_id, n=size)
        ids = [entity.id for entity in entities]
        for at, events in ((H0, 600), (H1, second_bucket_events)):
            rows = await db_session.scalars(
                select(IpsEntityMetric).where(
                    IpsEntityMetric.ips_entity_id.in_(ids), IpsEntityMetric.bucket_start == at
                )
            )
            for row in rows:
                row.events = events
        await db_session.flush()
        _result, first = await evaluate(db_session, source, H0, test_settings)
        opened = int(
            await db_session.scalar(
                select(func.count()).select_from(Alert).where(Alert.source_id.in_(ids))
            )
            or 0
        )
        _result, second = await evaluate(db_session, source, H1, test_settings)
        assert opened == size
        assert int(
            await db_session.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.source_id.in_(ids), Alert.status == AlertStatus.RESOLVED
                )
            )
            or 0
        ) == (size if second_bucket_events == 100 else 0)
        costs[size] = (first, second)
    return costs


async def test_refreshing_alerts_is_constant_cost_in_alerting_subjects(db_session) -> None:
    costs = await _alerting_cost(db_session, 600)
    assert costs[5][1] == costs[200][1], costs


async def test_resolving_alerts_is_constant_cost_in_alerting_subjects(db_session) -> None:
    costs = await _alerting_cost(db_session, 100)
    assert costs[5][1] == costs[200][1], costs


async def test_opening_alerts_costs_a_fixed_number_of_statements_per_new_alert(
    db_session,
) -> None:
    """AlertService.apply_bulk inserts each new alert inside its own savepoint (to keep
    the partial unique index's concurrency protection), so a first open is linear in
    the *new* alerts: exactly savepoint, insert, release each. Nothing else scales."""
    costs = await _alerting_cost(db_session, 600)
    assert costs[200][0] - costs[5][0] == 3 * (200 - 5), costs
