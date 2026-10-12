"""Log-source inventory API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import inventory_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertResolution, AlertService
from app.api.deps import ProviderDep, SessionDep
from app.fleet.service import reclassify_instance
from app.models.enums import ClassificationReviewStatus
from app.models.fleet import LogSourceClassification
from app.repositories.audit import record_audit
from app.repositories.log_source import LogSourceRepository
from app.schemas.fleet import ClassificationOverrideIn, FleetSourceOut, fleet_source_out
from app.schemas.log_source import (
    AnomalySummary,
    LogSourceDetail,
    LogSourceSummary,
    LogSourceUpdate,
    SyncResult,
)
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _jsonable(value: object) -> object:
    """Audit-detail form of a field value: enums and datetimes become strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    return getattr(value, "value", value)


def _to_summary(ls, anomaly_counts: dict[uuid.UUID, int]) -> LogSourceSummary:
    s = LogSourceSummary.model_validate(ls)
    s.open_anomaly_count = anomaly_counts.get(ls.id, 0)
    return s


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
    for field, value in payload.model_dump(exclude_unset=True).items():
        changes[field] = {"from": _jsonable(getattr(ls, field, None)), "to": _jsonable(value)}
        setattr(ls, field, value)
    await session.flush()
    await record_audit(
        session, action="log_source.update", actor=principal.subject,
        object_type="log_source", object_id=str(log_source_id), detail={"changes": changes},
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
                    reason="log source reviewed",
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
