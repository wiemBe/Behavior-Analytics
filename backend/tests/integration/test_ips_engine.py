"""The IPS anomaly engine against a real database: subjects, guards, lifecycle.

One bucket of one source is judged in a fixed number of statements whatever the
number of entities (pinned in test_ips_scale.py); these tests pin what the
engine decides: the measured-zero absence rule, fail-closed verdicts, the
hysteresis WAF uses, and the incident's fixed fingerprint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ips.baseline import (
    IPS_ENTITY_EVENTS_SEMANTICS,
    IPS_SOURCE_METRIC_SEMANTICS,
)
from app.ips.engine import IpsAnomalyEngine
from app.models.enums import (
    AnomalyState,
    IpsAnomalyType,
    IpsDimension,
)
from app.models.ips import (
    IpsAnomaly,
    IpsDetectorState,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
)
from app.models.log_source import LogSource
from tests.integration.test_ips_baseline import add_bucket, add_source, settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Monday 10:00, so a day-stepped bucket series keeps one weekday/hour cell.
START = datetime(2026, 8, 3, 10, tzinfo=UTC)
DAY = timedelta(days=1)

PORT = IpsAnomalyType.IPS_PORT_RATE_SPIKE
CONCENTRATION = IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE
NEW_PORT = IpsAnomalyType.IPS_NEW_DESTINATION_PORT


def engine_settings(**overrides: object):
    values: dict[str, object] = {
        "anomaly_open_after_intervals": 2,
        "anomaly_resolve_after_intervals": 3,
    }
    values.update(overrides)
    return settings(**values)


async def add_entity(
    session,
    instance,
    source: LogSource,
    *,
    dimension: IpsDimension = IpsDimension.DESTINATION_PORT,
    key: str = "443/tcp",
    first_seen_at: datetime = START,
) -> IpsEntity:
    entity = IpsEntity(
        instance_id=instance.id,
        log_source_id=source.id,
        dimension=dimension,
        value_key=key,
        display_name=key,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        active=True,
        normalizer_version=1,
    )
    session.add(entity)
    await session.flush()
    return entity


async def add_metric(
    session, entity: IpsEntity, at: datetime, events: int
) -> IpsEntityMetric:
    row = IpsEntityMetric(
        ips_entity_id=entity.id, bucket_start=at, events=events, bucket_seconds=3600
    )
    session.add(row)
    await session.flush()
    return row


async def add_entity_cell(
    session,
    entity: IpsEntity,
    at: datetime,
    *,
    profile_version: int = 1,
    metric: str = "entity_events",
    semantics: str = IPS_ENTITY_EVENTS_SEMANTICS,
) -> IpsEntityBaseline:
    cell = IpsEntityBaseline(
        ips_entity_id=entity.id,
        metric_name=metric,
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
        metric_semantics=semantics,
        profile_version=profile_version,
        normalizer_version=1,
    )
    session.add(cell)
    await session.flush()
    return cell


async def add_share_cell(
    session,
    source: LogSource,
    at: datetime,
    *,
    metric: str,
    median: float,
    mad: float,
    p95: float,
    numerator_sum: float | None = None,
    denominator_sum: float | None = None,
) -> IpsSourceBaseline:
    cell = IpsSourceBaseline(
        log_source_id=source.id,
        metric_name=metric,
        weekday=at.isoweekday(),
        hour=at.hour,
        median=median,
        mad=mad,
        p05=median - mad,
        p95=p95,
        sample_count=10,
        is_reliable=True,
        completeness=1.0,
        observations=[median] * 10,
        baseline_version=1,
        metric_semantics=IPS_SOURCE_METRIC_SEMANTICS[metric],
        profile_version=1,
        normalizer_version=1,
        numerator_sum=numerator_sum,
        denominator_sum=denominator_sum,
    )
    session.add(cell)
    await session.flush()
    return cell


async def state_for(
    session, *, entity_id=None, source_id=None, detector: IpsAnomalyType
) -> IpsDetectorState | None:
    query = select(IpsDetectorState).where(
        IpsDetectorState.detector_type == detector.value
    )
    if entity_id is not None:
        query = query.where(IpsDetectorState.ips_entity_id == entity_id)
    else:
        query = query.where(IpsDetectorState.log_source_id == source_id)
    return await session.scalar(query)


async def anomalies_for(session, detector: IpsAnomalyType) -> list[IpsAnomaly]:
    return list(
        (
            await session.scalars(
                select(IpsAnomaly).where(IpsAnomaly.detector_type == detector.value)
            )
        ).all()
    )


async def evaluate(session, source: LogSource, at: datetime, *, settings_overrides=None):
    bucket = await session.get(IpsSourceBucket, (source.id, at))
    assert bucket is not None
    engine = IpsAnomalyEngine(
        session,
        settings=engine_settings(**(settings_overrides or {})),
        clock=lambda: at + timedelta(hours=1),
    )
    return await engine.evaluate_bucket(source, bucket)


class TestEntitySpikeLifecycle:
    async def test_a_port_spike_opens_after_open_after_and_resolves_after_resolve_after(
        self, db_session
    ) -> None:
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        # Cells for every weekday the day-stepped buckets land on.
        for offset in range(6):
            await add_entity_cell(db_session, entity, START + DAY * offset)
        # Two anomalous buckets open the incident; three measured-normal ones
        # walk it RECOVERING -> RECOVERING -> RESOLVED.
        for offset, events in ((0, 600), (1, 600), (2, 100), (3, 100), (4, 100)):
            at = START + DAY * offset
            await add_bucket(db_session, source.id, at)
            await add_metric(db_session, entity, at, events)
            await evaluate(db_session, source, at)

        state = await state_for(db_session, entity_id=entity.id, detector=PORT)
        assert state is not None
        assert state.state == AnomalyState.RESOLVED
        assert state.consecutive_healthy == 3
        incidents = await anomalies_for(db_session, PORT)
        assert [i.state for i in incidents] == [AnomalyState.RESOLVED]
        assert incidents[0].resolved_at is not None

    async def test_an_entity_without_a_row_is_a_measured_zero_and_can_resolve(
        self, db_session
    ) -> None:
        """The absence rule: no row in an exact, covered, untruncated dimension
        is a measured zero, which is what lets a silent entity's incident close."""
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        for offset in range(5):
            await add_entity_cell(db_session, entity, START + DAY * offset)
        # Open on two spiked buckets ...
        for offset in (0, 1):
            at = START + DAY * offset
            await add_bucket(db_session, source.id, at)
            await add_metric(db_session, entity, at, 600)
            await evaluate(db_session, source, at)
        assert await state_for(db_session, entity_id=entity.id, detector=PORT)
        # ... then the entity goes silent: buckets without a metric row.
        for offset in (2, 3, 4):
            at = START + DAY * offset
            await add_bucket(db_session, source.id, at)
            result = await evaluate(db_session, source, at)
            assert result.inferred_observations == 1
        state = await state_for(db_session, entity_id=entity.id, detector=PORT)
        assert state is not None
        assert state.state == AnomalyState.RESOLVED
        incident = (await anomalies_for(db_session, PORT))[0]
        assert incident.state == AnomalyState.RESOLVED

    async def test_a_lower_bound_dimension_is_insufficient_and_advances_nothing(
        self, db_session
    ) -> None:
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        await add_entity_cell(db_session, entity, START)
        await add_bucket(
            db_session,
            source.id,
            START,
            destination_port_measurement="LOWER_BOUND",
        )
        await evaluate(db_session, source, START)

        state = await state_for(db_session, entity_id=entity.id, detector=PORT)
        assert state is not None
        assert state.state == AnomalyState.INSUFFICIENT_DATA
        assert (state.consecutive_anomalous, state.consecutive_healthy) == (0, 0)
        assert await anomalies_for(db_session, PORT) == []

    async def test_a_version_change_makes_the_cells_incompatible(
        self, db_session
    ) -> None:
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        await add_entity_cell(db_session, entity, START, profile_version=1)
        await add_bucket(db_session, source.id, START, profile_version=2)
        await add_metric(db_session, entity, START, 600)
        await evaluate(db_session, source, START)

        state = await state_for(db_session, entity_id=entity.id, detector=PORT)
        assert state is not None
        assert state.state == AnomalyState.INSUFFICIENT_DATA
        assert await anomalies_for(db_session, PORT) == []


class TestSourceDetectors:
    async def test_source_concentration_opens_and_resolves(self, db_session) -> None:
        _instance, source, _profile = await add_source(db_session)
        for offset in range(5):
            at = START + DAY * offset
            await add_share_cell(
                db_session,
                source,
                at,
                metric="top1_source_share",
                median=0.2,
                mad=0.02,
                p95=0.25,
                numerator_sum=80,
                denominator_sum=400,
            )
        # The largest source IP jumps from a fifth to nine tenths of the traffic.
        for offset, top1 in ((0, 90), (1, 90), (2, 20), (3, 20), (4, 20)):
            at = START + DAY * offset
            await add_bucket(db_session, source.id, at, top1=top1)
            await evaluate(db_session, source, at)

        state = await state_for(db_session, source_id=source.id, detector=CONCENTRATION)
        assert state is not None
        assert state.state == AnomalyState.RESOLVED
        incident = (await anomalies_for(db_session, CONCENTRATION))[0]
        assert incident.subject_kind == "IPS_SOURCE"
        assert incident.ips_entity_id is None
        assert incident.log_source_id == source.id
        assert incident.state == AnomalyState.RESOLVED


class TestNewDestinationPort:
    async def _seed_history(self, session, source, *, complete: bool) -> datetime:
        """A port entity first seen at START, with an hourly history behind it."""
        first_seen = START
        hours = 24  # ips_new_port_history_days=1 at a 3600 s cadence
        starts = [first_seen - timedelta(hours=h) for h in range(hours, 0, -1)]
        if not complete:
            starts.remove(first_seen - timedelta(hours=3))
        for at in [*starts, first_seen, first_seen + timedelta(hours=1)]:
            await add_bucket(session, source.id, at)
        return first_seen

    async def test_a_new_port_opens_on_a_verified_history(self, db_session) -> None:
        instance, source, _profile = await add_source(db_session)
        first_seen = await self._seed_history(db_session, source, complete=True)
        entity = await add_entity(db_session, instance, source, first_seen_at=first_seen)
        judged = first_seen + timedelta(hours=2)
        await add_bucket(db_session, source.id, judged)
        for at, events in (
            (first_seen, 10),
            (first_seen + timedelta(hours=1), 15),
            (judged, 30),
        ):
            await add_metric(db_session, entity, at, events)
        await evaluate(
            db_session,
            source,
            judged,
            settings_overrides={
                "anomaly_open_after_intervals": 1,
                "ips_new_port_history_days": 1,
            },
        )

        incidents = await anomalies_for(db_session, NEW_PORT)
        assert [i.state for i in incidents] == [AnomalyState.OPEN]
        assert incidents[0].ips_entity_id == entity.id
        assert incidents[0].observed_value == 55.0  # every event since first appearance

    async def test_an_incomplete_history_stays_insufficient(self, db_session) -> None:
        instance, source, _profile = await add_source(db_session)
        first_seen = await self._seed_history(db_session, source, complete=False)
        entity = await add_entity(db_session, instance, source, first_seen_at=first_seen)
        judged = first_seen + timedelta(hours=2)
        await add_bucket(db_session, source.id, judged)
        for at, events in (
            (first_seen, 10),
            (first_seen + timedelta(hours=1), 15),
            (judged, 30),
        ):
            await add_metric(db_session, entity, at, events)
        await evaluate(
            db_session,
            source,
            judged,
            settings_overrides={
                "anomaly_open_after_intervals": 1,
                "ips_new_port_history_days": 1,
            },
        )

        state = await state_for(db_session, entity_id=entity.id, detector=NEW_PORT)
        assert state is not None
        assert state.state == AnomalyState.INSUFFICIENT_DATA
        assert await anomalies_for(db_session, NEW_PORT) == []


class TestMaintenanceAndFingerprint:
    async def test_a_bucket_in_maintenance_advances_nothing(self, db_session) -> None:
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        for offset in range(3):
            await add_entity_cell(db_session, entity, START + DAY * offset)
        await add_bucket(db_session, source.id, START)
        await add_metric(db_session, entity, START, 600)
        await evaluate(db_session, source, START)
        assert (await state_for(db_session, entity_id=entity.id, detector=PORT)).state == (
            AnomalyState.CANDIDATE
        )

        # A spiked bucket recorded in maintenance: judged by nobody.
        at = START + DAY
        await add_bucket(db_session, source.id, at, in_maintenance=True)
        await add_metric(db_session, entity, at, 600)
        result = await evaluate(db_session, source, at)
        assert all(
            report.skipped_reason == "source was in maintenance"
            for report in result.subject_reports
        )
        state = await state_for(db_session, entity_id=entity.id, detector=PORT)
        assert state.state == AnomalyState.CANDIDATE
        assert state.consecutive_anomalous == 1
        assert state.last_interval_start == START
        assert len(await anomalies_for(db_session, PORT)) == 1

        # The bucket after maintenance still confirms on the next reading.
        at = START + 2 * DAY
        await add_bucket(db_session, source.id, at)
        await add_metric(db_session, entity, at, 600)
        await evaluate(db_session, source, at)
        assert (await state_for(db_session, entity_id=entity.id, detector=PORT)).state == (
            AnomalyState.OPEN
        )

    async def test_the_fingerprint_is_set_at_creation_and_survives_refreshes(
        self, db_session
    ) -> None:
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        for offset in range(4):
            await add_entity_cell(db_session, entity, START + DAY * offset)
        for offset, events in ((0, 600), (1, 600)):
            at = START + DAY * offset
            await add_bucket(db_session, source.id, at)
            await add_metric(db_session, entity, at, events)
            await evaluate(db_session, source, at)
        incident = (await anomalies_for(db_session, PORT))[0]
        assert incident.state == AnomalyState.OPEN
        fingerprint = incident.fingerprint
        assert fingerprint

        # A worse reading refreshes the incident but never re-identifies it.
        at = START + 2 * DAY
        await add_bucket(db_session, source.id, at)
        await add_metric(db_session, entity, at, 900)
        await evaluate(db_session, source, at)
        refreshed = (await anomalies_for(db_session, PORT))[0]
        assert refreshed.fingerprint == fingerprint
        assert refreshed.observed_value == 900.0
