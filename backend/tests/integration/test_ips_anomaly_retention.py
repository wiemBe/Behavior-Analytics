"""Closed IPS incidents age out; live, recent and referenced rows stay."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    IpsAnomalyType,
    IpsDimension,
    Severity,
)
from app.models.ips import IpsAnomaly, IpsAnomalyTransition, IpsDetectorState, IpsEntity
from app.services.ips_anomaly_retention import IpsAnomalyRetention
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, tzinfo=UTC)
DAYS = 10
CUTOFF = NOW - timedelta(days=DAYS)
OLD = CUTOFF - timedelta(seconds=1)


def cfg(**extra: object):
    return get_settings().model_copy(
        update={
            "ips_anomaly_retention_days": DAYS,
            "ips_anomaly_retention_batch_size": 100,
            "ips_anomaly_retention_max_batches": 20,
            "retention_enabled": False,
            "ips_retention_enabled": False,
            **extra,
        }
    )


async def world(session):
    instance = await make_instance(session)
    src = await make_log_source(session, instance, name="ips-src")
    entity = IpsEntity(
        instance_id=instance.id,
        log_source_id=src.id,
        dimension=IpsDimension.DESTINATION_PORT,
        value_key="443/tcp",
        first_seen_at=NOW,
        last_seen_at=NOW,
        active=True,
        normalizer_version=1,
    )
    session.add(entity)
    await session.flush()
    return src, entity


def anomaly(
    *,
    state: AnomalyState,
    detected_at: datetime,
    entity_id=None,
    source_id=None,
    **kw,
) -> IpsAnomaly:
    kind = "IPS_ENTITY" if entity_id is not None else "IPS_SOURCE"
    return IpsAnomaly(
        subject_kind=kind,
        ips_entity_id=entity_id,
        log_source_id=source_id,
        detector_type=(
            IpsAnomalyType.IPS_PORT_RATE_SPIKE
            if entity_id is not None
            else IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE
        ),
        fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.MEDIUM,
        state=state,
        detected_at=detected_at,
        profile_version=1,
        normalizer_version=1,
        details={},
        **kw,
    )


async def run(session, settings=None):
    return await IpsAnomalyRetention(session, settings=settings or cfg(), clock=lambda: NOW).run()


async def count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_entity_and_source_rows_past_the_cutoff_go_and_the_boundary_stays(
    db_session,
) -> None:
    src, entity = await world(db_session)
    old_entity = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, entity_id=entity.id, resolved_at=OLD
    )
    old_source = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, source_id=src.id, resolved_at=OLD
    )
    boundary = anomaly(
        state=AnomalyState.RESOLVED,
        detected_at=CUTOFF,
        entity_id=entity.id,
        resolved_at=CUTOFF,
    )
    fresh = anomaly(
        state=AnomalyState.RESOLVED, detected_at=NOW, source_id=src.id, resolved_at=NOW
    )
    db_session.add_all([old_entity, old_source, boundary, fresh])
    await db_session.commit()

    report = await run(db_session)

    assert report.as_dict() == {
        "deleted": {"ips_anomaly": 2},
        "incomplete": [],
        "skipped_reason": None,
    }
    left = set((await db_session.scalars(select(IpsAnomaly.id))).all())
    assert old_entity.id not in left
    assert old_source.id not in left
    assert boundary.id in left
    assert fresh.id in left
    assert await count(db_session, IpsEntity) == 1


async def test_normal_ages_by_anomaly_end(db_session) -> None:
    _, entity = await world(db_session)
    db_session.add(
        anomaly(
            state=AnomalyState.NORMAL,
            detected_at=NOW,
            entity_id=entity.id,
            anomaly_end=OLD,
            resolved_at=None,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 1


async def test_insufficient_data_ages_by_detected_at(db_session) -> None:
    src, _entity = await world(db_session)
    db_session.add(
        anomaly(
            state=AnomalyState.INSUFFICIENT_DATA,
            detected_at=OLD,
            source_id=src.id,
            anomaly_end=None,
            resolved_at=None,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 1


async def test_a_fresh_resolved_at_keeps_an_ancient_detected_at(db_session) -> None:
    _, entity = await world(db_session)
    db_session.add(
        anomaly(
            state=AnomalyState.RESOLVED,
            detected_at=OLD - timedelta(days=400),
            entity_id=entity.id,
            resolved_at=NOW,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 0
    assert await count(db_session, IpsAnomaly) == 1


@pytest.mark.parametrize(
    "state",
    [
        AnomalyState.CANDIDATE,
        AnomalyState.OPEN,
        AnomalyState.RECOVERING,
        AnomalyState.SUPPRESSED,
    ],
)
async def test_live_states_are_kept(db_session, state: AnomalyState) -> None:
    _, entity = await world(db_session)
    db_session.add(
        anomaly(
            state=state,
            detected_at=OLD,
            entity_id=entity.id,
            resolved_at=OLD,
            anomaly_end=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 0
    assert await count(db_session, IpsAnomaly) == 1


async def test_detector_active_pointer_keeps_the_incident(db_session) -> None:
    _, entity = await world(db_session)
    row = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, entity_id=entity.id, resolved_at=OLD
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        IpsDetectorState(
            subject_kind="IPS_ENTITY",
            ips_entity_id=entity.id,
            detector_type=IpsAnomalyType.IPS_PORT_RATE_SPIKE,
            state=AnomalyState.RESOLVED,
            active_anomaly_id=row.id,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 0
    assert await count(db_session, IpsAnomaly) == 1


async def test_detector_open_pointer_keeps_the_incident(db_session) -> None:
    src, _entity = await world(db_session)
    row = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, source_id=src.id, resolved_at=OLD
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        IpsDetectorState(
            subject_kind="IPS_SOURCE",
            log_source_id=src.id,
            detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
            state=AnomalyState.NORMAL,
            open_anomaly_id=row.id,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 0
    assert await count(db_session, IpsAnomaly) == 1


@pytest.mark.parametrize("status", list(AlertStatus))
async def test_any_alert_status_keeps_the_incident(db_session, status: AlertStatus) -> None:
    """Any alert that lists the incident keeps it for the life of the alert."""
    _, entity = await world(db_session)
    row = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, entity_id=entity.id, resolved_at=OLD
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        Alert(
            dedup_key=f"keep-{status.value}-{row.id}",
            fingerprint=f"keep-{status.value}-{row.id}",
            title="port spike",
            severity=Severity.HIGH,
            status=status,
            source_type="ips_entity",
            source_id=entity.id,
            source_anomaly_ids=[str(row.id)],
            evidence_snapshot={"anomaly_id": str(row.id)},
            opened_at=OLD,
            first_seen_at=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 0
    assert await count(db_session, IpsAnomaly) == 1
    assert await count(db_session, Alert) == 1


async def test_source_id_and_evidence_snapshot_are_not_references(db_session) -> None:
    _, entity = await world(db_session)
    row = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, entity_id=entity.id, resolved_at=OLD
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        Alert(
            dedup_key=f"loose-{row.id}",
            fingerprint=f"loose-{row.id}",
            title="port spike",
            severity=Severity.HIGH,
            status=AlertStatus.OPEN,
            source_type="ips_entity",
            source_id=row.id,
            source_anomaly_ids=[],
            evidence_snapshot={"anomaly_id": str(row.id)},
            opened_at=OLD,
            first_seen_at=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 1


async def test_transitions_leave_with_the_incident(db_session) -> None:
    _, entity = await world(db_session)
    row = anomaly(
        state=AnomalyState.RESOLVED, detected_at=OLD, entity_id=entity.id, resolved_at=OLD
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        IpsAnomalyTransition(
            ips_anomaly_id=row.id, to_state=AnomalyState.RESOLVED, occurred_at=OLD
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["ips_anomaly"] == 1
    assert await count(db_session, IpsAnomaly) == 0
    assert await count(db_session, IpsAnomalyTransition) == 0


async def test_batch_cap_marks_incomplete(db_session) -> None:
    _, entity = await world(db_session)
    db_session.add_all(
        [
            anomaly(
                state=AnomalyState.RESOLVED,
                detected_at=OLD - timedelta(seconds=i),
                entity_id=entity.id,
                resolved_at=OLD - timedelta(seconds=i),
            )
            for i in range(5)
        ]
    )
    await db_session.commit()

    report = await run(
        db_session,
        cfg(ips_anomaly_retention_batch_size=2, ips_anomaly_retention_max_batches=1),
    )

    assert report.deleted == {"ips_anomaly": 2}
    assert report.incomplete == ["ips_anomaly"]
    assert await count(db_session, IpsAnomaly) == 3


async def test_retention_flags_false_still_delete(db_session) -> None:
    _, entity = await world(db_session)
    db_session.add(
        anomaly(
            state=AnomalyState.RESOLVED, detected_at=OLD, entity_id=entity.id, resolved_at=OLD
        )
    )
    await db_session.commit()
    settings = cfg()
    assert settings.retention_enabled is False
    assert settings.ips_retention_enabled is False

    assert (await run(db_session, settings)).deleted["ips_anomaly"] == 1
