"""JDBC log-source read routes (JDBC-1 J1m3): stored state only, no provider, no queue."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import EffectiveSettingsDep, SessionDep
from app.models.enums import SearchFamily
from app.repositories.jdbc import JdbcQueryError, JdbcRepository
from app.schemas.jdbc import JdbcSnapshotOut, JdbcSourceDetail, JdbcSourcePage
from app.services.search_cadence import SearchCadenceService, bucket_seconds

router = APIRouter(tags=["log-sources"])

NO_JDBC_BLOCK = "No JDBC block for this log source."


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
