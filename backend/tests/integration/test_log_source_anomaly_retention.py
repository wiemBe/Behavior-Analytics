"""Closed Phase A incidents age out; live, recent and referenced rows stay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyState, AnomalyType, Severity
from app.models.explanation import (
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.log_source import AnomalyStateTransition, LogSourceAnomaly, LogSourceDetectorState
from app.services.log_source_anomaly_retention import LogSourceAnomalyRetention
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, tzinfo=UTC)
DAYS = 10
CUTOFF = NOW - timedelta(days=DAYS)
OLD = CUTOFF - timedelta(seconds=1)


def cfg(**extra: object):
    return get_settings().model_copy(
        update={
            "log_source_anomaly_retention_days": DAYS,
            "log_source_anomaly_retention_batch_size": 100,
            "log_source_anomaly_retention_max_batches": 20,
            "retention_enabled": False,
            **extra,
        }
    )


def incident(source_id, *, state: AnomalyState, detected_at: datetime, **kw) -> LogSourceAnomaly:
    return LogSourceAnomaly(
        log_source_id=source_id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.MEDIUM,
        state=state,
        detected_at=detected_at,
        details={},
        **kw,
    )


async def source(session):
    instance = await make_instance(session)
    return await make_log_source(session, instance, name="phase-a")


async def run(session, settings=None):
    return await LogSourceAnomalyRetention(
        session, settings=settings or cfg(), clock=lambda: NOW
    ).run()


async def count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_the_cutoff_row_is_kept_and_the_older_row_goes(db_session) -> None:
    src = await source(db_session)
    old = incident(
        src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD
    )
    boundary = incident(
        src.id, state=AnomalyState.RESOLVED, detected_at=CUTOFF, resolved_at=CUTOFF
    )
    fresh = incident(src.id, state=AnomalyState.RESOLVED, detected_at=NOW, resolved_at=NOW)
    db_session.add_all([old, boundary, fresh])
    await db_session.commit()
    old_id, boundary_id, fresh_id = old.id, boundary.id, fresh.id

    report = await run(db_session)

    assert report.as_dict() == {
        "deleted": {"log_source_anomaly": 1},
        "incomplete": [],
        "skipped_reason": None,
    }
    assert await count(db_session, LogSourceAnomaly) == 2
    left = set(await db_session.scalars(select(LogSourceAnomaly.id)))
    assert old_id not in left
    assert boundary_id in left
    assert fresh_id in left


async def test_normal_ages_by_anomaly_end_not_by_a_fresh_detected_at(db_session) -> None:
    src = await source(db_session)
    db_session.add(
        incident(
            src.id,
            state=AnomalyState.NORMAL,
            detected_at=NOW,
            anomaly_end=OLD,
            resolved_at=None,
        )
    )
    await db_session.commit()

    report = await run(db_session)

    assert report.deleted["log_source_anomaly"] == 1
    assert await count(db_session, LogSourceAnomaly) == 0


async def test_insufficient_data_ages_by_detected_at(db_session) -> None:
    src = await source(db_session)
    db_session.add(
        incident(
            src.id,
            state=AnomalyState.INSUFFICIENT_DATA,
            detected_at=OLD,
            anomaly_end=None,
            resolved_at=None,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 1
    assert await count(db_session, LogSourceAnomaly) == 0


async def test_a_fresh_resolved_at_keeps_an_ancient_detected_at(db_session) -> None:
    src = await source(db_session)
    db_session.add(
        incident(
            src.id,
            state=AnomalyState.RESOLVED,
            detected_at=OLD - timedelta(days=400),
            resolved_at=NOW,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 0
    assert await count(db_session, LogSourceAnomaly) == 1


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
    src = await source(db_session)
    db_session.add(
        incident(src.id, state=state, detected_at=OLD, resolved_at=OLD, anomaly_end=OLD)
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 0
    assert await count(db_session, LogSourceAnomaly) == 1


async def test_detector_active_pointer_keeps_the_incident(db_session) -> None:
    src = await source(db_session)
    row = incident(src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD)
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        LogSourceDetectorState(
            log_source_id=src.id,
            anomaly_type=AnomalyType.VOLUME_DROP,
            state=AnomalyState.RESOLVED,
            active_anomaly_id=row.id,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 0
    assert await count(db_session, LogSourceAnomaly) == 1


async def test_detector_open_pointer_keeps_the_incident(db_session) -> None:
    src = await source(db_session)
    row = incident(src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD)
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        LogSourceDetectorState(
            log_source_id=src.id,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            state=AnomalyState.NORMAL,
            open_anomaly_id=row.id,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 0
    assert await count(db_session, LogSourceAnomaly) == 1


@pytest.mark.parametrize("status", list(AlertStatus))
async def test_any_alert_status_keeps_the_incident(db_session, status: AlertStatus) -> None:
    """Any alert that lists the incident keeps it for the life of the alert."""
    src = await source(db_session)
    row = incident(src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD)
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        Alert(
            dedup_key=f"keep-{status.value}-{row.id}",
            fingerprint=f"keep-{status.value}-{row.id}",
            title="volume spike",
            severity=Severity.HIGH,
            status=status,
            source_type="log_source",
            source_id=src.id,
            source_anomaly_ids=[str(row.id)],
            evidence_snapshot={"anomaly_id": str(row.id)},
            opened_at=OLD,
            first_seen_at=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 0
    assert await count(db_session, LogSourceAnomaly) == 1
    assert await count(db_session, Alert) == 1


async def test_source_id_and_evidence_snapshot_are_not_references(db_session) -> None:
    """source_id is the log source. The snapshot is the alert's own copy."""
    src = await source(db_session)
    row = incident(src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD)
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        Alert(
            dedup_key=f"loose-{row.id}",
            fingerprint=f"loose-{row.id}",
            title="volume spike",
            severity=Severity.HIGH,
            status=AlertStatus.OPEN,
            source_type="log_source",
            source_id=row.id,
            source_anomaly_ids=[],
            evidence_snapshot={"anomaly_id": str(row.id)},
            opened_at=OLD,
            first_seen_at=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 1
    assert await count(db_session, LogSourceAnomaly) == 0


async def test_exclusive_children_leave_with_the_incident(db_session) -> None:
    src = await source(db_session)
    row = incident(src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD)
    db_session.add(row)
    await db_session.flush()
    explanation = AnomalyExplanation(
        anomaly_id=row.id,
        anomaly_window_start=OLD,
        anomaly_window_end=OLD,
        baseline_window_start=OLD,
        baseline_window_end=OLD,
        comparison_strategy="recent_normal_window",
    )
    db_session.add(explanation)
    await db_session.flush()
    db_session.add_all(
        [
            AnomalyExplanationDimension(explanation_id=explanation.id, dimension="source_ip"),
            AnomalyExplanationContributor(
                explanation_id=explanation.id, dimension="source_ip", value="10.1.1.1"
            ),
            AnomalyStateTransition(
                anomaly_id=row.id, to_state=AnomalyState.RESOLVED, occurred_at=OLD
            ),
        ]
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["log_source_anomaly"] == 1
    assert await count(db_session, LogSourceAnomaly) == 0
    assert await count(db_session, AnomalyStateTransition) == 0
    assert await count(db_session, AnomalyExplanation) == 0
    assert await count(db_session, AnomalyExplanationDimension) == 0
    assert await count(db_session, AnomalyExplanationContributor) == 0


async def test_batch_cap_marks_incomplete(db_session) -> None:
    src = await source(db_session)
    db_session.add_all(
        [
            incident(
                src.id,
                state=AnomalyState.RESOLVED,
                detected_at=OLD - timedelta(seconds=i),
                resolved_at=OLD - timedelta(seconds=i),
            )
            for i in range(5)
        ]
    )
    await db_session.commit()

    report = await run(
        db_session,
        cfg(
            log_source_anomaly_retention_batch_size=2,
            log_source_anomaly_retention_max_batches=1,
        ),
    )

    assert report.deleted == {"log_source_anomaly": 2}
    assert report.incomplete == ["log_source_anomaly"]
    assert await count(db_session, LogSourceAnomaly) == 3


async def test_retention_enabled_false_still_deletes(db_session) -> None:
    src = await source(db_session)
    db_session.add(
        incident(src.id, state=AnomalyState.RESOLVED, detected_at=OLD, resolved_at=OLD)
    )
    await db_session.commit()
    settings = cfg()
    assert settings.retention_enabled is False

    assert (await run(db_session, settings)).deleted["log_source_anomaly"] == 1
