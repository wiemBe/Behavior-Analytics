"""SOC-17c: the alert policy's Phase A gate and the two-layer verdict.

With zero ``alert_policy`` rows every producer opens exactly the alert the
pre-17c code opened. A row can only reduce paging: the incident, its transitions
and its explanation stay, the alert and its notification do not. Resolution is
never gated, so an alert that already exists still closes when the policy turns
record-only later. DB-gated.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.alert_policy import AlertPolicy
from app.models.enums import (
    AlertPolicyMode,
    AlertPolicyScope,
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    Criticality,
    OutagePolicyMode,
    OutagePolicyScope,
    SourceClass,
)
from app.models.log_source import AnomalyStateTransition, LogSourceAnomaly
from app.models.outage import OutageAlertPolicy
from tests.integration.factories import make_instance, utc
from tests.integration.test_outage_policy_engine import (
    RecordingEnqueuer,
    _bucket,
    _classified,
    _eps_baseline,
    _event_count_baseline,
    _no_events_anomaly,
    _open_alert,
)
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "encryption_key": "x" * 44,
        "anomaly_deviation_threshold": 3.5,
        "anomaly_open_after_intervals": 1,
        "anomaly_resolve_after_intervals": 1,
        "anomaly_silence_grace_buckets": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def _policy(
    session,  # type: ignore[no-untyped-def]
    *,
    value: str,
    detector: str | None,
    mode: AlertPolicyMode,
    family: AnomalyFamily = AnomalyFamily.LOG_SOURCE,
    scope: AlertPolicyScope = AlertPolicyScope.SOURCE,
    instance_id: uuid.UUID | None = None,
) -> AlertPolicy:
    row = AlertPolicy(
        instance_id=instance_id,
        scope_kind=scope.value,
        scope_value=value,
        family=family.value,
        detector_type=detector,
        mode=mode,
        updated_by="test",
    )
    session.add(row)
    await session.flush()
    return row


async def _outage(
    session,  # type: ignore[no-untyped-def]
    value: str,
    mode: OutagePolicyMode,
    *,
    scope: OutagePolicyScope = OutagePolicyScope.SOURCE,
    grace: int = 0,
) -> None:
    session.add(
        OutageAlertPolicy(
            scope_kind=scope.value,
            scope_value=value,
            mode=mode.value,
            silence_grace_buckets=grace,
        )
    )
    await session.flush()


async def _spike(session, log_source, start, enqueuer=None) -> None:  # type: ignore[no-untyped-def]
    engine = AnomalyEngine(session, settings=_settings(), enqueuer=enqueuer)
    for index in range(2):
        await engine.evaluate_interval(
            log_source,
            await _bucket(
                session,
                log_source,
                start,
                index,
                average_eps=500.0,
                event_count=150_000,
                stored_event_count=150_000,
                last_event_at=start,
                event_delay_seconds=5.0,
                payload_signature=f"spike-{index}",
            ),
        )


async def _incident(session, log_source, atype: AnomalyType):  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(LogSourceAnomaly).where(
            LogSourceAnomaly.log_source_id == log_source.id,
            LogSourceAnomaly.anomaly_type == atype,
        )
    )


# ------------------------------------------------------- characterisation
async def test_zero_rows_opens_the_same_alerts_as_before(db_session) -> None:
    """No policy rows: every Phase A detector alerts exactly as it did."""
    instance = await make_instance(db_session)
    start = utc(h=10)

    quiet = await _classified(
        db_session, instance, qradar_id=1, name="quiet", criticality=Criticality.CRITICAL
    )
    await _event_count_baseline(db_session, quiet, start)
    noisy = await _classified(
        db_session, instance, qradar_id=2, name="noisy", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, noisy, start)

    dispatcher = RecordingEnqueuer()
    engine = AnomalyEngine(db_session, settings=_settings(), enqueuer=dispatcher)
    await engine.evaluate_interval(quiet, await _bucket(db_session, quiet, start, 0))
    await _spike(db_session, noisy, start, dispatcher)

    assert await _open_alert(db_session, quiet, AnomalyType.NO_EVENTS) is not None
    assert await _open_alert(db_session, noisy, AnomalyType.VOLUME_SPIKE) is not None
    assert len(dispatcher.enqueued) == 2


# ------------------------------------------------------------- layer 2
@pytest.mark.parametrize("mode", [AlertPolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST])
async def test_a_stopped_alert_still_records_the_incident_and_its_transitions(
    db_session, mode: AlertPolicyMode
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="spiky", criticality=Criticality.CRITICAL
    )
    await _eps_baseline(db_session, source, start)
    await _policy(
        db_session, value=str(source.id), detector=AnomalyType.VOLUME_SPIKE.value, mode=mode
    )
    dispatcher = RecordingEnqueuer()

    engine = AnomalyEngine(db_session, settings=_settings(), enqueuer=dispatcher)
    for index in range(2):
        await engine.evaluate_interval(
            source,
            await _bucket(
                db_session,
                source,
                start,
                index,
                average_eps=500.0,
                event_count=150_000,
                stored_event_count=150_000,
                last_event_at=start,
                event_delay_seconds=5.0,
                payload_signature=f"spike-{index}",
            ),
        )

    incident = await _incident(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert incident.opened_at is not None
    transitions = await db_session.scalar(
        select(func.count())
        .select_from(AnomalyStateTransition)
        .where(AnomalyStateTransition.anomaly_id == incident.id)
    )
    assert transitions and transitions >= 1
    # But the alert and the notification intent are absent.
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None
    assert dispatcher.enqueued == []


async def test_a_class_row_governs_a_classified_source(db_session) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="fw-a", criticality=Criticality.CRITICAL
    )
    await _eps_baseline(db_session, source, start)
    await _policy(
        db_session,
        value=SourceClass.FIREWALL.value,
        detector=AnomalyType.VOLUME_SPIKE.value,
        mode=AlertPolicyMode.RECORD_ONLY,
        scope=AlertPolicyScope.CLASS,
    )
    await _spike(db_session, source, start)
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None
    assert await _incident(db_session, source, AnomalyType.VOLUME_SPIKE) is not None


async def test_a_source_row_beats_a_class_row(db_session) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="fw-a", criticality=Criticality.CRITICAL
    )
    await _eps_baseline(db_session, source, start)
    await _policy(
        db_session,
        value=SourceClass.FIREWALL.value,
        detector=AnomalyType.VOLUME_SPIKE.value,
        mode=AlertPolicyMode.RECORD_ONLY,
        scope=AlertPolicyScope.CLASS,
    )
    await _policy(
        db_session,
        value=str(source.id),
        detector=AnomalyType.VOLUME_SPIKE.value,
        mode=AlertPolicyMode.ALERT,
    )
    await _spike(db_session, source, start)
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is not None


async def test_an_instance_row_applies_only_to_its_instance(db_session) -> None:
    instance = await make_instance(db_session)
    other = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="fw-a", criticality=Criticality.CRITICAL
    )
    await _eps_baseline(db_session, source, start)
    await _policy(
        db_session,
        value=str(source.id),
        detector=AnomalyType.VOLUME_SPIKE.value,
        mode=AlertPolicyMode.RECORD_ONLY,
        instance_id=other.id,
    )
    await _spike(db_session, source, start)
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is not None


# ------------------------------------------------------- two-layer verdict
@pytest.mark.parametrize(
    ("outage_mode", "policy_mode", "pages"),
    [
        (OutagePolicyMode.ALERT, AlertPolicyMode.ALERT, True),
        (OutagePolicyMode.ALERT, AlertPolicyMode.RECORD_ONLY, False),
        (OutagePolicyMode.ALERT, AlertPolicyMode.DIGEST, False),
        (OutagePolicyMode.RECORD_ONLY, AlertPolicyMode.ALERT, False),
        (OutagePolicyMode.RECORD_ONLY, AlertPolicyMode.RECORD_ONLY, False),
        (OutagePolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST, False),
    ],
)
async def test_no_events_two_layer_truth_table_is_monotone(
    db_session,
    outage_mode: OutagePolicyMode,
    policy_mode: AlertPolicyMode,
    pages: bool,
) -> None:
    """Layer 1 (outage) can stop; layer 2 can only stop further, never upgrade."""
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="quiet", criticality=Criticality.CRITICAL
    )
    await _event_count_baseline(db_session, source, start)
    await _outage(db_session, str(source.id), outage_mode)
    await _policy(
        db_session, value=str(source.id), detector=AnomalyType.NO_EVENTS.value, mode=policy_mode
    )
    dispatcher = RecordingEnqueuer()

    engine = AnomalyEngine(db_session, settings=_settings(), enqueuer=dispatcher)
    await engine.evaluate_interval(source, await _bucket(db_session, source, start, 0))

    incident = await _no_events_anomaly(db_session, source)
    assert incident is not None and incident.state == AnomalyState.OPEN
    alert = await _open_alert(db_session, source, AnomalyType.NO_EVENTS)
    assert (alert is not None) is pages
    assert (len(dispatcher.enqueued) == 1) is pages


async def test_a_live_alert_still_resolves_after_the_policy_turns_record_only(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="quiet", criticality=Criticality.CRITICAL
    )
    await _event_count_baseline(db_session, source, start)
    await _outage(db_session, str(source.id), OutagePolicyMode.ALERT)

    first = AnomalyEngine(db_session, settings=_settings())
    await first.evaluate_interval(source, await _bucket(db_session, source, start, 0))
    assert await _open_alert(db_session, source, AnomalyType.NO_EVENTS) is not None

    await _policy(
        db_session,
        value=str(source.id),
        detector=AnomalyType.NO_EVENTS.value,
        mode=AlertPolicyMode.RECORD_ONLY,
    )
    second = AnomalyEngine(db_session, settings=_settings())
    for index in range(1, 4):
        await second.evaluate_interval(
            source,
            await _bucket(
                db_session,
                source,
                start,
                index,
                average_eps=50.0,
                event_count=15_000,
                stored_event_count=15_000,
                last_event_at=start,
                event_delay_seconds=5.0,
            ),
        )

    from app.alerts.fingerprint import anomaly_fingerprint
    from app.models.alert import Alert
    from app.models.enums import AlertStatus

    resolved = await db_session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(source.id, AnomalyType.NO_EVENTS.value)
        )
    )
    assert resolved is not None and resolved.status == AlertStatus.RESOLVED


# ------------------------------------------------------------------ cost
async def test_the_policy_load_is_one_statement_whatever_the_fleet(db_session) -> None:
    async def policy_statements(size: int) -> int:
        instance = await make_instance(db_session)
        start = utc(h=10)
        sources = []
        for index in range(size):
            source = await _classified(
                db_session, instance, qradar_id=1000 + index, name=f"fleet-{size}-{index}",
                criticality=Criticality.CRITICAL,
            )
            await _eps_baseline(db_session, source, start)
            await _policy(
                db_session,
                value=str(source.id),
                detector=AnomalyType.VOLUME_SPIKE.value,
                mode=AlertPolicyMode.RECORD_ONLY,
                instance_id=instance.id,
            )
            sources.append(source)
        metrics = [
            await _bucket(
                db_session,
                source,
                start,
                0,
                average_eps=500.0,
                event_count=150_000,
                stored_event_count=150_000,
                last_event_at=start,
                event_delay_seconds=5.0,
                payload_signature=f"sig-{index}",
            )
            for index, source in enumerate(sources)
        ]
        engine = AnomalyEngine(db_session, settings=_settings())
        with counting(db_session) as statements:
            for source, metric in zip(sources, metrics, strict=True):
                await engine.evaluate_interval(source, metric)
        return sum(
            1
            for statement in statements
            if "alert_policy" in statement and "outage_alert_policy" not in statement
        )

    small = await policy_statements(2)
    large = await policy_statements(6)
    assert small == large == 1, (small, large)


async def test_source_only_rows_never_read_the_class_map(db_session) -> None:
    async def class_statements(size: int) -> int:
        instance = await make_instance(db_session)
        start = utc(h=10)
        sources = []
        for index in range(size):
            source = await _classified(
                db_session, instance, qradar_id=2000 + index, name=f"src-{size}-{index}",
                criticality=Criticality.CRITICAL,
            )
            await _eps_baseline(db_session, source, start)
            await _policy(
                db_session,
                value=str(source.id),
                detector=AnomalyType.VOLUME_SPIKE.value,
                mode=AlertPolicyMode.RECORD_ONLY,
            )
            sources.append(source)
        engine = AnomalyEngine(db_session, settings=_settings())
        with counting(db_session) as statements:
            for index, source in enumerate(sources):
                await engine.evaluate_interval(
                    source,
                    await _bucket(
                        db_session,
                        source,
                        start,
                        0,
                        average_eps=500.0,
                        event_count=150_000,
                        stored_event_count=150_000,
                        last_event_at=start,
                        event_delay_seconds=5.0,
                        payload_signature=f"sig-{index}",
                    ),
                )
        return sum(
            1 for statement in statements if "log_source_classification" in statement
        )

    assert await class_statements(2) == await class_statements(6) == 0


async def test_a_class_row_reads_the_class_map_once_whatever_the_fleet(db_session) -> None:
    async def class_statements(size: int, klass: SourceClass) -> int:
        instance = await make_instance(db_session)
        start = utc(h=10)
        sources = []
        for index in range(size):
            source = await _classified(
                db_session, instance, qradar_id=3000 + index, name=f"cls-{size}-{index}",
                cls=klass, criticality=Criticality.CRITICAL,
            )
            await _eps_baseline(db_session, source, start)
            sources.append(source)
        await _policy(
            db_session,
            value=klass.value,
            detector=AnomalyType.VOLUME_SPIKE.value,
            mode=AlertPolicyMode.RECORD_ONLY,
            scope=AlertPolicyScope.CLASS,
        )
        engine = AnomalyEngine(db_session, settings=_settings())
        with counting(db_session) as statements:
            for index, source in enumerate(sources):
                await engine.evaluate_interval(
                    source,
                    await _bucket(
                        db_session,
                        source,
                        start,
                        0,
                        average_eps=500.0,
                        event_count=150_000,
                        stored_event_count=150_000,
                        last_event_at=start,
                        event_delay_seconds=5.0,
                        payload_signature=f"sig-{index}",
                    ),
                )
        return sum(
            1 for statement in statements if "log_source_classification" in statement
        )

    assert await class_statements(2, SourceClass.FIREWALL) == (
        await class_statements(6, SourceClass.IPS)
    ) == 1
