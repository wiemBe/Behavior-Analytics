"""Closed rule-match incidents age out; live, recent and referenced rows stay.

The service is exercised directly, and the Celery task is exercised once through
its own synchronous shim so a wiring mistake between the task and the service
cannot pass (AGENTS.md §4).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyState, Severity
from app.models.instance import QRadarInstance
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import (
    RuleAnomaly,
    RuleAnomalyTransition,
    RuleDetectorState,
)
from app.models.triage import AnomalyTriage
from app.services.rule_anomaly_retention import RuleAnomalyRetention
from app.workers import tasks
from tests.integration.schema_reset import reset_public_schema
from tests.integration.test_migrations import _sync_url

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
DAYS = 10
CUTOFF = NOW - timedelta(days=DAYS)
OLD = CUTOFF - timedelta(seconds=1)


def cfg(**extra: object):
    return get_settings().model_copy(
        update={
            "rule_anomaly_retention_days": DAYS,
            "ips_anomaly_retention_batch_size": 100,
            "ips_anomaly_retention_max_batches": 20,
            "retention_enabled": False,
            **extra,
        }
    )


async def world(session):
    instance = QRadarInstance(
        name=f"rule-ret-{uuid.uuid4().hex[:8]}",
        console_host="lab.local",
        api_version="20.0",
        provider_kind="REST",
        mcp_enabled=False,
        enabled=True,
    )
    session.add(instance)
    await session.flush()
    rule = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=9001,
        name="rule-retention",
        enabled=True,
        is_building_block=False,
    )
    session.add(rule)
    await session.flush()
    return rule


def anomaly(
    *,
    rule_id,
    state: AnomalyState,
    detected_at: datetime,
    **kw,
) -> RuleAnomaly:
    return RuleAnomaly(
        rule_id=rule_id,
        qradar_id=9001,
        is_building_block=False,
        detector_type="RULE_SILENCE",
        fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
        severity=Severity.MEDIUM,
        state=state,
        detected_at=detected_at,
        details={},
        **kw,
    )


async def run(session, settings=None):
    return await RuleAnomalyRetention(
        session, settings=settings or cfg(), clock=lambda: NOW
    ).run()


async def count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_closed_rows_past_the_cutoff_go_and_the_boundary_stays(db_session) -> None:
    rule = await world(db_session)
    old = anomaly(
        rule_id=rule.id,
        state=AnomalyState.RESOLVED,
        detected_at=OLD,
        resolved_at=OLD,
    )
    boundary = anomaly(
        rule_id=rule.id,
        state=AnomalyState.RESOLVED,
        detected_at=CUTOFF,
        resolved_at=CUTOFF,
    )
    fresh = anomaly(
        rule_id=rule.id,
        state=AnomalyState.RESOLVED,
        detected_at=NOW,
        resolved_at=NOW,
    )
    db_session.add_all([old, boundary, fresh])
    await db_session.flush()
    db_session.add(
        AnomalyTriage(
            family="rule",
            anomaly_id=old.id,
            status="NEW",
            updated_by="seed",
            version=1,
        )
    )
    await db_session.commit()

    report = await run(db_session)

    assert report.as_dict() == {
        "deleted": {"rule_anomaly": 1, "anomaly_triage": 1},
        "incomplete": [],
        "skipped_reason": None,
    }
    left = set((await db_session.scalars(select(RuleAnomaly.id))).all())
    assert old.id not in left
    assert boundary.id in left
    assert fresh.id in left
    assert await count(db_session, AnomalyTriage) == 0


async def test_normal_ages_by_anomaly_end(db_session) -> None:
    rule = await world(db_session)
    db_session.add(
        anomaly(
            rule_id=rule.id,
            state=AnomalyState.NORMAL,
            detected_at=NOW,
            anomaly_end=OLD,
            resolved_at=None,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 1


async def test_insufficient_data_ages_by_detected_at(db_session) -> None:
    rule = await world(db_session)
    db_session.add(
        anomaly(
            rule_id=rule.id,
            state=AnomalyState.INSUFFICIENT_DATA,
            detected_at=OLD,
            anomaly_end=None,
            resolved_at=None,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 1


async def test_a_fresh_resolved_at_keeps_an_ancient_detected_at(db_session) -> None:
    rule = await world(db_session)
    db_session.add(
        anomaly(
            rule_id=rule.id,
            state=AnomalyState.RESOLVED,
            detected_at=OLD - timedelta(days=400),
            resolved_at=NOW,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 0
    assert await count(db_session, RuleAnomaly) == 1


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
    rule = await world(db_session)
    db_session.add(
        anomaly(
            rule_id=rule.id,
            state=state,
            detected_at=OLD,
            resolved_at=OLD,
            anomaly_end=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 0
    assert await count(db_session, RuleAnomaly) == 1


async def test_detector_pointer_keeps_the_incident(db_session) -> None:
    rule = await world(db_session)
    row = anomaly(
        rule_id=rule.id,
        state=AnomalyState.RESOLVED,
        detected_at=OLD,
        resolved_at=OLD,
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        RuleDetectorState(
            rule_id=rule.id,
            detector_type="RULE_SILENCE",
            state=AnomalyState.RESOLVED,
            active_anomaly_id=row.id,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 0
    assert await count(db_session, RuleAnomaly) == 1


@pytest.mark.parametrize("status", list(AlertStatus))
async def test_any_alert_status_keeps_the_incident(db_session, status: AlertStatus) -> None:
    """Any alert that lists the incident keeps it for the life of the alert."""
    rule = await world(db_session)
    row = anomaly(
        rule_id=rule.id,
        state=AnomalyState.RESOLVED,
        detected_at=OLD,
        resolved_at=OLD,
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        Alert(
            dedup_key=f"keep-{status.value}-{row.id}",
            fingerprint=f"keep-{status.value}-{row.id}",
            title="rule silent",
            severity=Severity.HIGH,
            status=status,
            source_type="analytics_rule",
            source_id=rule.id,
            source_anomaly_ids=[str(row.id)],
            evidence_snapshot={"anomaly_id": str(row.id)},
            opened_at=OLD,
            first_seen_at=OLD,
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 0
    assert await count(db_session, RuleAnomaly) == 1


async def test_transitions_leave_with_the_incident(db_session) -> None:
    rule = await world(db_session)
    row = anomaly(
        rule_id=rule.id,
        state=AnomalyState.RESOLVED,
        detected_at=OLD,
        resolved_at=OLD,
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        RuleAnomalyTransition(
            rule_anomaly_id=row.id, to_state=AnomalyState.RESOLVED, occurred_at=OLD
        )
    )
    await db_session.commit()

    assert (await run(db_session)).deleted["rule_anomaly"] == 1
    assert await count(db_session, RuleAnomaly) == 0
    assert await count(db_session, RuleAnomalyTransition) == 0


async def test_batch_cap_marks_incomplete(db_session) -> None:
    rule = await world(db_session)
    db_session.add_all(
        [
            anomaly(
                rule_id=rule.id,
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
        cfg(ips_anomaly_retention_batch_size=2, ips_anomaly_retention_max_batches=1),
    )

    assert report.deleted == {"rule_anomaly": 2}
    assert report.incomplete == ["rule_anomaly"]
    assert await count(db_session, RuleAnomaly) == 3


def test_the_celery_task_runs_the_deleter(monkeypatch) -> None:
    """The operator path: the beat invokes the shim, not the service directly."""
    sync_url = _sync_url()
    reset_public_schema(sync_url)
    engine = create_engine(sync_url, future=True)
    async_engine = create_async_engine(sync_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        from app.models import Base

        Base.metadata.create_all(engine)
        with Session(engine) as session:
            instance = QRadarInstance(
                name="task-lab",
                console_host="lab.local",
                api_version="20.0",
                provider_kind="REST",
                mcp_enabled=False,
                enabled=True,
            )
            session.add(instance)
            session.flush()
            rule = AnalyticsRule(
                instance_id=instance.id,
                qradar_id=9002,
                name="task-rule",
                enabled=True,
                is_building_block=False,
            )
            session.add(rule)
            session.flush()
            now = datetime.now(UTC)
            old = RuleAnomaly(
                rule_id=rule.id,
                qradar_id=rule.qradar_id,
                is_building_block=False,
                detector_type="RULE_STORM",
                fingerprint="fp-task-old",
                severity=Severity.MEDIUM,
                state=AnomalyState.RESOLVED,
                detected_at=now - timedelta(days=400),
                resolved_at=now - timedelta(days=400),
                details={},
            )
            fresh = RuleAnomaly(
                rule_id=rule.id,
                qradar_id=rule.qradar_id,
                is_building_block=False,
                detector_type="RULE_STORM",
                fingerprint="fp-task-fresh",
                severity=Severity.MEDIUM,
                state=AnomalyState.RESOLVED,
                detected_at=now,
                resolved_at=now,
                details={},
            )
            session.add_all([old, fresh])
            session.flush()
            session.add(
                AnomalyTriage(
                    family="rule",
                    anomaly_id=old.id,
                    status="NEW",
                    updated_by="seed",
                    version=1,
                )
            )
            session.commit()
            old_id, fresh_id = old.id, fresh.id

        report = tasks.apply_rule_anomaly_retention_task()

        assert report["deleted"] == {"rule_anomaly": 1, "anomaly_triage": 1}
        with Session(engine) as session:
            remaining = set(session.scalars(select(RuleAnomaly.id)).all())
            assert old_id not in remaining
            assert fresh_id in remaining
            assert session.scalar(select(func.count()).select_from(AnomalyTriage)) == 0
    finally:
        asyncio.run(async_engine.dispose())
        engine.dispose()
