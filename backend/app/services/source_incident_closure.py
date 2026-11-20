"""Administrative closure of a log source's live incidents.

A skipped detector can never resolve its own open incident, so the change that
stops it closes them here. The caller owns the transaction: nothing here commits.
No web-layer import, so a worker can call it too.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ARRAY, Table, any_, bindparam, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import anomaly_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertResolution, AlertService
from app.i18n.messages import MessageRef, render
from app.models.enums import ACTIVE_ANOMALY_STATES, AnomalyState, AnomalyType
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
)

#: Closure of a source that QRadar reports disabled. The two texts are the same sentence;
#: it states no verdict, so it never says "recovered" or "healthy".
MESSAGE_SOURCE_DISABLED_INCIDENT = MessageRef("anomaly.source_disabled_without_verdict")
MESSAGE_SOURCE_DISABLED_ALERT = MessageRef("alert.source_disabled_without_verdict")
SOURCE_VOLUME_TYPES = (AnomalyType.NO_EVENTS, AnomalyType.VOLUME_DROP, AnomalyType.VOLUME_SPIKE)
INVENTORY_ACTOR = "system:inventory-sync"
#: Sources per statement group. Ids travel as one array bind, so the cap is about the size
#: of the locked and rewritten row sets, not the driver's parameter limit.
CHUNK = 500


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


@dataclass(frozen=True)
class DisabledClosureSummary:
    """What one bulk closure changed, summed over its chunks."""

    sources: int = 0
    incidents_closed: int = 0
    alerts_resolved: int = 0
    notifications_enqueued: int = 0
    baseline_cells_invalidated: int = 0

    def add(self, other: DisabledClosureSummary) -> DisabledClosureSummary:
        return DisabledClosureSummary(
            sources=self.sources + other.sources,
            incidents_closed=self.incidents_closed + other.incidents_closed,
            alerts_resolved=self.alerts_resolved + other.alerts_resolved,
            notifications_enqueued=self.notifications_enqueued + other.notifications_enqueued,
            baseline_cells_invalidated=(
                self.baseline_cells_invalidated + other.baseline_cells_invalidated
            ),
        )


def _any_of(name: str) -> ColumnElement[Any]:
    """One array bind for an id list: the statement text does not grow with the ids."""
    return any_(bindparam(name, type_=ARRAY(PgUUID(as_uuid=True))))


async def close_for_disabled_sources(
    session: AsyncSession,
    source_ids: Iterable[uuid.UUID],
    *,
    observed_at: datetime,
    actor: str = INVENTORY_ACTOR,
) -> DisabledClosureSummary:
    """Close, without a verdict, everything volume-related that the given sources still hold.

    For every source at once: each CANDIDATE, OPEN or RECOVERING incident of NO_EVENTS,
    VOLUME_DROP and VOLUME_SPIKE (suppressed ones too) becomes RESOLVED at ``observed_at``
    with one transition that has no driving bucket and no values; an open ``anomaly_end`` is
    bounded at ``observed_at``, which claims no recovery. The sources' detector rows go to
    INSUFFICIENT_DATA with pointers and counters cleared, the incidents' OPEN or ACKNOWLEDGED
    alerts resolve through one ``apply_bulk`` lookup (their notifications are enqueued the
    usual way), and the sources' reliable baseline cells become unusable so a later re-enable
    cannot judge against a band the disabled tail contaminated. Closed records are not
    touched, so a repeat call writes nothing. The statement count depends on the number of
    chunks of ``CHUNK`` sources, not on the sources, apart from the dispatcher's savepoint
    and insert per notification route. Never commits.
    """
    unique = sorted(set(source_ids))
    total = DisabledClosureSummary()
    for start in range(0, len(unique), CHUNK):
        total = total.add(
            await _close_chunk(session, unique[start : start + CHUNK], observed_at, actor)
        )
    return total


async def _close_chunk(
    session: AsyncSession, ids: Sequence[uuid.UUID], observed_at: datetime, actor: str
) -> DisabledClosureSummary:
    source_values = list(ids)
    live = [state.value for state in ACTIVE_ANOMALY_STATES]
    rows = (
        await session.execute(
            select(
                LogSourceAnomaly.id,
                LogSourceAnomaly.log_source_id,
                LogSourceAnomaly.anomaly_type,
                LogSourceAnomaly.state,
            )
            .where(
                LogSourceAnomaly.log_source_id == _any_of("source_ids"),
                LogSourceAnomaly.anomaly_type.in_([kind.value for kind in SOURCE_VOLUME_TYPES]),
                LogSourceAnomaly.state.in_(live),
            )
            .order_by(LogSourceAnomaly.id)
            .with_for_update(),
            {"source_ids": source_values},
        )
    ).all()
    reason = MESSAGE_SOURCE_DISABLED_INCIDENT
    if rows:
        incident_values = [row.id for row in rows]
        await session.execute(
            update(LogSourceAnomaly)
            .where(LogSourceAnomaly.id == _any_of("incident_ids"))
            .values(
                state=AnomalyState.RESOLVED,
                resolved_at=observed_at,
                anomaly_end=func.coalesce(LogSourceAnomaly.anomaly_end, observed_at),
                updated_at=observed_at,
            )
            .execution_options(synchronize_session="fetch"),
            {"incident_ids": incident_values},
        )
        await session.execute(
            insert(AnomalyStateTransition),
            [
                {
                    "id": uuid.uuid4(),
                    "anomaly_id": row.id,
                    "from_state": row.state,
                    "to_state": AnomalyState.RESOLVED,
                    "occurred_at": observed_at,
                    # An administrative closure has no driving bucket and gives no values.
                    "bucket_start": None,
                    "reason": render(reason),
                    "reason_i18n": reason.to_wire(),
                    "actor": actor,
                    "observed_value": None,
                    "expected_value": None,
                    "created_at": observed_at,
                    "updated_at": observed_at,
                }
                for row in rows
            ],
        )
    await session.execute(
        update(LogSourceDetectorState)
        .where(
            LogSourceDetectorState.log_source_id == _any_of("source_ids"),
            LogSourceDetectorState.anomaly_type.in_([kind.value for kind in SOURCE_VOLUME_TYPES]),
            # Rows already reset stay as they are, so a repeat call writes nothing.
            or_(
                LogSourceDetectorState.state != AnomalyState.INSUFFICIENT_DATA,
                LogSourceDetectorState.is_open.is_(True),
                LogSourceDetectorState.open_anomaly_id.is_not(None),
                LogSourceDetectorState.active_anomaly_id.is_not(None),
                LogSourceDetectorState.consecutive_anomalous != 0,
                LogSourceDetectorState.consecutive_healthy != 0,
            ),
        )
        .values(
            state=AnomalyState.INSUFFICIENT_DATA,
            is_open=False,
            open_anomaly_id=None,
            active_anomaly_id=None,
            consecutive_anomalous=0,
            consecutive_healthy=0,
            updated_at=observed_at,
        )
        .execution_options(synchronize_session="fetch"),
        {"source_ids": source_values},
    )

    resolutions = [
        AlertResolution(
            fingerprint=anomaly_fingerprint(source_id, kind.value),
            actor=actor,
            reason_message=MESSAGE_SOURCE_DISABLED_ALERT,
        )
        for source_id, kind in sorted(
            {(row.log_source_id, AnomalyType(row.anomaly_type)) for row in rows},
            key=lambda pair: (str(pair[0]), pair[1].value),
        )
    ]
    resolved = 0
    enqueued = 0
    if resolutions:
        _, results = await AlertService(session, clock=lambda: observed_at).apply_bulk(
            [], resolutions
        )
        dispatcher = NotificationDispatcher(session, notifiers={}, policy=default_policy())
        for result in results:
            if result is not None and result.transition is not None:
                resolved += 1
                enqueued += len(await dispatcher.enqueue(result.alert, result.transition))

    # The same values `BaselineBuilder._invalidate_cells` writes, keyed by source rather than
    # by cell: a source has up to 336 cells, so a key list would overrun the driver's
    # parameter limit. Only reliable cells change, so a repeat call touches none.
    cells = cast(Table, LogSourceBaseline.__table__)
    invalidated = (
        await session.execute(
            update(cells)
            .where(cells.c.log_source_id == _any_of("source_ids"), cells.c.is_reliable.is_(True))
            .values(
                sample_count=0,
                is_reliable=False,
                p05=None,
                p95=None,
                observations=[],
                completeness=0.0,
                computed_at=observed_at,
                updated_at=observed_at,
                baseline_version=cells.c.baseline_version + 1,
            )
            .returning(cells.c.id),
            {"source_ids": source_values},
        )
    ).all()
    if invalidated:
        for obj in list(session.identity_map.values()):
            if isinstance(obj, LogSourceBaseline):
                session.expire(obj)
    return DisabledClosureSummary(
        sources=len(ids),
        incidents_closed=len(rows),
        alerts_resolved=resolved,
        notifications_enqueued=enqueued,
        baseline_cells_invalidated=len(invalidated),
    )
