"""SOC-1a: retention removes the triage row of an anomaly it deleted.

The triage table has no foreign key (one column cannot point at four anomaly tables), so
each anomaly retention task owns the consequence. This test drives every one of the four
services, not the shared helper alone: a wrong family literal in one call site would
otherwise leave that family's triage rows orphaned forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsAnomalyType,
    PwAnomalyType,
    PwScopeKind,
    Severity,
    TriageStatus,
    WafAnomalyType,
)
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.models.waf import WafAnomaly, WafEndpoint
from app.services.ips_anomaly_retention import IpsAnomalyRetention
from app.services.log_source_anomaly_retention import LogSourceAnomalyRetention
from app.services.pw_retention import PwRetention
from app.services.waf_retention import WafRetention
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=11)


def cfg(**extra: object):
    return get_settings().model_copy(update=extra)


async def _seed(session, family: AnomalyFamily) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    """One terminal, old anomaly of `family` and one live one, each with triage."""
    inst = await make_instance(session)
    source = await make_log_source(
        session, inst, qradar_id=7000 + len(family.value), name=f"{family.value}-ret"
    )
    stale_id = uuid.uuid4()
    live_id = uuid.uuid4()
    if family is AnomalyFamily.LOG_SOURCE:
        session.add_all(
            [
                LogSourceAnomaly(
                    id=stale_id,
                    log_source_id=source.id,
                    anomaly_type=AnomalyType.VOLUME_SPIKE,
                    severity=Severity.MEDIUM,
                    state=AnomalyState.RESOLVED,
                    detected_at=OLD,
                    resolved_at=OLD,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
                LogSourceAnomaly(
                    id=live_id,
                    log_source_id=source.id,
                    anomaly_type=AnomalyType.VOLUME_SPIKE,
                    severity=Severity.MEDIUM,
                    state=AnomalyState.OPEN,
                    detected_at=NOW,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
            ]
        )
    elif family is AnomalyFamily.WAF:
        endpoint = WafEndpoint(
            instance_id=inst.id,
            log_source_id=source.id,
            normalized_endpoint="/ret",
            http_method="GET",
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            first_seen_at=OLD,
            last_seen_at=NOW,
            tracked_since=OLD,
            is_active=True,
        )
        session.add(endpoint)
        await session.flush()
        session.add_all(
            [
                WafAnomaly(
                    id=stale_id,
                    waf_endpoint_id=endpoint.id,
                    detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
                    severity=Severity.MEDIUM,
                    state=AnomalyState.RESOLVED,
                    detected_at=OLD,
                    resolved_at=OLD,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
                WafAnomaly(
                    id=live_id,
                    waf_endpoint_id=endpoint.id,
                    detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
                    severity=Severity.MEDIUM,
                    state=AnomalyState.OPEN,
                    detected_at=NOW,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
            ]
        )
    elif family is AnomalyFamily.IPS:
        session.add_all(
            [
                IpsAnomaly(
                    id=stale_id,
                    subject_kind="IPS_SOURCE",
                    log_source_id=source.id,
                    detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
                    fingerprint=f"ips-stale-{stale_id.hex}",
                    severity=Severity.MEDIUM,
                    state=AnomalyState.RESOLVED,
                    detected_at=OLD,
                    resolved_at=OLD,
                    profile_version=1,
                    normalizer_version=1,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
                IpsAnomaly(
                    id=live_id,
                    subject_kind="IPS_SOURCE",
                    log_source_id=source.id,
                    detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
                    fingerprint=f"ips-live-{live_id.hex}",
                    severity=Severity.MEDIUM,
                    state=AnomalyState.OPEN,
                    detected_at=NOW,
                    profile_version=1,
                    normalizer_version=1,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
            ]
        )
    else:
        watch = PropertyWatch(
            instance_id=inst.id,
            name=f"watch-ret-{uuid.uuid4().hex[:8]}",
            scope_kind=PwScopeKind.LOG_SOURCE,
            log_source_id=source.id,
            properties=[{"kind": "NORMALIZED", "name": "URL"}],
            detectors=[PwAnomalyType.PW_VALUE_SPIKE.value],
        )
        session.add(watch)
        await session.flush()
        session.add_all(
            [
                PwAnomaly(
                    id=stale_id,
                    watch_id=watch.id,
                    value_key="/ret",
                    detector_type=PwAnomalyType.PW_VALUE_SPIKE,
                    fingerprint=f"pw-stale-{stale_id.hex}",
                    severity=Severity.LOW,
                    state=AnomalyState.RESOLVED,
                    detected_at=OLD,
                    resolved_at=OLD,
                    watch_version=1,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
                PwAnomaly(
                    id=live_id,
                    watch_id=watch.id,
                    value_key="/ret",
                    detector_type=PwAnomalyType.PW_VALUE_SPIKE,
                    fingerprint=f"pw-live-{live_id.hex}",
                    severity=Severity.LOW,
                    state=AnomalyState.OPEN,
                    detected_at=NOW,
                    watch_version=1,
                    details={},
                    evidence_status=EvidenceStatus.NOT_REQUESTED,
                ),
            ]
        )

    for anomaly_id in (stale_id, live_id):
        row = AnomalyTriage(
            family=family,
            anomaly_id=anomaly_id,
            status=TriageStatus.NEW,
            updated_by="seed",
            version=1,
        )
        session.add(row)
        await session.flush()
        session.add(
            AnomalyTriageEvent(
                triage_id=row.id,
                kind="COMMENT",
                actor="seed",
                comment="retention test",
                triage_version=1,
            )
        )
    await session.flush()
    return {"stale": stale_id, "live": live_id}


CASES = [
    (
        AnomalyFamily.LOG_SOURCE,
        LogSourceAnomalyRetention,
        lambda: cfg(
            log_source_anomaly_retention_days=10,
            log_source_anomaly_retention_batch_size=100,
            log_source_anomaly_retention_max_batches=20,
        ),
    ),
    (
        AnomalyFamily.WAF,
        WafRetention,
        lambda: cfg(
            waf_retention_enabled=True,
            waf_metric_retention_days=35,
            waf_anomaly_retention_days=10,
            waf_retention_batch_size=100,
            waf_retention_max_batches=20,
        ),
    ),
    (
        AnomalyFamily.IPS,
        IpsAnomalyRetention,
        lambda: cfg(
            ips_anomaly_retention_days=10,
            ips_anomaly_retention_batch_size=100,
            ips_anomaly_retention_max_batches=20,
        ),
    ),
    (
        AnomalyFamily.PROPERTY_WATCH,
        PwRetention,
        lambda: cfg(
            pw_metric_retention_days=35,
            pw_anomaly_retention_days=10,
            pw_retention_batch_size=100,
            pw_retention_max_batches=20,
        ),
    ),
]


@pytest.mark.parametrize(("family", "service", "settings"), CASES)
async def test_the_triage_row_leaves_with_its_anomaly(
    db_session, family: AnomalyFamily, service, settings
) -> None:  # type: ignore[no-untyped-def]
    ids = await _seed(db_session, family)
    report = await service(db_session, settings=settings(), clock=lambda: NOW).run()

    remaining = set(
        (
            await db_session.scalars(
                select(AnomalyTriage.anomaly_id).where(AnomalyTriage.family == family)
            )
        ).all()
    )
    assert remaining == {ids["live"]}, "a triage row outlived its anomaly"
    assert report.deleted.get("anomaly_triage") == 1

    # The stale anomaly itself is gone; the live one is not.
    model = {
        AnomalyFamily.LOG_SOURCE: LogSourceAnomaly,
        AnomalyFamily.WAF: WafAnomaly,
        AnomalyFamily.IPS: IpsAnomaly,
        AnomalyFamily.PROPERTY_WATCH: PwAnomaly,
    }[family]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(model).where(model.id == ids["stale"])
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(model).where(model.id == ids["live"])
        )
        == 1
    )

    # The stale row's events left by cascade; the live row's stayed.
    events = await db_session.scalar(select(func.count()).select_from(AnomalyTriageEvent))
    assert events == 1
