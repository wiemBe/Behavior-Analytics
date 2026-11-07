"""SOC-17d suppression gates in the WAF, IPS and property-watch engines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import (
    AlertPolicyMode,
    AlertPolicyScope,
    AnomalyFamily,
    AnomalyState,
    IpsAnomalyType,
    WafAnomalyType,
)
from app.models.ips import IpsAnomaly
from app.models.waf import WafAnomaly
from tests.integration.pw_engine_support import T0 as PW_T0
from tests.integration.pw_engine_support import plan_range
from tests.integration.test_ips_alerting import RecordingEnqueuer
from tests.integration.test_ips_alerting import alert_settings as ips_settings
from tests.integration.test_ips_alerting import alerts as ips_alerts
from tests.integration.test_ips_alerting import evaluate as ips_evaluate
from tests.integration.test_ips_alerting import seed as ips_seed
from tests.integration.test_ips_engine import DAY as IPS_DAY
from tests.integration.test_ips_engine import START as IPS_START
from tests.integration.test_pw_alerting import SPIKE as PW_SPIKE
from tests.integration.test_pw_alerting import alerting_rig, spike
from tests.integration.test_pw_alerting import alerts as pw_alerts
from tests.integration.test_pw_alerting import step as pw_step
from tests.integration.test_waf_alerting import STEP as WAF_STEP
from tests.integration.test_waf_alerting import T0 as WAF_T0
from tests.integration.test_waf_alerting import alert_settings as waf_settings
from tests.integration.test_waf_alerting import evaluate as waf_evaluate
from tests.integration.test_waf_alerting import seed_endpoint

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _suppression(
    *,
    family: AnomalyFamily,
    subject_id,  # type: ignore[no-untyped-def]
    detector: str | None,
    starts_at: datetime,
    ends_at: datetime,
) -> AlertSuppression:
    return AlertSuppression(
        family=family.value,
        subject_id=subject_id,
        detector_type=detector,
        kind="ANALYST",
        reason="known noisy condition",
        owner="analyst",
        starts_at=starts_at,
        ends_at=ends_at,
        created_by="analyst",
    )


def _policy(source_id, family: AnomalyFamily, detector: str) -> AlertPolicy:  # type: ignore[no-untyped-def]
    return AlertPolicy(
        scope_kind=AlertPolicyScope.SOURCE.value,
        scope_value=str(source_id),
        family=family.value,
        detector_type=detector,
        mode=AlertPolicyMode.DIGEST.value,
        updated_by="test",
    )


async def _hits(session) -> list[AlertSuppressionHit]:  # type: ignore[no-untyped-def]
    return list((await session.scalars(select(AlertSuppressionHit))).all())


# --------------------------------------------------------------------- WAF
async def test_waf_zero_suppression_rows_opens_the_existing_alert(db_session) -> None:
    _instance, source, endpoint = await seed_endpoint(db_session)
    await waf_evaluate(db_session, waf_settings(), source, endpoint, WAF_T0, 500)
    assert len(list((await db_session.scalars(select(Alert))).all())) == 1
    assert await _hits(db_session) == []


async def test_waf_suppression_records_one_hit_per_incident_and_refreshes_without_alert(
    db_session,
) -> None:
    _instance, source, endpoint = await seed_endpoint(db_session)
    now = datetime.now(UTC)
    db_session.add(
        _suppression(
            family=AnomalyFamily.WAF,
            subject_id=source.id,
            detector=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
        )
    )
    await db_session.flush()
    dispatcher = RecordingEnqueuer()

    for index, at in enumerate((WAF_T0, WAF_T0 + WAF_STEP)):
        await waf_evaluate(
            db_session, waf_settings(), source, endpoint, at, 400 + index,
            enqueuer=dispatcher,
        )

    incident = await db_session.scalar(select(WafAnomaly))
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert incident.suppressed is False
    assert list((await db_session.scalars(select(Alert))).all()) == []
    assert dispatcher.calls == []
    [hit] = await _hits(db_session)
    assert hit.anomaly_id == incident.id


async def test_waf_exactly_ten_times_breaks_through_and_repeated_buckets_dedup(
    db_session,
) -> None:
    _instance, source, endpoint = await seed_endpoint(db_session)
    now = datetime.now(UTC)
    db_session.add(
        _suppression(
            family=AnomalyFamily.WAF,
            subject_id=source.id,
            detector=None,
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
        )
    )
    await db_session.flush()
    dispatcher = RecordingEnqueuer()

    await waf_evaluate(
        db_session, waf_settings(), source, endpoint, WAF_T0, 1000, enqueuer=dispatcher
    )
    await waf_evaluate(
        db_session,
        waf_settings(),
        source,
        endpoint,
        WAF_T0 + WAF_STEP,
        1100,
        enqueuer=dispatcher,
    )

    [alert] = list((await db_session.scalars(select(Alert))).all())
    assert alert.occurrence_count == 2
    assert len(dispatcher.calls) == 1
    assert await _hits(db_session) == []


async def test_waf_policy_and_family_switch_stay_outside_suppression(db_session) -> None:
    _instance, source, endpoint = await seed_endpoint(db_session)
    now = datetime.now(UTC)
    db_session.add_all([
        _policy(
            source.id, AnomalyFamily.WAF, WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value
        ),
        _suppression(
            family=AnomalyFamily.WAF,
            subject_id=source.id,
            detector=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
        ),
    ])
    await db_session.flush()
    await waf_evaluate(db_session, waf_settings(), source, endpoint, WAF_T0, 1000)
    assert list((await db_session.scalars(select(Alert))).all()) == []
    assert await _hits(db_session) == []


async def test_waf_family_switch_off_writes_no_suppression_hit(db_session) -> None:
    _instance, source, endpoint = await seed_endpoint(db_session)
    now = datetime.now(UTC)
    db_session.add(
        _suppression(
            family=AnomalyFamily.WAF,
            subject_id=source.id,
            detector=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
        )
    )
    await db_session.flush()
    await waf_evaluate(
        db_session,
        waf_settings(waf_alerting_enabled=False),
        source,
        endpoint,
        WAF_T0,
        1000,
    )
    assert list((await db_session.scalars(select(Alert))).all()) == []
    assert await _hits(db_session) == []


# --------------------------------------------------------------------- IPS
def _ips_suppression(source_id, detector: str | None) -> AlertSuppression:  # type: ignore[no-untyped-def]
    return _suppression(
        family=AnomalyFamily.IPS,
        subject_id=source_id,
        detector=detector,
        starts_at=IPS_START - timedelta(hours=1),
        ends_at=IPS_START + timedelta(days=7),
    )


async def test_ips_zero_suppression_rows_opens_the_existing_alert(db_session) -> None:
    _instance, source, entity = await ips_seed(db_session)
    await ips_evaluate(db_session, ips_settings(), source, IPS_START, entity, 600)
    assert len(await ips_alerts(db_session)) == 1
    assert await _hits(db_session) == []


async def test_ips_entity_anomaly_maps_to_its_log_source_and_hits_once(db_session) -> None:
    _instance, source, entity = await ips_seed(db_session)
    db_session.add(_ips_suppression(source.id, IpsAnomalyType.IPS_PORT_RATE_SPIKE.value))
    await db_session.flush()
    dispatcher = RecordingEnqueuer()

    await ips_evaluate(
        db_session, ips_settings(), source, IPS_START, entity, 600, enqueuer=dispatcher
    )
    await ips_evaluate(
        db_session,
        ips_settings(),
        source,
        IPS_START + IPS_DAY,
        entity,
        700,
        enqueuer=dispatcher,
    )

    incident = await db_session.scalar(select(IpsAnomaly))
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert incident.suppressed is False
    assert await ips_alerts(db_session) == []
    assert dispatcher.calls == []
    [hit] = await _hits(db_session)
    assert hit.anomaly_id == incident.id


async def test_ips_exactly_ten_times_breaks_through_and_repeated_buckets_dedup(
    db_session,
) -> None:
    _instance, source, entity = await ips_seed(db_session)
    db_session.add(_ips_suppression(source.id, None))
    await db_session.flush()
    dispatcher = RecordingEnqueuer()

    await ips_evaluate(
        db_session, ips_settings(), source, IPS_START, entity, 1000, enqueuer=dispatcher
    )
    await ips_evaluate(
        db_session,
        ips_settings(),
        source,
        IPS_START + IPS_DAY,
        entity,
        1100,
        enqueuer=dispatcher,
    )

    [alert] = await ips_alerts(db_session)
    assert alert.occurrence_count == 2
    assert len(dispatcher.calls) == 1
    assert await _hits(db_session) == []


async def test_ips_policy_stop_writes_no_suppression_hit(db_session) -> None:
    _instance, source, entity = await ips_seed(db_session)
    db_session.add_all([
        _policy(source.id, AnomalyFamily.IPS, IpsAnomalyType.IPS_PORT_RATE_SPIKE.value),
        _ips_suppression(source.id, IpsAnomalyType.IPS_PORT_RATE_SPIKE.value),
    ])
    await db_session.flush()
    await ips_evaluate(db_session, ips_settings(), source, IPS_START, entity, 1000)
    assert await ips_alerts(db_session) == []
    assert await _hits(db_session) == []


async def test_ips_family_switch_off_writes_no_suppression_hit(db_session) -> None:
    _instance, source, entity = await ips_seed(db_session)
    db_session.add(_ips_suppression(source.id, IpsAnomalyType.IPS_PORT_RATE_SPIKE.value))
    await db_session.flush()
    await ips_evaluate(
        db_session,
        ips_settings(ips_alerting_enabled=False),
        source,
        IPS_START,
        entity,
        1000,
    )
    assert await ips_alerts(db_session) == []
    assert await _hits(db_session) == []


# ----------------------------------------------------------- property watch
def _pw_suppression(watch_id, detector: str | None = PW_SPIKE.value) -> AlertSuppression:  # type: ignore[no-untyped-def]
    return _suppression(
        family=AnomalyFamily.PROPERTY_WATCH,
        subject_id=watch_id,
        detector=detector,
        starts_at=PW_T0 - timedelta(minutes=1),
        ends_at=PW_T0 + timedelta(days=7),
    )


async def test_pw_zero_suppression_rows_opens_the_existing_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig)
    for index in range(3):
        await pw_step(rig, index)
    assert len(await pw_alerts(rig)) == 1
    assert await _hits(db_session) == []


async def test_pw_suppression_records_one_hit_per_incident_without_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    db_session.add(_pw_suppression(rig.watch.id))
    await db_session.commit()
    spike(rig)
    dispatcher = RecordingEnqueuer()
    for index in range(4):
        await pw_step(rig, index, enqueuer=dispatcher)

    [incident] = await rig.anomalies(PW_SPIKE)
    assert incident.state == AnomalyState.OPEN
    assert incident.suppressed is False
    assert await pw_alerts(rig) == []
    assert dispatcher.calls == []
    [hit] = await _hits(db_session)
    assert hit.anomaly_id == incident.id


async def test_pw_exactly_ten_times_breaks_through_and_repeated_buckets_dedup(
    db_session,
) -> None:
    rig = await alerting_rig(db_session)
    db_session.add(_pw_suppression(rig.watch.id, None))
    await db_session.commit()
    plan_range(rig.plan, 1, 5, {"alice": 1000, "bob": 400})
    dispatcher = RecordingEnqueuer()
    for index in range(5):
        await pw_step(rig, index, enqueuer=dispatcher)

    [alert] = await pw_alerts(rig)
    assert alert.occurrence_count == 3
    assert len(dispatcher.calls) == 1
    assert await _hits(db_session) == []


async def test_pw_policy_stop_writes_no_suppression_hit(db_session) -> None:
    rig = await alerting_rig(db_session)
    db_session.add_all([
        _policy(rig.watch.id, AnomalyFamily.PROPERTY_WATCH, PW_SPIKE.value),
        _pw_suppression(rig.watch.id),
    ])
    await db_session.commit()
    spike(rig)
    for index in range(4):
        await pw_step(rig, index)
    assert await pw_alerts(rig) == []
    assert await _hits(db_session) == []


async def test_pw_family_switch_off_writes_no_suppression_hit(db_session) -> None:
    rig = await alerting_rig(db_session, alerting=False)
    db_session.add(_pw_suppression(rig.watch.id))
    await db_session.commit()
    spike(rig)
    for index in range(4):
        await pw_step(rig, index)
    assert await pw_alerts(rig) == []
    assert await _hits(db_session) == []
