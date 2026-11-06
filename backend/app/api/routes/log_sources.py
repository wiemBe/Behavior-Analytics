"""Log-source inventory API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import anomaly_fingerprint, inventory_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertResolution, AlertService
from app.anomaly.thresholds import ThresholdResolver, direction_excluded
from app.api.deps import ProviderDep, SessionDep
from app.fleet.service import reclassify_instance
from app.i18n.messages import MessageRef, render
from app.i18n.refusal import permission_refusal
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyState,
    AnomalyType,
    ClassificationReviewStatus,
    Criticality,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceDetectorState,
)
from app.pivot.service import PivotNotFound, log_source_pivot
from app.repositories.audit import record_audit
from app.repositories.log_source import LogSourceRepository
from app.schemas.fleet import ClassificationOverrideIn, FleetSourceOut, fleet_source_out
from app.schemas.log_source import (
    AnomalySummary,
    LogSourceDetail,
    LogSourceSummary,
    LogSourceUpdate,
    SyncResult,
    business_days_usable,
)
from app.schemas.pivot import PivotOut
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_ADMIN,
    PERM_LOG_SOURCE_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services.inventory_sync import InventorySyncService

router = APIRouter(prefix="/log-sources", tags=["log-sources"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


def _reject_unusable_business_days(ls: LogSource, updates: dict[str, object]) -> None:
    """422 when the merged row would be business-hours-only with no usable days.

    Item bounds and an explicit empty list sent together with
    `business_hours_only: true` are rejected by the schema. This covers a PATCH
    that sets only one of the two fields.
    """
    hours_only = updates.get("business_hours_only", ls.business_hours_only)
    if hours_only is not True:
        return
    days = updates.get("business_days", ls.business_days)
    if business_days_usable(days):
        return
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[
            {
                "type": "value_error",
                "loc": ["body", "business_days"],
                "msg": (
                    "business_days must list at least one ISO weekday from 1 to 7 "
                    "when business_hours_only is true"
                ),
                "input": days,
            }
        ],
    )


def _jsonable(value: object) -> object:
    """Audit-detail form of a field value: enums and datetimes become strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    return getattr(value, "value", value)


def _to_summary(ls: LogSource, anomaly_counts: dict[uuid.UUID, int]) -> LogSourceSummary:
    s = LogSourceSummary.model_validate(ls)
    s.open_anomaly_count = anomaly_counts.get(ls.id, 0)
    return s


#: The recorded reason of a tuning closure (design §3.1, D-54).
MESSAGE_DIRECTION_DISABLED = MessageRef("anomaly.tuning.direction_disabled")


def _effective_direction(criticality: Criticality, custom: object) -> str:
    """The direction the engine will use for this source, via the same resolver.

    A malformed stored value is not rejected on read; the resolver returns it and
    ``direction_excluded`` treats anything but the two known values as ``both``.
    """
    return ThresholdResolver().resolve(
        criticality=criticality,
        custom=custom if isinstance(custom, dict) else None,
    ).direction


async def _close_disabled_incidents(
    session: AsyncSession,
    ls: LogSource,
    anomaly_types: set[AnomalyType],
    *,
    actor: str,
    now: datetime,
) -> list[dict[str, object]]:
    """Resolve the source's live incidents of a newly disabled direction.

    A skipped detector can never resolve its own open incident, so the tuning
    change that disabled it closes them here: state RESOLVED, one recorded
    transition per incident with the operator as actor, the detector's state row
    returned to NORMAL, and the condition's alert resolved. Same transaction as
    the PATCH; the returned blocks go into its audit detail.
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
                        LogSourceAnomaly.log_source_id == ls.id,
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
                    # An operator action has no driving bucket.
                    bucket_start=None,
                    reason=render(MESSAGE_DIRECTION_DISABLED),
                    reason_i18n=MESSAGE_DIRECTION_DISABLED.to_wire(),
                    actor=actor,
                    observed_value=incident.observed_value,
                    expected_value=incident.expected_value,
                )
            )
        detector_state = await session.scalar(
            select(LogSourceDetectorState).where(
                LogSourceDetectorState.log_source_id == ls.id,
                LogSourceDetectorState.anomaly_type == anomaly_type,
            )
        )
        if detector_state is not None:
            detector_state.state = AnomalyState.NORMAL
            detector_state.is_open = False
            detector_state.open_anomaly_id = None
            detector_state.active_anomaly_id = None
            detector_state.consecutive_anomalous = 0
            detector_state.consecutive_healthy = 0
        result = await alerts.resolve_by_fingerprint(
            anomaly_fingerprint(ls.id, anomaly_type.value),
            actor=actor,
            reason_message=MESSAGE_DIRECTION_DISABLED,
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


@router.get("", response_model=list[LogSourceSummary])
async def list_log_sources(
    session: SessionDep,
    monitored_only: bool = Query(default=False),
) -> list[LogSourceSummary]:
    repo = LogSourceRepository(session)
    sources = await repo.list(monitored_only=monitored_only)
    counts = await repo.open_anomaly_counts()
    return [_to_summary(ls, counts) for ls in sources]


@router.get("/{log_source_id}", response_model=LogSourceDetail)
async def get_log_source(log_source_id: uuid.UUID, session: SessionDep) -> LogSourceDetail:
    repo = LogSourceRepository(session)
    ls = await repo.get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    counts = await repo.open_anomaly_counts()
    detail = LogSourceDetail.model_validate(ls)
    detail.open_anomaly_count = counts.get(ls.id, 0)
    return detail


@router.get("/{log_source_id}/pivot", response_model=PivotOut)
async def get_log_source_pivot(log_source_id: uuid.UUID, session: SessionDep) -> PivotOut:
    """The source's last hour of events as AQL plus a console link. Never run by the product."""
    try:
        return await log_source_pivot(session, log_source_id)
    except PivotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{log_source_id}/anomalies", response_model=list[AnomalySummary])
async def get_anomalies(log_source_id: uuid.UUID, session: SessionDep) -> list[AnomalySummary]:
    repo = LogSourceRepository(session)
    if await repo.get(log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    anomalies = await repo.open_anomalies_for(log_source_id)
    return [AnomalySummary.model_validate(a) for a in anomalies]


@router.patch("/{log_source_id}", response_model=LogSourceDetail)
async def update_log_source(
    log_source_id: uuid.UUID,
    payload: LogSourceUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> LogSourceDetail:
    """Update operator-owned SOC metadata. QRadar-mirrored fields are read-only
    and not present on the update schema. Requires log_source:write, and the
    change is audited as {field: {from, to}}."""
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    repo = LogSourceRepository(session)
    ls = await repo.get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")

    changes: dict[str, dict[str, object]] = {}
    updates: dict[str, object] = payload.model_dump(exclude_unset=True)
    # Audit-only: a preview never changes what is saved.
    updates.pop("preview", None)
    _reject_unusable_business_days(ls, updates)
    old_custom = ls.custom_thresholds or {}
    new_custom = updates.get("custom_thresholds", old_custom)
    old_direction = _effective_direction(ls.criticality, old_custom)
    new_direction = _effective_direction(ls.criticality, new_custom)
    newly_excluded = set(direction_excluded(new_direction)) - set(
        direction_excluded(old_direction)
    )
    for field, value in updates.items():
        changes[field] = {"from": _jsonable(getattr(ls, field, None)), "to": _jsonable(value)}
        setattr(ls, field, value)
    await session.flush()
    now = datetime.now(UTC)
    closed = await _close_disabled_incidents(
        session, ls, newly_excluded, actor=principal.subject, now=now
    )
    audit_detail: dict[str, object] = {"changes": changes}
    if payload.preview is not None:
        audit_detail["preview"] = payload.preview.model_dump(mode="json")
    if closed:
        audit_detail["closed_incidents"] = closed
    await record_audit(
        session, action="log_source.update", actor=principal.subject,
        object_type="log_source", object_id=str(log_source_id), detail=audit_detail,
    )

    counts = await repo.open_anomaly_counts()
    detail = LogSourceDetail.model_validate(ls)
    detail.open_anomaly_count = counts.get(ls.id, 0)
    return detail


@router.post("/sync", response_model=SyncResult, status_code=status.HTTP_200_OK)
async def sync_inventory(
    session: SessionDep, provider: ProviderDep, principal: PrincipalDep
) -> SyncResult:
    """Pull the current log-source inventory from the configured provider.

    Idempotent: creates new sources, refreshes QRadar-owned fields on existing
    ones, leaves SOC metadata untouched.

    Requires admin:*. It is an administrative action that drives QRadar API
    load, not SOC metadata editing, and no role short of admin needs it; a
    dedicated admin:inventory would add a permission nothing else would hold.
    """
    _guard(principal, PERM_ADMIN)
    svc = InventorySyncService(session, provider)
    instance = await svc.ensure_default_instance()
    return await svc.sync(instance)


async def _classification(
    session: SessionDep, log_source_id: uuid.UUID
) -> LogSourceClassification | None:
    """The source's classification row, re-read past the identity map.

    The bulk upsert writes the row behind the ORM's back, so a cached instance
    would show the pre-change class.
    """
    return await session.scalar(
        select(LogSourceClassification)
        .where(LogSourceClassification.log_source_id == log_source_id)
        .execution_options(populate_existing=True)
    )


@router.patch("/{log_source_id}/classification", response_model=FleetSourceOut)
async def set_classification_override(
    log_source_id: uuid.UUID,
    payload: ClassificationOverrideIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> FleetSourceOut:
    """Set or clear (null) the operator's class override for one source.

    The source is re-classified in the same request, so the response shows the
    class the platform now uses. Audited as {from, to}. Reviewing is a separate,
    deliberate act: overriding does not mark the source reviewed.
    """
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    ls = await LogSourceRepository(session).get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    before = await _classification(session, log_source_id)
    previous = before.override_class if before else None
    await reclassify_instance(
        session,
        ls.instance_id,
        classified_at=datetime.now(UTC),
        source_ids=[log_source_id],
        override_updates={log_source_id: payload.override_class},
        override_actor=principal.subject,
    )
    after = await _classification(session, log_source_id)
    await record_audit(
        session, action="log_source.classification_override", actor=principal.subject,
        object_type="log_source", object_id=str(log_source_id),
        detail={
            "from": _jsonable(previous),
            "to": _jsonable(payload.override_class),
            "class": _jsonable(after.source_class) if after else None,
        },
    )
    return fleet_source_out(ls, after)


@router.post("/{log_source_id}/review", response_model=FleetSourceOut)
async def mark_reviewed(
    log_source_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> FleetSourceOut:
    """Mark a source reviewed and resolve its new-source alert.

    Idempotent: a source that is already reviewed is returned unchanged, with no
    second audit row and no alert work. Classifying never resolves the alert;
    only this deliberate act does.
    """
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    ls = await LogSourceRepository(session).get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    row = await _classification(session, log_source_id)
    if row is None:
        # Created REVIEWED by the bulk path; nothing is left to acknowledge.
        await reclassify_instance(
            session, ls.instance_id, classified_at=datetime.now(UTC),
            source_ids=[log_source_id],
        )
        row = await _classification(session, log_source_id)
    elif row.review_status != ClassificationReviewStatus.REVIEWED:
        now = datetime.now(UTC)
        row.review_status = ClassificationReviewStatus.REVIEWED
        row.reviewed_at = now
        row.reviewed_by = principal.subject
        await session.flush()
        _, resolved = await AlertService(session, clock=lambda: now).apply_bulk(
            [],
            [
                AlertResolution(
                    fingerprint=inventory_fingerprint(ls.instance_id, ls.qradar_id),
                    actor=principal.subject,
                    reason_message=MessageRef("alert.inventory.log_source.reviewed"),
                )
            ],
        )
        result = resolved[0]
        if result is not None and result.transition is not None:
            # Enqueue only: delivery belongs to the dispatch worker, as for the
            # alert resolve route.
            await NotificationDispatcher(
                session, notifiers={}, policy=default_policy()
            ).enqueue(result.alert, result.transition)
        await record_audit(
            session, action="log_source.review", actor=principal.subject,
            object_type="log_source", object_id=str(log_source_id),
            detail={"alert_resolved": result is not None},
        )
    return fleet_source_out(ls, row)
