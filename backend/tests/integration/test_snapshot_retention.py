"""Rule-health and coverage snapshots age on their own day counts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.enums import CoverageStatus, RuleHealthStatus
from app.models.rule import AnalyticsRule, DetectionCoverageSnapshot, RuleHealthSnapshot
from app.services.snapshot_retention import SnapshotRetention
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def cfg(**extra: object):
    return get_settings().model_copy(
        update={
            "rule_health_snapshot_retention_days": 90,
            "detection_coverage_snapshot_retention_days": 90,
            "snapshot_retention_batch_size": 100,
            "snapshot_retention_max_batches": 20,
            "retention_enabled": False,
            **extra,
        }
    )


async def parents(session):
    instance = await make_instance(session)
    rule = AnalyticsRule(instance_id=instance.id, qradar_id=7, name="rule-7")
    session.add(rule)
    await session.flush()
    return instance, rule


def health(rule_id, when: datetime) -> RuleHealthSnapshot:
    return RuleHealthSnapshot(
        rule_id=rule_id, evaluated_at=when, status=RuleHealthStatus.HEALTHY
    )


def coverage(instance_id, when: datetime, technique: str = "T1078") -> DetectionCoverageSnapshot:
    return DetectionCoverageSnapshot(
        instance_id=instance_id,
        technique_id=technique,
        captured_at=when,
        status=CoverageStatus.COVERED,
    )


async def run(session, settings=None):
    return await SnapshotRetention(session, settings=settings or cfg(), clock=lambda: NOW).run()


async def count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_each_table_uses_its_own_cutoff_and_keeps_the_boundary(db_session) -> None:
    instance, rule = await parents(db_session)
    health_cutoff = NOW - timedelta(days=90)
    coverage_cutoff = NOW - timedelta(days=10)
    db_session.add_all(
        [
            health(rule.id, health_cutoff - timedelta(seconds=1)),
            health(rule.id, health_cutoff),
            health(rule.id, NOW),
            coverage(instance.id, coverage_cutoff - timedelta(seconds=1), "T1078"),
            coverage(instance.id, coverage_cutoff, "T1078.001"),
            coverage(instance.id, NOW, "T1078.002"),
        ]
    )
    await db_session.commit()

    report = await run(
        db_session, cfg(detection_coverage_snapshot_retention_days=10)
    )

    assert report.as_dict() == {
        "deleted": {"rule_health_snapshot": 1, "detection_coverage_snapshot": 1},
        "incomplete": [],
        "skipped_reason": None,
    }
    assert await count(db_session, RuleHealthSnapshot) == 2
    assert await count(db_session, DetectionCoverageSnapshot) == 2
    assert await count(db_session, AnalyticsRule) == 1


async def test_a_thirty_day_row_leaves_coverage_and_stays_on_rule_health(db_session) -> None:
    instance, rule = await parents(db_session)
    when = NOW - timedelta(days=30)
    db_session.add_all([health(rule.id, when), coverage(instance.id, when)])
    await db_session.commit()

    report = await run(
        db_session,
        cfg(
            rule_health_snapshot_retention_days=90,
            detection_coverage_snapshot_retention_days=10,
        ),
    )

    assert report.deleted["rule_health_snapshot"] == 0
    assert report.deleted["detection_coverage_snapshot"] == 1
    assert await count(db_session, RuleHealthSnapshot) == 1
    assert await count(db_session, DetectionCoverageSnapshot) == 0


async def test_batch_cap_is_per_table(db_session) -> None:
    instance, rule = await parents(db_session)
    old = NOW - timedelta(days=100)
    db_session.add_all(
        [health(rule.id, old - timedelta(seconds=i)) for i in range(5)]
        + [coverage(instance.id, old, "T1003")]
    )
    await db_session.commit()

    report = await run(
        db_session,
        cfg(snapshot_retention_batch_size=2, snapshot_retention_max_batches=1),
    )

    assert report.deleted == {
        "rule_health_snapshot": 2,
        "detection_coverage_snapshot": 1,
    }
    assert report.incomplete == ["rule_health_snapshot"]
    assert await count(db_session, RuleHealthSnapshot) == 3
    assert await count(db_session, DetectionCoverageSnapshot) == 0
    assert await count(db_session, AnalyticsRule) == 1


async def test_retention_enabled_false_still_deletes(db_session) -> None:
    instance, rule = await parents(db_session)
    db_session.add_all(
        [
            health(rule.id, NOW - timedelta(days=100)),
            coverage(instance.id, NOW - timedelta(days=100)),
        ]
    )
    await db_session.commit()
    settings = cfg()
    assert settings.retention_enabled is False

    report = await run(db_session, settings)

    assert report.deleted["rule_health_snapshot"] == 1
    assert report.deleted["detection_coverage_snapshot"] == 1
    assert report.skipped_reason is None
