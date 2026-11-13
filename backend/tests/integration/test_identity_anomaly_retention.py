"""Closed identity incidents age out; live, recent and referenced rows stay."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    EvidenceStatus,
    IdentityAnomalyType,
    IdentityMeasurement,
    Severity,
    TriageStatus,
)
from app.models.identity_anomaly import IdentityAnomaly, IdentityDetectorState
from app.models.triage import AnomalyTriage
from app.services.identity_anomaly_retention import IdentityAnomalyRetention
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, tzinfo=UTC)
DAYS = 10
CUTOFF = NOW - timedelta(days=DAYS)
OLD = CUTOFF - timedelta(seconds=1)


def cfg(**extra: object):
    return get_settings().model_copy(
        update={
            "identity_anomaly_retention_days": DAYS,
            "identity_retention_batch_size": 100,
            "identity_retention_max_batches": 20,
            "retention_enabled": False,
            "identity_retention_enabled": False,
            **extra,
        }
    )


def incident(
    source_id: uuid.UUID,
    *,
    state: AnomalyState,
    detected_at: datetime,
    **kw: object,
) -> IdentityAnomaly:
    return IdentityAnomaly(
        log_source_id=source_id,
        event_id=4726,
        detector_type=IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED,
        fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.HIGH,
        state=state,
        detected_at=detected_at,
        consecutive_buckets=1,
        policy_version=1,
        profile_version=1,
        catalog_version=1,
        details={},
        evidence_status=EvidenceStatus.COMPLETE,
        observed_measurement=IdentityMeasurement.EXACT,
        **kw,
    )


async def run(session, settings=None):
    return await IdentityAnomalyRetention(
        session, settings=settings or cfg(), clock=lambda: NOW
    ).run()


async def test_old_resolved_rows_go_and_the_cutoff_stays(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc")
    old = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    boundary = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=CUTOFF, resolved_at=CUTOFF
    )
    fresh = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=NOW, resolved_at=NOW
    )
    db_session.add_all([old, boundary, fresh])
    await db_session.commit()

    report = await run(db_session)

    assert report.as_dict() == {
        "deleted": {"identity_anomaly": 1},
        "incomplete": [],
        "skipped_reason": None,
    }
    left = set((await db_session.scalars(select(IdentityAnomaly.id))).all())
    assert old.id not in left
    assert boundary.id in left
    assert fresh.id in left


@pytest.mark.parametrize("state", [AnomalyState.OPEN, AnomalyState.RECOVERING])
async def test_open_and_recovering_rows_stay(db_session, state: AnomalyState) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc")
    db_session.add(incident(source.id, state=state, detected_at=OLD, anomaly_end=OLD))
    await db_session.commit()

    assert (await run(db_session)).deleted["identity_anomaly"] == 0
    assert await db_session.scalar(select(func.count()).select_from(IdentityAnomaly)) == 1


async def test_detector_pointers_and_alert_ids_keep_the_row(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc")
    pointed = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    opened = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    named = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    snapshot_only = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    db_session.add_all([pointed, opened, named, snapshot_only])
    await db_session.flush()
    db_session.add(
        IdentityDetectorState(
            log_source_id=source.id,
            event_id=4726,
            detector_type=IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED,
            state=AnomalyState.RESOLVED,
            active_anomaly_id=pointed.id,
        )
    )
    db_session.add(
        IdentityDetectorState(
            log_source_id=source.id,
            event_id=4720,
            detector_type=IdentityAnomalyType.IDENTITY_ACCOUNT_CREATED,
            state=AnomalyState.NORMAL,
            open_anomaly_id=opened.id,
        )
    )
    db_session.add(
        Alert(
            dedup_key=f"keep-{named.id}",
            fingerprint=f"keep-{named.id}",
            title="kept",
            severity=Severity.HIGH,
            status=AlertStatus.RESOLVED,
            source_type="identity_source",
            source_id=source.id,
            source_anomaly_ids=[str(named.id)],
            evidence_snapshot={"copy": str(snapshot_only.id)},
            opened_at=OLD,
            first_seen_at=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["identity_anomaly"] == 1
    left = set((await db_session.scalars(select(IdentityAnomaly.id))).all())
    assert pointed.id in left
    assert opened.id in left
    assert named.id in left
    assert snapshot_only.id not in left
    assert await db_session.scalar(select(func.count()).select_from(Alert)) == 1


async def test_orphan_triage_goes_and_a_live_triage_stays(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc")
    gone = incident(
        source.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    kept = incident(
        source.id, state=AnomalyState.OPEN, detected_at=OLD
    )
    db_session.add_all([gone, kept])
    await db_session.flush()
    db_session.add_all(
        [
            AnomalyTriage(
                family=AnomalyFamily.IDENTITY,
                anomaly_id=gone.id,
                status=TriageStatus.NEW,
                updated_by="analyst",
                version=1,
            ),
            AnomalyTriage(
                family=AnomalyFamily.IDENTITY,
                anomaly_id=kept.id,
                status=TriageStatus.NEW,
                updated_by="analyst",
                version=1,
            ),
        ]
    )
    await db_session.commit()

    report = await run(db_session)

    assert report.deleted["identity_anomaly"] == 1
    assert report.deleted["anomaly_triage"] == 1
    left = set((await db_session.scalars(select(AnomalyTriage.anomaly_id))).all())
    assert left == {kept.id}
