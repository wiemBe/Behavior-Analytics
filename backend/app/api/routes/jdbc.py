"""JDBC log-source read routes (JDBC-1 J1m3): stored state only, no provider, no queue."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import EffectiveSettingsDep, SessionDep
from app.i18n.refusal import permission_refusal
from app.jdbc.jobs import request_and_enqueue_jdbc_run
from app.models.enums import SearchFamily
from app.models.instance import QRadarInstance
from app.repositories.audit import record_audit
from app.repositories.jdbc import JdbcQueryError, JdbcRepository
from app.schemas.jdbc import JdbcSnapshotOut, JdbcSourceDetail, JdbcSourcePage, JdbcSyncQueued
from app.security.auth import get_principal
from app.security.rbac import PERM_ADMIN, PermissionDenied, Principal, require_permission
from app.services.search_cadence import SearchCadenceService, bucket_seconds

router = APIRouter(tags=["log-sources"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]
NO_JDBC_BLOCK = "No JDBC block for this log source."
JDBC_OFF = (
    "JDBC collection is turned off. An administrator can turn it on under Administration "
    "-> Features."
)
NO_CONSOLE = "No enabled console matches."
QUEUE_FAILED = "the JDBC refresh could not be queued"


async def _timing(session: SessionDep, settings: EffectiveSettingsDep) -> dict[str, Any]:
    """Request-wide inputs, resolved once: one cadence read, the rest from settings."""
    volume = SearchFamily.PHASE_A_VOLUME
    cadence = (await SearchCadenceService(session, settings).get(volume)).cadence_seconds
    return {
        "now": datetime.now(UTC),
        "cadence_seconds": cadence,
        "width_seconds": bucket_seconds(volume, settings) or settings.collection_interval_seconds,
        "anchor": settings.search_cadence_settle_seconds,
        "inventory_cadence_seconds": settings.log_source_sync_interval_seconds,
        # No typed truncation provenance exists yet; a guess would be a false TRUNCATED.
        "truncated_attempt": False,
    }


@router.get("/jdbc", response_model=JdbcSourcePage)
async def list_jdbc_sources(
    session: SessionDep,
    settings: EffectiveSettingsDep,
    limit: int | None = Query(default=None),
    offset: int = Query(default=0),
    q: str | None = Query(default=None),
    instance_id: uuid.UUID | None = Query(default=None),
    protocol: int | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    flow_state: str | None = Query(default=None),
) -> JdbcSourcePage:
    """One page of JDBC-like sources with flow state; four data SELECTs however many exist."""
    repo = JdbcRepository(session, settings)
    try:
        repo.bounds(limit=limit, offset=offset, q=q, enabled=enabled, protocol=protocol,
                    instance_id=instance_id, flow_state=flow_state)
        page = await repo.list(
            **await _timing(session, settings), limit=limit, offset=offset, q=q,
            enabled=enabled, protocol=protocol, instance_id=instance_id, flow_state=flow_state)
    except JdbcQueryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return JdbcSourcePage(
        items=page.items, total=page.total, limit=page.limit, offset=page.offset,
        snapshot=JdbcSnapshotOut(**vars(page.snapshot)))


@router.get("/{log_source_id}/jdbc", response_model=JdbcSourceDetail)
async def get_jdbc_block(
    log_source_id: uuid.UUID, session: SessionDep, settings: EffectiveSettingsDep,
) -> JdbcSourceDetail:
    """The JDBC block of one source; unknown ids and other protocols are one fixed 404."""
    found = await JdbcRepository(session, settings).detail(
        log_source_id, **await _timing(session, settings))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_JDBC_BLOCK)
    return found


@router.post("/jdbc/sync", response_model=JdbcSyncQueued, status_code=status.HTTP_202_ACCEPTED)
async def queue_jdbc_sync(
    session: SessionDep,
    settings: EffectiveSettingsDep,
    principal: PrincipalDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> JdbcSyncQueued:
    """Queue the JDBC parameter pass for every enabled console, or one (`admin:*`).

    Only the stored lease/queue state and the Celery queue are touched; no provider is built
    and QRadar is not called. A queued request or a live lease coalesces (`already_running`).
    A broker failure rolls the request back, so no QUEUED row is left to block the next one.
    One audit row per request.
    """
    try:
        require_permission(principal, PERM_ADMIN)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc
    if not settings.jdbc_collection_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, JDBC_OFF)
    from app.workers import tasks

    query = select(QRadarInstance.id).where(QRadarInstance.enabled.is_(True))
    if instance_id is not None:
        query = query.where(QRadarInstance.id == instance_id)
    ids = list((await session.scalars(query.order_by(QRadarInstance.name))).all())
    if instance_id is not None and not ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_CONSOLE)

    task_ids: list[str] = []
    already = False

    def enqueue(iid: uuid.UUID) -> None:
        task_ids.append(str(tasks.collect_jdbc_configs_task.delay(instance_id=str(iid)).id))

    for iid in ids:
        try:
            queued = await request_and_enqueue_jdbc_run(
                session, iid, now=datetime.now(UTC), enqueue=enqueue)
        except Exception as exc:
            await session.rollback()
            await record_audit(
                session, action="jdbc.sync", actor=principal.subject, object_type="jdbc",
                object_id=None, outcome="FAILURE",
                detail={"queued": False, "error_class": type(exc).__name__},
            )
            await session.commit()
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, QUEUE_FAILED) from exc
        already = already or not queued
    await record_audit(
        session, action="jdbc.sync", actor=principal.subject, object_type="jdbc",
        object_id=None,
        detail={"queued": bool(task_ids), "already_running": already, "task_ids": task_ids},
    )
    return JdbcSyncQueued(queued=bool(task_ids), already_running=already, task_ids=task_ids)
