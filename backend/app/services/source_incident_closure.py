"""Administrative closure of a log source's live incidents.

A skipped detector can never resolve its own open incident, so the change that
stops it closes them here. The caller owns the transaction: nothing here commits.
No web-layer import, so a worker can call it too.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import anomaly_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertService
from app.i18n.messages import MessageRef, render
from app.models.enums import ACTIVE_ANOMALY_STATES, AnomalyState, AnomalyType
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceDetectorState,
)


async def close_source_incidents(
    session: AsyncSession,
    log_source_id: uuid.UUID,
    anomaly_types: Collection[AnomalyType],
    *,
    reason: MessageRef,
    actor: str,
    now: datetime,
    reset_state: AnomalyState,
    keep_values: bool,
) -> list[dict[str, object]]:
    """Resolve the source's live incidents of the given anomaly types.

    State RESOLVED, one recorded transition per incident with ``actor`` and
    ``reason``, the detector's state row set to ``reset_state`` with pointers and
    counters cleared, and the condition's alert resolved with the same reason.
    ``keep_values`` copies the incident's observed/expected values onto the
    transition (direction tuning: NORMAL, True); False records none (a closure
    that gives no verdict: INSUFFICIENT_DATA, False). The returned blocks go into
    the caller's audit detail.
    """
    if not anomaly_types:
        return []
    alerts = AlertService(session, clock=lambda: now)
    closed: list[dict[str, object]] = []
    for anomaly_type in sorted(anomaly_types, key=lambda kind: kind.value):
        incidents = list(
            (
                await session.scalars(
                    select(LogSourceAnomaly).where(
                        LogSourceAnomaly.log_source_id == log_source_id,
                        LogSourceAnomaly.anomaly_type == anomaly_type,
                        LogSourceAnomaly.state.in_(
                            [state.value for state in ACTIVE_ANOMALY_STATES]
                        ),
                    )
                )
            ).all()
        )
        for incident in incidents:
            previous = AnomalyState(incident.state)
            incident.state = AnomalyState.RESOLVED
            incident.resolved_at = now
            session.add(
                AnomalyStateTransition(
                    anomaly_id=incident.id,
                    from_state=previous,
                    to_state=AnomalyState.RESOLVED,
                    occurred_at=now,
                    # An administrative closure has no driving bucket.
                    bucket_start=None,
                    reason=render(reason),
                    reason_i18n=reason.to_wire(),
                    actor=actor,
                    observed_value=incident.observed_value if keep_values else None,
                    expected_value=incident.expected_value if keep_values else None,
                )
            )
        detector_state = await session.scalar(
            select(LogSourceDetectorState).where(
                LogSourceDetectorState.log_source_id == log_source_id,
                LogSourceDetectorState.anomaly_type == anomaly_type,
            )
        )
        if detector_state is not None:
            detector_state.state = reset_state
            detector_state.is_open = False
            detector_state.open_anomaly_id = None
            detector_state.active_anomaly_id = None
            detector_state.consecutive_anomalous = 0
            detector_state.consecutive_healthy = 0
        result = await alerts.resolve_by_fingerprint(
            anomaly_fingerprint(log_source_id, anomaly_type.value),
            actor=actor,
            reason_message=reason,
        )
        if result is not None and result.transition is not None:
            await NotificationDispatcher(
                session, notifiers={}, policy=default_policy()
            ).enqueue(result.alert, result.transition)
        closed.append(
            {
                "anomaly_type": anomaly_type.value,
                # A count, not the ids: the audit redactor masks UUID-shaped values, and a
                # masked list would look like a record while saying nothing.
                "incidents": len(incidents),
                "alert_resolved": result is not None and result.transition is not None,
            }
        )
    return closed
