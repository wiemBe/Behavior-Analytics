"""IPS incidents drive the generic alert lifecycle, behind `ips_alerting_enabled`.

Mirrors tests/integration/test_waf_alerting.py and test_waf_alert_source_class.py:
shadow mode writes nothing, an open raises one alert on the incident's fixed
fingerprint, a still-anomalous bucket refreshes it, a recovery resolves it, and
the alert carries the entity's stored identity and nothing raw.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import ips_anomaly_fingerprint
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.anomaly.evidence import AnomalyEvidence
from app.ips.detector import IpsVerdict
from app.ips.engine import IpsAnomalyEngine
from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    ClassificationReviewStatus,
    DetectorSignal,
    IpsAnomalyType,
    IpsDimension,
    NotificationChannel,
    Severity,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.ips import IpsAnomaly, IpsSourceBucket
from tests.integration.factories import make_log_source
from tests.integration.test_ips_baseline import add_bucket, add_source
from tests.integration.test_ips_engine import (
    DAY,
    PORT,
    START,
    add_entity,
    add_entity_cell,
    add_metric,
    add_share_cell,
    engine_settings,
)
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CONCENTRATION = IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE


def alert_settings(**overrides: object):
    values: dict[str, object] = {
        "ips_alerting_enabled": True,
        "anomaly_open_after_intervals": 1,
        "anomaly_resolve_after_intervals": 1,
    }
    values.update(overrides)
    return engine_settings(**values)


class RecordingEnqueuer:
    """The enqueuer test double: records every (alert, transition) pair."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def enqueue(self, alert, transition):  # type: ignore[no-untyped-def]
        self.calls.append((alert.fingerprint, str(transition)))
        return []


def routing_policy(**scope: object) -> RoutingPolicy:
    return RoutingPolicy(
        rules=[
            RoutingRule(
                channel=NotificationChannel.SLACK,
                target="ips-route",
                alert_types=frozenset({"ips_anomaly"}),
                **scope,  # type: ignore[arg-type]
            ),
            RoutingRule(
                channel=NotificationChannel.WEBHOOK,
                target="waf-only",
                alert_types=frozenset({"waf_anomaly"}),
            ),
        ]
    )


async def seed(session, *, cells: int = 4, key: str = "3389/tcp"):  # type: ignore[no-untyped-def]
    instance, source, _profile = await add_source(session)
    entity = await add_entity(session, instance, source, key=key)
    for offset in range(cells):
        await add_entity_cell(session, entity, START + DAY * offset)
    return instance, source, entity


async def evaluate(  # type: ignore[no-untyped-def]
    session,
    settings,
    source,
    at,
    entity=None,
    events=None,
    *,
    enqueuer=None,
    maintenance: bool = False,
    detectors=None,
    **bucket_overrides,
):
    await add_bucket(session, source.id, at, in_maintenance=maintenance, **bucket_overrides)
    if entity is not None and events is not None:
        await add_metric(session, entity, at, events)
    bucket = await session.get(IpsSourceBucket, (source.id, at))
    engine = IpsAnomalyEngine(
        session,
        settings=settings,
        enqueuer=enqueuer,
        detectors=detectors,
        clock=lambda: at + timedelta(hours=1),
    )
    return await engine.evaluate_bucket(source, bucket)


async def alerts(session) -> list[Alert]:  # type: ignore[no-untyped-def]
    return list((await session.scalars(select(Alert).order_by(Alert.opened_at))).all())


async def notification_count(session) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(AlertNotification)) or 0)


def port_fingerprint(instance, source, key: str = "3389/tcp") -> str:
    return ips_anomaly_fingerprint(
        instance_id=instance.id,
        log_source_id=source.id,
        detector_type=PORT.value,
        dimension=IpsDimension.DESTINATION_PORT.value,
        value_key=key,
    )


async def test_open_refresh_resolve_and_recur(db_session) -> None:
    settings = alert_settings()
    instance, source, entity = await seed(db_session)
    enqueuer = RecordingEnqueuer()
    await evaluate(db_session, settings, source, START, entity, 600, enqueuer=enqueuer)
    rows = await alerts(db_session)
    assert len(rows) == 1
    first = rows[0]
    assert first.fingerprint == port_fingerprint(instance, source)
    incident = await db_session.scalar(select(IpsAnomaly))
    assert incident is not None and incident.fingerprint == first.fingerprint
    assert first.title == "IPS_PORT_RATE_SPIKE on ips-412: destination port 3389/tcp"
    assert first.source_type == "ips_entity" and first.source_id == entity.id
    assert first.severity == incident.severity
    assert first.description == first.evidence_snapshot["reason"]
    assert first.source_anomaly_ids == [str(incident.id)]
    assert first.context == {
        "log_source": "ips-412",
        "owner": None,
        "criticality": "MEDIUM",
        "instance": instance.name,
        "detector_type": "IPS_PORT_RATE_SPIKE",
        "dimension": "DESTINATION_PORT",
        "entity": "3389/tcp",
        "observed": 600.0,
        "expected": 100.0,
        "ratio": first.context["ratio"],
    }
    assert first.context["ratio"] == pytest.approx(6.0)
    assert len(enqueuer.calls) == 1

    # A further anomalous bucket refreshes the same alert: no second row, no transition.
    await evaluate(db_session, settings, source, START + DAY, entity, 700, enqueuer=enqueuer)
    assert len(await alerts(db_session)) == 1
    assert first.occurrence_count == 2
    assert len(enqueuer.calls) == 1

    # Recovery resolves it by the same fingerprint, with the engine as actor.
    await evaluate(db_session, settings, source, START + DAY * 2, entity, 100, enqueuer=enqueuer)
    assert first.status == AlertStatus.RESOLVED
    assert len(enqueuer.calls) == 2
    assert enqueuer.calls[1][0] == first.fingerprint

    # A recurrence opens a new alert lifecycle, not a revived closed alert.
    await evaluate(db_session, settings, source, START + DAY * 3, entity, 700, enqueuer=enqueuer)
    rows = await alerts(db_session)
    assert len(rows) == 2
    assert rows[1].id != first.id and rows[1].status == AlertStatus.OPEN
    assert rows[1].fingerprint == first.fingerprint
    assert len(enqueuer.calls) == 3


async def test_notifications_are_enqueued_once_per_transition(db_session) -> None:
    settings = alert_settings()
    _instance, source, entity = await seed(db_session)
    dispatcher = NotificationDispatcher(db_session, {}, routing_policy(), settings=settings)

    steps = ((0, 600, 1), (1, 650, 1), (2, 100, 2), (3, 700, 3))
    for offset, events, expected in steps:
        await evaluate(
            db_session, settings, source, START + DAY * offset, entity, events,
            enqueuer=dispatcher,
        )
        # One notification on open, none on refresh, one on resolve, one on the recurrence;
        # the waf-only route never matches an IPS alert.
        assert await notification_count(db_session) == expected
    targets = (await db_session.scalars(select(AlertNotification.target))).all()
    assert set(targets) == {"ips-route"}


async def test_shadow_mode_writes_no_alert_and_adds_no_statement(db_session) -> None:
    off = alert_settings(ips_alerting_enabled=False)
    on = alert_settings()
    instance, source, entity = await seed(db_session)
    other = await make_log_source(db_session, instance, qradar_id=413, name="twin")
    twin = await add_entity(db_session, instance, other, key="3389/tcp")
    for offset in range(4):
        await add_entity_cell(db_session, twin, START + DAY * offset)
    dispatcher = NotificationDispatcher(db_session, {}, routing_policy(), settings=off)

    counts: dict[str, list[str]] = {}
    for label, subject_source, subject_entity, configured in (
        ("off", source, entity, off),
        ("on", other, twin, on),
    ):
        with recorded_statements(db_session) as statements:
            # Open, refresh, then resolve: every alerting path is exercised.
            for offset, events in ((0, 600), (1, 650), (2, 100)):
                await evaluate(
                    db_session, configured, subject_source, START + DAY * offset,
                    subject_entity, events, enqueuer=dispatcher,
                )
        counts[label] = statements

    rows = await alerts(db_session)
    assert [alert.context["log_source"] for alert in rows] == ["twin"]
    assert await notification_count(db_session) > 0
    def alert_path(statement: str) -> bool:
        lowered = statement.lower()
        # AlertService.apply_bulk wraps each outcome in a savepoint.
        return "savepoint" in lowered or any(
            token in lowered for token in ("alert", "log_source_classification", "qradar_instance")
        )

    assert not any(alert_path(s) for s in counts["off"])
    # The shadow run is the alerting run minus everything the alert path adds.
    added = [s for s in counts["on"] if alert_path(s)]
    assert added
    assert len(counts["off"]) == len(counts["on"]) - len(added)


async def test_shadow_mode_still_records_the_incident(db_session) -> None:
    settings = alert_settings(ips_alerting_enabled=False)
    _instance, source, entity = await seed(db_session)
    await evaluate(db_session, settings, source, START, entity, 600)
    await evaluate(db_session, settings, source, START + DAY, entity, 100)
    incident = await db_session.scalar(select(IpsAnomaly))
    assert incident is not None and incident.state == AnomalyState.RESOLVED
    assert await alerts(db_session) == []
    assert await notification_count(db_session) == 0


async def test_default_setting_is_off() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["ips_alerting_enabled"].default is False


async def test_maintenance_neither_opens_nor_refreshes(db_session) -> None:
    settings = alert_settings()
    _instance, source, entity = await seed(db_session)
    await evaluate(db_session, settings, source, START, entity, 600)
    alert = (await alerts(db_session))[0]
    assert alert.occurrence_count == 1

    await evaluate(db_session, settings, source, START + DAY, entity, 700, maintenance=True)
    assert alert.occurrence_count == 1

    # A fresh spike recorded in maintenance opens nothing.
    _instance2, other, other_entity = await seed(db_session)
    await evaluate(db_session, settings, other, START, other_entity, 600, maintenance=True)
    assert len(await alerts(db_session)) == 1


def always_anomalous(ctx) -> IpsVerdict:  # type: ignore[no-untyped-def]
    """A detector that ignores maintenance, so only the engine's own guards hold."""
    bucket = ctx.observation.bucket
    return IpsVerdict(
        detector_type=PORT,
        evidence=AnomalyEvidence(
            anomaly_type=PORT,
            signal=DetectorSignal.ANOMALOUS,
            severity=Severity.HIGH,
            observed_value=900.0,
            expected_value=100.0,
            baseline_low=80.0,
            baseline_high=120.0,
            deviation_score=9.0,
            threshold=3.5,
            sample_count=10,
            interval_start=bucket.bucket_start,
            interval_end=bucket.bucket_end,
            confidence=1.0,
            reason="destination port rate is above baseline",
        ),
    )


async def test_the_engine_itself_keeps_maintenance_out_of_alerting(db_session) -> None:
    settings = alert_settings()
    detectors = {PORT: always_anomalous}
    _instance, source, entity = await seed(db_session)
    await evaluate(db_session, settings, source, START, entity, 600, detectors=detectors)
    alert = (await alerts(db_session))[0]

    await evaluate(
        db_session, settings, source, START + DAY, entity, 600,
        detectors=detectors, maintenance=True,
    )
    assert alert.occurrence_count == 1

    _instance2, other, other_entity = await seed(db_session)
    await evaluate(
        db_session, settings, other, START, other_entity, 600,
        detectors=detectors, maintenance=True,
    )
    assert len(await alerts(db_session)) == 1


async def test_candidate_raises_nothing(db_session) -> None:
    settings = alert_settings(anomaly_open_after_intervals=2)
    _instance, source, entity = await seed(db_session)
    report = await evaluate(db_session, settings, source, START, entity, 600)
    states = {r.states.get(PORT.value) for r in report.subject_reports}
    assert "CANDIDATE" in {str(s) for s in states} or AnomalyState.CANDIDATE in states
    assert await alerts(db_session) == []


async def test_entity_and_source_fingerprints_differ(db_session) -> None:
    settings = alert_settings()
    instance, source, entity = await seed(db_session)
    second = await add_entity(db_session, instance, source, key="22/tcp")
    for offset in range(4):
        await add_entity_cell(db_session, second, START + DAY * offset)
        await add_share_cell(
            db_session, source, START + DAY * offset,
            metric="top1_source_share", median=0.2, mad=0.02, p95=0.25,
            numerator_sum=80, denominator_sum=400,
        )
    await add_bucket(db_session, source.id, START, top1=90)
    await add_metric(db_session, entity, START, 600)
    await add_metric(db_session, second, START, 600)
    bucket = await db_session.get(IpsSourceBucket, (source.id, START))
    await IpsAnomalyEngine(db_session, settings=settings).evaluate_bucket(source, bucket)

    rows = await alerts(db_session)
    by_type = {(a.source_type, a.context["detector_type"]): a for a in rows}
    port_alerts = [a for a in rows if a.source_type == "ips_entity"]
    source_alert = by_type[("ips_source", CONCENTRATION.value)]
    assert len(port_alerts) == 2
    assert len({a.fingerprint for a in rows}) == len(rows)
    assert {a.fingerprint for a in port_alerts} == {
        port_fingerprint(instance, source, "3389/tcp"),
        port_fingerprint(instance, source, "22/tcp"),
    }
    assert source_alert.fingerprint == ips_anomaly_fingerprint(
        instance_id=instance.id, log_source_id=source.id, detector_type=CONCENTRATION.value
    )
    assert source_alert.source_id == source.id
    assert source_alert.title == "IPS_SOURCE_CONCENTRATION_SPIKE on ips-412"
    assert "dimension" not in source_alert.context and "entity" not in source_alert.context


async def classify(session, log_source_id, source_class: SourceClass) -> None:  # type: ignore[no-untyped-def]
    session.add(
        LogSourceClassification(
            log_source_id=log_source_id,
            source_class=source_class.value,
            basis="NAME_REGEX",
            conflicts=[],
            classified_at=START,
            review_status=ClassificationReviewStatus.REVIEWED.value,
        )
    )
    await session.flush()


async def test_classified_source_reaches_a_class_scoped_route(db_session) -> None:
    settings = alert_settings()
    _instance, source, entity = await seed(db_session)
    await classify(db_session, source.id, SourceClass.IPS)
    match = routing_policy(source_classes=frozenset({"IPS"}))
    other = routing_policy(source_classes=frozenset({"FIREWALL"}))
    dispatcher = NotificationDispatcher(db_session, {}, match, settings=settings)

    await evaluate(db_session, settings, source, START, entity, 600, enqueuer=dispatcher)
    alert = (await alerts(db_session))[0]
    assert alert.context["source_class"] == "IPS"
    assert [route.target for route in match.resolve(alert)] == ["ips-route"]
    assert other.resolve(alert) == []
    assert await notification_count(db_session) == 1

    await evaluate(db_session, settings, source, START + DAY, entity, 700, enqueuer=dispatcher)
    assert alert.occurrence_count == 2
    assert alert.context["source_class"] == "IPS"
    # The class never enters the fingerprint.
    assert alert.fingerprint == ips_anomaly_fingerprint(
        instance_id=_instance.id,
        log_source_id=source.id,
        detector_type=PORT.value,
        dimension="DESTINATION_PORT",
        value_key="3389/tcp",
    )


async def test_unclassified_source_omits_the_key(db_session) -> None:
    settings = alert_settings()
    _instance, source, entity = await seed(db_session)
    policy = routing_policy(source_classes=frozenset({"IPS"}))
    dispatcher = NotificationDispatcher(db_session, {}, policy, settings=settings)

    await evaluate(db_session, settings, source, START, entity, 600, enqueuer=dispatcher)
    alert = (await alerts(db_session))[0]
    assert "source_class" not in alert.context
    assert policy.resolve(alert) == []
    assert await notification_count(db_session) == 0


async def test_alert_text_carries_no_raw_event_value(db_session) -> None:
    settings = alert_settings()
    _instance, source, entity = await seed(db_session)
    raw_ip = "198.51.100.77"
    await evaluate(
        db_session, settings, source, START, entity, 600,
        contributors={"SOURCE_IP": [{"value": raw_ip, "events": 500}]},
    )
    alert = (await alerts(db_session))[0]
    visible = json.dumps(
        [alert.title, alert.description, alert.context, alert.evidence_snapshot],
        default=str,
    )
    assert raw_ip not in visible
    assert set(alert.context) == {
        "log_source", "owner", "criticality", "instance", "detector_type", "dimension",
        "entity", "observed", "expected", "ratio",
    }
    assert alert.context["entity"] == entity.display_name
    assert alert.title.endswith(entity.display_name)


async def _scheduled_run(session, *, enabled: bool, enqueuer):  # type: ignore[no-untyped-def]
    from app.services.ips_pipeline import IpsInstancePipeline
    from tests.integration.factories import FakeClock, make_instance
    from tests.integration.test_ips_pipeline import (
        DAY as PIPELINE_DAY,
    )
    from tests.integration.test_ips_pipeline import (
        START as PIPELINE_START,
    )
    from tests.integration.test_ips_pipeline import pipeline_settings, seed_source

    instance = await make_instance(session)
    # Spike on the first bucket only: it opens, then two healthy buckets resolve it.
    _source, entity, _cell = await seed_source(session, instance, qradar_id=412, spikes=[0])
    # seed_source holds one cell; the later days need theirs to be judged at all.
    for offset in (1, 2):
        await add_entity_cell(session, entity, PIPELINE_START + PIPELINE_DAY * offset)
    return await IpsInstancePipeline(
        session,
        settings=pipeline_settings(
            ips_alerting_enabled=enabled, anomaly_resolve_after_intervals=1
        ),
        clock=FakeClock(PIPELINE_START + PIPELINE_DAY * 4),
        enqueuer=enqueuer,
    ).run(instance)


async def test_a_scheduled_run_enqueues_a_notification_per_transition(db_session) -> None:
    enqueuer = RecordingEnqueuer()
    report = await _scheduled_run(db_session, enabled=True, enqueuer=enqueuer)
    assert report.sources[0].status == "ok", report.sources[0].error
    rows = await alerts(db_session)
    assert len(rows) == 1 and rows[0].status == AlertStatus.RESOLVED
    # One on open, one on resolve.
    assert [call[0] for call in enqueuer.calls] == [rows[0].fingerprint] * 2


async def test_a_scheduled_run_with_alerting_off_enqueues_nothing(db_session) -> None:
    enqueuer = RecordingEnqueuer()
    await _scheduled_run(db_session, enabled=False, enqueuer=enqueuer)
    assert enqueuer.calls == []
    assert await alerts(db_session) == []
