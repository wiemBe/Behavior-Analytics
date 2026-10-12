"""Log-source inventory API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import ProviderDep, SessionDep
from app.repositories.audit import record_audit
from app.repositories.log_source import LogSourceRepository
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
