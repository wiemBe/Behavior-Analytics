"""SOC-17d's Phase A suppression gate, break-through and non-disturbance.

The incident lifecycle stays exactly where it was. Only the alert action after
an ALERT policy decision is held back, with one hit per incident. DB-gated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.alert import Alert
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import (
    AlertPolicyMode,
    AlertPolicyScope,
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    Criticality,
    OutagePolicyMode,
    OutagePolicyScope,
    SuppressionKind,
)
from app.models.log_source import AnomalyStateTransition
from app.models.outage import OutageAlertPolicy
from tests.integration.factories import make_instance, utc
from tests.integration.test_alert_policy_engine import _incident
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


async def _suppression(
    session,  # type: ignore[no-untyped-def]
    source,  # type: ignore[no-untyped-def]
    detector: str | None,
    *,
    kind: SuppressionKind = SuppressionKind.ANALYST,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> AlertSuppression:
    now = datetime.now(UTC)
    row = AlertSuppression(
        family=AnomalyFamily.LOG_SOURCE.value,
        subject_id=source.id,
        detector_type=detector,
        kind=kind.value,
        reason="known noisy condition",
        owner="analyst",
        starts_at=starts_at or now - timedelta(minutes=1),
        ends_at=ends_at or now + timedelta(days=1),
        reference="CHG-17" if kind is SuppressionKind.KNOWN_CHANGE else None,
        created_by="analyst",
    )
    session.add(row)
    await session.flush()
    return row


async def _spikes(
    session,  # type: ignore[no-untyped-def]
    source,  # type: ignore[no-untyped-def]
    start,  # type: ignore[no-untyped-def]
    *,
    count: int = 3,
    start_index: int = 0,
    observed: float = 400.0,
    enqueuer=None,  # type: ignore[no-untyped-def]
    settings: Settings | None = None,
) -> None:
    engine = AnomalyEngine(session, settings=settings or _settings(), enqueuer=enqueuer)
    for index in range(start_index, count):
        await engine.evaluate_interval(
            source,
            await _bucket(
                session,
                source,
                start,
                index,
                average_eps=observed,
                event_count=int(observed * 300),
                stored_event_count=int(observed * 300),
                last_event_at=start,
                event_delay_seconds=5.0,
                payload_signature=f"spike-{observed}-{index}",
            ),
        )


async def _hit_count(session) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(AlertSuppressionHit)) or 0)


async def test_zero_suppression_rows_opens_exactly_the_existing_alerts(db_session) -> None:
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
    await _spikes(db_session, noisy, start, count=2, observed=500, enqueuer=dispatcher)

    assert await _open_alert(db_session, quiet, AnomalyType.NO_EVENTS) is not None
    assert await _open_alert(db_session, noisy, AnomalyType.VOLUME_SPIKE) is not None
    assert len(dispatcher.enqueued) == 2
    assert await _hit_count(db_session) == 0


async def test_suppression_records_the_open_incident_and_one_hit_without_an_alert(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="spiky", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, source, start)
    suppression = await _suppression(
        db_session, source, AnomalyType.VOLUME_SPIKE.value
    )
    dispatcher = RecordingEnqueuer()

    await _spikes(db_session, source, start, observed=400, enqueuer=dispatcher)

    incident = await _incident(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert incident.opened_at is not None
    assert incident.suppressed is False
    assert incident.suppression_reason is None
    transitions = await db_session.scalar(
        select(func.count())
        .select_from(AnomalyStateTransition)
        .where(AnomalyStateTransition.anomaly_id == incident.id)
    )
    assert transitions and transitions >= 1
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None
    assert dispatcher.enqueued == []
    hits = list((await db_session.scalars(select(AlertSuppressionHit))).all())
    assert len(hits) == 1
    assert hits[0].suppression_id == suppression.id
    assert hits[0].anomaly_id == incident.id
    assert hits[0].detector_type == AnomalyType.VOLUME_SPIKE.value
    await db_session.refresh(suppression)
    assert suppression.ended_at is None


async def test_breakthrough_dedups_repeated_extreme_buckets_and_leaves_the_window_active(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="extreme", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, source, start)
    suppression = await _suppression(
        db_session, source, AnomalyType.VOLUME_SPIKE.value
    )
    dispatcher = RecordingEnqueuer()

    # The reliable baseline is 50 EPS: 500 is exactly the 10x boundary.
    await _spikes(db_session, source, start, observed=500, enqueuer=dispatcher)

    alert = await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert alert is not None and alert.occurrence_count == 2
    assert len(dispatcher.enqueued) == 1
    assert await _hit_count(db_session) == 0
    await db_session.refresh(suppression)
    assert suppression.ended_at is None and suppression.ends_at > datetime.now(UTC)


async def test_the_next_anomalous_bucket_after_expiry_opens_the_still_open_incident(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="expires", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, source, start)
    suppression = await _suppression(
        db_session, source, AnomalyType.VOLUME_SPIKE.value
    )
    await _spikes(db_session, source, start, count=2, observed=400)
    incident = await _incident(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert incident is not None and incident.state == AnomalyState.OPEN
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None

    suppression.ends_at = datetime.now(UTC) - timedelta(microseconds=1)
    await db_session.flush()
    await _spikes(db_session, source, start, count=3, start_index=2, observed=400)

    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is not None
    assert await _hit_count(db_session) == 1


@pytest.mark.parametrize("mode", [AlertPolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST])
async def test_policy_stop_precedes_suppression_so_nothing_is_hit(
    db_session, mode: AlertPolicyMode
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="policy", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, source, start)
    db_session.add(
        AlertPolicy(
            scope_kind=AlertPolicyScope.SOURCE.value,
            scope_value=str(source.id),
            family=AnomalyFamily.LOG_SOURCE.value,
            detector_type=AnomalyType.VOLUME_SPIKE.value,
            mode=mode.value,
            updated_by="test",
        )
    )
    await _suppression(db_session, source, AnomalyType.VOLUME_SPIKE.value)

    # Even a break-through magnitude cannot resurrect a RECORD_ONLY/DIGEST action.
    await _spikes(db_session, source, start, observed=500)

    assert await _incident(db_session, source, AnomalyType.VOLUME_SPIKE) is not None
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None
    assert await _hit_count(db_session) == 0


async def test_outage_record_only_precedes_even_critical_silence_breakthrough(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="outage", criticality=Criticality.CRITICAL
    )
    await _event_count_baseline(db_session, source, start)
    db_session.add(
        OutageAlertPolicy(
            scope_kind=OutagePolicyScope.SOURCE.value,
            scope_value=str(source.id),
            mode=OutagePolicyMode.RECORD_ONLY.value,
            silence_grace_buckets=0,
        )
    )
    await _suppression(db_session, source, AnomalyType.NO_EVENTS.value)

    engine = AnomalyEngine(db_session, settings=_settings())
    await engine.evaluate_interval(source, await _bucket(db_session, source, start, 0))

    assert await _no_events_anomaly(db_session, source) is not None
    assert await _open_alert(db_session, source, AnomalyType.NO_EVENTS) is None
    assert await _hit_count(db_session) == 0


async def test_maintenance_keeps_its_own_suppressed_semantics_and_writes_no_hit(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="maintenance", criticality=Criticality.MEDIUM
    )
    source.maintenance_mode = True
    source.maintenance_until = datetime.now(UTC) + timedelta(hours=1)
    source.maintenance_reason = "database maintenance"
    await _eps_baseline(db_session, source, start)
    await _suppression(
        db_session,
        source,
        AnomalyType.VOLUME_SPIKE.value,
        kind=SuppressionKind.KNOWN_CHANGE,
    )

    await _spikes(db_session, source, start, count=2, observed=400)

    # A new maintenance-bucket anomaly follows the pre-existing SUPPRESSED
    # lifecycle (it does not become an OPEN incident), and never reaches 17d.
    assert await _incident(db_session, source, AnomalyType.VOLUME_SPIKE) is None
    assert source.maintenance_mode is True
    assert source.maintenance_reason == "database maintenance"
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None
    assert await _hit_count(db_session) == 0


async def test_known_change_suppression_does_not_set_the_maintenance_fields(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="change", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, source, start)
    await _suppression(
        db_session,
        source,
        AnomalyType.VOLUME_SPIKE.value,
        kind=SuppressionKind.KNOWN_CHANGE,
    )

    await _spikes(db_session, source, start, count=2, observed=400)

    incident = await _incident(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert incident is not None and incident.suppressed is False
    assert incident.suppression_reason is None
    assert source.maintenance_mode is False and source.maintenance_until is None


async def test_resolution_is_never_gated_by_a_new_suppression(db_session) -> None:
    instance = await make_instance(db_session)
    start = utc(h=10)
    source = await _classified(
        db_session, instance, qradar_id=1, name="resolve", criticality=Criticality.MEDIUM
    )
    await _eps_baseline(db_session, source, start)
    await _spikes(db_session, source, start, count=2, observed=400)
    alert = await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert alert is not None
    await _suppression(db_session, source, AnomalyType.VOLUME_SPIKE.value)

    engine = AnomalyEngine(db_session, settings=_settings())
    for index in range(2, 5):
        await engine.evaluate_interval(
            source,
            await _bucket(
                db_session,
                source,
                start,
                index,
                average_eps=50,
                event_count=15_000,
                stored_event_count=15_000,
                last_event_at=start,
                event_delay_seconds=5,
            ),
        )

    resolved = await db_session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(source.id, AnomalyType.VOLUME_SPIKE.value)
        )
    )
    assert resolved is not None and resolved.status == AlertStatus.RESOLVED
    assert await _hit_count(db_session) == 0


async def test_the_open_row_load_is_one_statement_at_two_fleet_sizes(db_session) -> None:
    async def suppression_statements(size: int) -> int:
        instance = await make_instance(db_session)
        start = utc(h=10)
        sources = []
        for index in range(size):
            source = await _classified(
                db_session,
                instance,
                qradar_id=1000 + index,
                name=f"fleet-{size}-{index}",
                criticality=Criticality.MEDIUM,
            )
            await _eps_baseline(db_session, source, start)
            sources.append(source)
        engine = AnomalyEngine(db_session, settings=_settings())
        with counting(db_session) as statements:
            for source in sources:
                for index in range(2):
                    await engine.evaluate_interval(
                        source,
                        await _bucket(
                            db_session,
                            source,
                            start,
                            index,
                            average_eps=400,
                            event_count=120_000,
                            stored_event_count=120_000,
                            last_event_at=start,
                            event_delay_seconds=5,
                            payload_signature=f"{source.id}-{index}",
                        ),
                    )
        return sum(
            1
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
            and "FROM alert_suppression" in statement
            and "alert_suppression_hit" not in statement
        )

    small = await suppression_statements(2)
    large = await suppression_statements(6)
    assert small == large == 1, (small, large)
