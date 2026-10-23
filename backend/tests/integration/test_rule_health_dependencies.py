"""Unknown log-source-type dependencies are insufficient, not healthy.

A type whose every source was skipped (maintenance or monitoring off) is
absent from the health map. That used to be stored as healthy.
"""

from __future__ import annotations

import pytest

from app.models.enums import MappingSource, RuleDependencyKind, RuleHealthStatus
from app.models.rule import RuleDependency
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_rule_health_coverage import (
    NOW,
    evaluator,
    make_rule,
    snapshots_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _depend(session, rule, *, type_id: str, name: str) -> None:
    session.add(
        RuleDependency(
            rule_id=rule.id,
            kind=RuleDependencyKind.LOG_SOURCE_TYPE,
            target_ref=type_id,
            target_name=name,
            source=MappingSource.EXPLICIT,
            confidence=1.0,
        )
    )
    await session.flush()


async def test_dependency_map_gaps_are_not_healthy(db_session) -> None:
    inst = await make_instance(db_session)
    await make_log_source(
        db_session, inst, qradar_id=1201, name="unmonitored", type_id=12,
        monitoring_enabled=False,
    )
    await make_log_source(
        db_session, inst, qradar_id=1202, name="maintenance", type_id=12,
        maintenance_mode=True, maintenance_until=None,
    )
    await make_log_source(
        db_session, inst, qradar_id=1901, name="healthy-linux", type_id=99,
        last_event_time=NOW,
    )
    await make_log_source(
        db_session, inst, qradar_id=1701, name="quiet", type_id=7,
        last_event_time=None,
    )
    await make_log_source(
        db_session, inst, qradar_id=1801, name="skipped-mixed", type_id=8,
        monitoring_enabled=False,
    )
    await make_log_source(
        db_session, inst, qradar_id=1802, name="unhealthy-mixed", type_id=8,
        last_event_time=None,
    )
    await make_log_source(
        db_session, inst, qradar_id=1902, name="skipped-with-healthy", type_id=9,
        monitoring_enabled=False,
    )
    await make_log_source(
        db_session, inst, qradar_id=1903, name="healthy-mixed", type_id=9,
        last_event_time=NOW,
    )

    skipped = await make_rule(db_session, inst, qradar_id=201, name="skipped-type")
    healthy = await make_rule(
        db_session, inst, qradar_id=202, name="healthy-type", last_fired_at=NOW
    )
    quiet = await make_rule(db_session, inst, qradar_id=203, name="quiet-type")
    mixed_bad = await make_rule(db_session, inst, qradar_id=204, name="mixed-bad")
    mixed_good = await make_rule(
        db_session, inst, qradar_id=205, name="mixed-good", last_fired_at=NOW
    )
    await _depend(db_session, skipped, type_id="12", name="Linux OS")
    await _depend(db_session, healthy, type_id="99", name="Windows")
    await _depend(db_session, quiet, type_id="7", name="Firewall")
    await _depend(db_session, mixed_bad, type_id="8", name="Proxy")
    await _depend(db_session, mixed_good, type_id="9", name="Mail")

    await evaluator(db_session, rule_health_flap_threshold=1).evaluate_instance(inst)

    skipped_snap = (await snapshots_for(db_session, skipped))[-1]
    assert skipped_snap.status == RuleHealthStatus.INSUFFICIENT_DATA
    assert skipped.health_status == RuleHealthStatus.INSUFFICIENT_DATA
    assert "Linux OS" in skipped_snap.reason
    assert "not been observed" in skipped_snap.reason
    assert skipped_snap.required_log_sources_healthy is None

    healthy_snap = (await snapshots_for(db_session, healthy))[-1]
    assert healthy_snap.status == RuleHealthStatus.HEALTHY
    assert healthy.health_status == RuleHealthStatus.HEALTHY
    assert healthy_snap.required_log_sources_healthy is True

    quiet_snap = (await snapshots_for(db_session, quiet))[-1]
    assert quiet_snap.status == RuleHealthStatus.DEPENDENCY_DEGRADED
    assert "not delivering events" in quiet_snap.reason
    assert quiet_snap.required_log_sources_healthy is False

    mixed_bad_snap = (await snapshots_for(db_session, mixed_bad))[-1]
    assert mixed_bad_snap.status == RuleHealthStatus.DEPENDENCY_DEGRADED
    assert mixed_bad_snap.required_log_sources_healthy is False

    mixed_good_snap = (await snapshots_for(db_session, mixed_good))[-1]
    assert mixed_good_snap.status == RuleHealthStatus.HEALTHY
    assert mixed_good_snap.required_log_sources_healthy is True
