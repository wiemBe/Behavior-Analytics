"""SOC-17c: the alert policy stops WAF, IPS and property-watch alerts only.

Each family's gate sits in ``_apply_alert_actions``, after the incident is
recorded. With no policy rows every family behaves as before; a RECORD_ONLY or
DIGEST row removes the alert and its notification but not the incident, and a
live alert still resolves after the policy changes. The family switch stays the
outer gate. DB-gated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.alert_policy import AlertPolicy
from app.models.enums import (
    AlertPolicyMode,
    AlertPolicyScope,
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    IpsAnomalyType,
    WafAnomalyType,
)
from app.models.ips import IpsAnomaly
from app.models.waf import WafAnomaly
from tests.integration.test_ips_alerting import (
    alert_settings as ips_settings,
)
from tests.integration.test_ips_alerting import alerts as ips_alerts
from tests.integration.test_ips_alerting import evaluate as ips_evaluate
from tests.integration.test_ips_alerting import seed as ips_seed
from tests.integration.test_ips_engine import START as IPS_START
from tests.integration.test_pw_alerting import (
    SPIKE,
    alerting_rig,
    spike,
)
from tests.integration.test_pw_alerting import (
    alerts as pw_alerts,
)
from tests.integration.test_pw_alerting import (
    step as pw_step,
)
from tests.integration.test_waf_alerting import STEP, T0, seed_endpoint
from tests.integration.test_waf_alerting import alert_settings as waf_settings
from tests.integration.test_waf_alerting import evaluate as waf_evaluate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _policy(
    *,
    instance_id,  # type: ignore[no-untyped-def]
    family: AnomalyFamily,
    source_id,  # type: ignore[no-untyped-def]
    detector: str,
    mode: AlertPolicyMode,
) -> AlertPolicy:
    return AlertPolicy(
        instance_id=instance_id,
        scope_kind=AlertPolicyScope.SOURCE.value,
        scope_value=str(source_id),
        family=family.value,
        detector_type=detector,
        mode=mode,
        updated_by="test",
    )


# --------------------------------------------------------------------- WAF
async def test_waf_with_zero_rows_opens_its_alert(db_session) -> None:
    settings = waf_settings()
    _instance, source, endpoint = await seed_endpoint(db_session)
    await waf_evaluate(db_session, settings, source, endpoint, T0, 500)
    assert len(list((await db_session.scalars(select(Alert))).all())) == 1


@pytest.mark.parametrize("mode", [AlertPolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST])
async def test_waf_policy_records_the_incident_without_the_alert(
    db_session, mode: AlertPolicyMode
) -> None:
    settings = waf_settings()
    instance, source, endpoint = await seed_endpoint(db_session)
    db_session.add(
        _policy(
            instance_id=instance.id,
            family=AnomalyFamily.WAF,
            source_id=source.id,
            detector=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
            mode=mode,
        )
    )
    await db_session.flush()

    await waf_evaluate(db_session, settings, source, endpoint, T0, 500)

    incident = await db_session.scalar(select(WafAnomaly))
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert incident.detector_type == WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value
    assert list((await db_session.scalars(select(Alert))).all()) == []


async def test_waf_alert_still_resolves_after_the_policy_turns_record_only(
    db_session,
) -> None:
    settings = waf_settings()
    instance, source, endpoint = await seed_endpoint(db_session)
    await waf_evaluate(db_session, settings, source, endpoint, T0, 500)
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and alert.status == AlertStatus.OPEN

    db_session.add(
        _policy(
            instance_id=instance.id,
            family=AnomalyFamily.WAF,
            source_id=source.id,
            detector=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
            mode=AlertPolicyMode.RECORD_ONLY,
        )
    )
    await db_session.flush()
    await waf_evaluate(db_session, settings, source, endpoint, T0 + STEP, 100)

    await db_session.refresh(alert)
    assert alert.status == AlertStatus.RESOLVED


async def test_waf_family_switch_off_wins_over_an_alert_policy(db_session) -> None:
    settings = waf_settings(waf_alerting_enabled=False)
    instance, source, endpoint = await seed_endpoint(db_session)
    db_session.add(
        _policy(
            instance_id=instance.id,
            family=AnomalyFamily.WAF,
            source_id=source.id,
            detector=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
            mode=AlertPolicyMode.ALERT,
        )
    )
    await db_session.flush()
    await waf_evaluate(db_session, settings, source, endpoint, T0, 500)
    assert list((await db_session.scalars(select(Alert))).all()) == []


# --------------------------------------------------------------------- IPS
@pytest.mark.parametrize("mode", [AlertPolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST])
async def test_ips_policy_records_the_incident_without_the_alert(
    db_session, mode: AlertPolicyMode
) -> None:
    settings = ips_settings()
    instance, source, entity = await ips_seed(db_session)
    db_session.add(
        _policy(
            instance_id=instance.id,
            family=AnomalyFamily.IPS,
            source_id=source.id,
            detector=IpsAnomalyType.IPS_PORT_RATE_SPIKE.value,
            mode=mode,
        )
    )
    await db_session.flush()

    await ips_evaluate(db_session, settings, source, IPS_START, entity, 600)

    incident = await db_session.scalar(select(IpsAnomaly))
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert incident.detector_type == IpsAnomalyType.IPS_PORT_RATE_SPIKE.value
    assert await ips_alerts(db_session) == []


async def test_ips_family_switch_off_wins_over_an_alert_policy(db_session) -> None:
    settings = ips_settings(ips_alerting_enabled=False)
    instance, source, entity = await ips_seed(db_session)
    db_session.add(
        _policy(
            instance_id=instance.id,
            family=AnomalyFamily.IPS,
            source_id=source.id,
            detector=IpsAnomalyType.IPS_PORT_RATE_SPIKE.value,
            mode=AlertPolicyMode.ALERT,
        )
    )
    await db_session.flush()
    await ips_evaluate(db_session, settings, source, IPS_START, entity, 600)
    assert await ips_alerts(db_session) == []


async def test_ips_alert_still_resolves_after_the_policy_turns_record_only(
    db_session,
) -> None:
    from datetime import timedelta

    settings = ips_settings()
    instance, source, entity = await ips_seed(db_session)
    await ips_evaluate(db_session, settings, source, IPS_START, entity, 600)
    [alert] = await ips_alerts(db_session)
    assert alert.status == AlertStatus.OPEN

    db_session.add(
        _policy(
            instance_id=instance.id,
            family=AnomalyFamily.IPS,
            source_id=source.id,
            detector=IpsAnomalyType.IPS_PORT_RATE_SPIKE.value,
            mode=AlertPolicyMode.RECORD_ONLY,
        )
    )
    await db_session.flush()
    await ips_evaluate(
        db_session, settings, source, IPS_START + timedelta(days=1), entity, 100
    )
    await db_session.refresh(alert)
    assert alert.status == AlertStatus.RESOLVED


# ----------------------------------------------------------- property watch
@pytest.mark.parametrize("mode", [AlertPolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST])
async def test_pw_policy_records_the_incident_without_the_alert(
    db_session, mode: AlertPolicyMode
) -> None:
    rig = await alerting_rig(db_session)
    db_session.add(
        _policy(
            instance_id=rig.instance.id,
            family=AnomalyFamily.PROPERTY_WATCH,
            source_id=rig.watch.id,
            detector=SPIKE.value,
            mode=mode,
        )
    )
    await db_session.commit()
    spike(rig)
    for index in range(0, 4):
        await pw_step(rig, index)

    incidents = await rig.anomalies(SPIKE)
    assert incidents and incidents[0].state == AnomalyState.OPEN
    assert await pw_alerts(rig) == []


async def test_pw_family_switch_off_wins_over_an_alert_policy(db_session) -> None:
    rig = await alerting_rig(db_session, alerting=False)
    db_session.add(
        _policy(
            instance_id=rig.instance.id,
            family=AnomalyFamily.PROPERTY_WATCH,
            source_id=rig.watch.id,
            detector=SPIKE.value,
            mode=AlertPolicyMode.ALERT,
        )
    )
    await db_session.commit()
    spike(rig)
    for index in range(0, 4):
        await pw_step(rig, index)
    assert await pw_alerts(rig) == []


async def test_pw_alert_still_resolves_after_the_policy_turns_record_only(
    db_session,
) -> None:
    rig = await alerting_rig(db_session)
    spike(rig)
    for index in range(0, 3):
        await pw_step(rig, index)
    [alert] = await pw_alerts(rig)
    assert alert.status == AlertStatus.OPEN

    db_session.add(
        _policy(
            instance_id=rig.instance.id,
            family=AnomalyFamily.PROPERTY_WATCH,
            source_id=rig.watch.id,
            detector=SPIKE.value,
            mode=AlertPolicyMode.RECORD_ONLY,
        )
    )
    await db_session.commit()
    for index in range(3, 7):
        await pw_step(rig, index)
    [alert] = await pw_alerts(rig)
    assert alert.status == AlertStatus.RESOLVED
