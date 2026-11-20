"""The incident-closure service extracted from the direction-tuning route (JDBC-1 P3a).

The extraction is a pure refactor: the PATCH route calls the service with the
tuning parameters and every recorded fact stays what it was. These tests pin the
service's explicit parameters (reason, actor, time, detector reset) both through the
real route and by calling it directly, including the other reset policy the QRadar
pause closure will need.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.i18n.messages import MessageRef
from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    AlertStatus,
    AlertTransition,
    AnomalyState,
    AnomalyType,
    NotificationChannel,
)
from app.models.identity import AuditLog
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceDetectorState,
)
from app.services.source_incident_closure import close_source_incidents
from tests.integration.test_direction_tuning import (
    _open_incident,
    _source,
    writer_client,
)

pytestmark = pytest.mark.integration

DIRECTION_REASON = MessageRef("noise.tuning.direction_disabled")
OTHER_REASON = MessageRef("alert.inventory.log_source.reviewed")
CLOSED_AT = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
HOOK = "https://hooks.example.test/closure"


async def _detector(session, source_id, kind) -> LogSourceDetectorState:
    row = await session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == source_id,
            LogSourceDetectorState.anomaly_type == kind,
        )
    )
    assert row is not None
    return row


async def _transitions(session, anomaly_id) -> list[AnomalyStateTransition]:
    return list(
        (
            await session.scalars(
                select(AnomalyStateTransition).where(
                    AnomalyStateTransition.anomaly_id == anomaly_id
                )
            )
        ).all()
    )


async def _notifications(session, alert_id) -> list[AlertNotification]:
    return list(
        (
            await session.scalars(
                select(AlertNotification).where(AlertNotification.alert_id == alert_id)
            )
        ).all()
    )


async def test_direction_tuning_command_unchanged(
    db_session, db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", HOOK)
    source = await _source(db_session)
    drop, drop_alert = await _open_incident(
        db_session, source, AnomalyType.VOLUME_DROP, with_alert=True
    )
    spike, _ = await _open_incident(db_session, source, AnomalyType.VOLUME_SPIKE)
    source_id, drop_id, spike_id = source.id, drop.id, spike.id
    assert drop_alert is not None
    alert_id = drop_alert.id
    await db_session.commit()

    response = writer_client(db_schema).patch(
        f"/api/v1/log-sources/{source_id}",
        json={"custom_thresholds": {"direction": "spike"}},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    resolved = await db_session.get(LogSourceAnomaly, drop_id)
    assert resolved is not None and AnomalyState(resolved.state) is AnomalyState.RESOLVED
    assert resolved.resolved_at is not None
    untouched = await db_session.get(LogSourceAnomaly, spike_id)
    assert untouched is not None and AnomalyState(untouched.state) is AnomalyState.OPEN
    assert await _transitions(db_session, spike_id) == []

    (transition,) = await _transitions(db_session, drop_id)
    assert AnomalyState(transition.from_state) is AnomalyState.OPEN
    assert AnomalyState(transition.to_state) is AnomalyState.RESOLVED
    assert transition.actor == "writer"
    assert transition.bucket_start is None
    assert transition.occurred_at == resolved.resolved_at
    assert transition.reason == "the direction was disabled by a tuning change"
    assert transition.reason_i18n == DIRECTION_REASON.to_wire()
    # Direction tuning keeps the incident's own figures on the transition.
    assert (transition.observed_value, transition.expected_value) == (5.0, 50.0)

    drop_state = await _detector(db_session, source_id, AnomalyType.VOLUME_DROP)
    assert AnomalyState(drop_state.state) is AnomalyState.NORMAL
    assert drop_state.is_open is False
    assert drop_state.open_anomaly_id is None and drop_state.active_anomaly_id is None
    assert drop_state.consecutive_anomalous == 0 and drop_state.consecutive_healthy == 0
    spike_state = await _detector(db_session, source_id, AnomalyType.VOLUME_SPIKE)
    assert AnomalyState(spike_state.state) is AnomalyState.OPEN
    assert spike_state.consecutive_anomalous == 2

    alert = await db_session.get(Alert, alert_id)
    assert alert is not None and alert.status == AlertStatus.RESOLVED
    assert alert.resolution_reason_i18n == DIRECTION_REASON.to_wire()
    (notification,) = await _notifications(db_session, alert_id)
    assert notification.transition == AlertTransition.RESOLVED
    assert notification.channel == NotificationChannel.WEBHOOK
    assert notification.target == HOOK

    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "log_source.update")
    )
    assert audit is not None
    assert audit.detail["closed_incidents"] == [
        {"anomaly_type": "VOLUME_DROP", "incidents": 1, "alert_resolved": True}
    ]


async def test_operator_reason_reset_and_notifications_unchanged(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", HOOK)
    # Tuning parameters: NORMAL, values copied, the operator as actor.
    tuned = await _source(db_session, qradar_id=1)
    tuned_drop, tuned_alert = await _open_incident(
        db_session, tuned, AnomalyType.VOLUME_DROP, with_alert=True
    )
    assert tuned_alert is not None
    tuned_spike, _ = await _open_incident(db_session, tuned, AnomalyType.VOLUME_SPIKE)

    closed = await close_source_incidents(
        db_session,
        tuned.id,
        {AnomalyType.VOLUME_DROP},
        reason=DIRECTION_REASON,
        actor="ada",
        now=CLOSED_AT,
        reset_state=AnomalyState.NORMAL,
        keep_values=True,
    )
    assert closed == [{"anomaly_type": "VOLUME_DROP", "incidents": 1, "alert_resolved": True}]
    (transition,) = await _transitions(db_session, tuned_drop.id)
    assert transition.reason_i18n == DIRECTION_REASON.to_wire()
    assert transition.actor == "ada"
    assert transition.occurred_at == CLOSED_AT and tuned_drop.resolved_at == CLOSED_AT
    assert (transition.observed_value, transition.expected_value) == (5.0, 50.0)
    state = await _detector(db_session, tuned.id, AnomalyType.VOLUME_DROP)
    assert AnomalyState(state.state) is AnomalyState.NORMAL
    assert state.open_anomaly_id is None and state.active_anomaly_id is None
    assert AnomalyState(tuned_spike.state) is AnomalyState.OPEN
    assert tuned_alert.status == AlertStatus.RESOLVED
    assert tuned_alert.resolved_at == CLOSED_AT
    assert tuned_alert.resolved_by == "ada"
    assert len(await _notifications(db_session, tuned_alert.id)) == 1

    # The other reset policy: no verdict, so INSUFFICIENT_DATA and no figures.
    paused = await _source(db_session, qradar_id=2)
    paused_incident, paused_alert = await _open_incident(
        db_session, paused, AnomalyType.VOLUME_SPIKE, with_alert=True
    )
    assert paused_alert is not None
    closed = await close_source_incidents(
        db_session,
        paused.id,
        [AnomalyType.VOLUME_SPIKE],
        reason=OTHER_REASON,
        actor="system:test",
        now=CLOSED_AT,
        reset_state=AnomalyState.INSUFFICIENT_DATA,
        keep_values=False,
    )
    assert closed == [{"anomaly_type": "VOLUME_SPIKE", "incidents": 1, "alert_resolved": True}]
    (transition,) = await _transitions(db_session, paused_incident.id)
    assert transition.reason_i18n == OTHER_REASON.to_wire()
    assert transition.actor == "system:test"
    assert transition.observed_value is None and transition.expected_value is None
    state = await _detector(db_session, paused.id, AnomalyType.VOLUME_SPIKE)
    assert AnomalyState(state.state) is AnomalyState.INSUFFICIENT_DATA
    assert state.is_open is False
    assert state.open_anomaly_id is None and state.active_anomaly_id is None
    assert state.consecutive_anomalous == 0 and state.consecutive_healthy == 0
    assert paused_alert.resolution_reason_i18n == OTHER_REASON.to_wire()
    assert len(await _notifications(db_session, paused_alert.id)) == 1

    # Nothing to close: no types, or no live incident, records nothing.
    assert await close_source_incidents(
        db_session, paused.id, set(), reason=OTHER_REASON, actor="x", now=CLOSED_AT,
        reset_state=AnomalyState.NORMAL, keep_values=True,
    ) == []
    again = await close_source_incidents(
        db_session, paused.id, {AnomalyType.VOLUME_SPIKE}, reason=OTHER_REASON, actor="x",
        now=CLOSED_AT, reset_state=AnomalyState.NORMAL, keep_values=True,
    )
    assert again == [{"anomaly_type": "VOLUME_SPIKE", "incidents": 0, "alert_resolved": False}]
    assert len(await _transitions(db_session, paused_incident.id)) == 1
