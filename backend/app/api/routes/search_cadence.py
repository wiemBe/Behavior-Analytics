"""Read and administer the cadence of each QRadar search family."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import SessionDep, SettingsDep, requires
from app.models.enums import SearchCadence, SearchFamily
from app.models.search_cadence import SearchCadenceSetting
from app.schemas.search_cadence import (
    SearchCadenceLastRunOut,
    SearchCadenceOut,
    SearchCadenceUpdate,
)
from app.security.rbac import PERM_SEARCH_CADENCE_ADMIN, Principal
from app.services.search_cadence import SearchCadenceService

router = APIRouter(prefix="/admin/search-cadence", tags=["search-cadence"])

AdminPrincipalDep = Annotated[
    Principal, Depends(requires(PERM_SEARCH_CADENCE_ADMIN))
]


def _bucket_seconds(family: SearchFamily, settings: SettingsDep) -> int | None:
    if family in {SearchFamily.PHASE_A_VOLUME, SearchFamily.WAF_COLLECTION}:
        return settings.collection_interval_seconds
    if family == SearchFamily.RULE_MATCH:
        return settings.rule_match_window_seconds
    return None


def _as_out(
    row: SearchCadenceSetting,
    *,
    service: SearchCadenceService,
    settings: SettingsDep,
    now: datetime,
) -> SearchCadenceOut:
    family = SearchFamily(row.family)
    last_run = None
    if row.last_run_at is not None:
        last_run = SearchCadenceLastRunOut(
            at=row.last_run_at,
            outcome=row.last_run_outcome,
            searches_sent=row.last_run_searches,
            buckets_collected=row.last_run_buckets,
            split_count=row.last_run_splits,
        )
    return SearchCadenceOut(
        family=family,
        cadence_seconds=row.cadence_seconds,
        allowed_cadences_seconds=[int(cadence) for cadence in SearchCadence],
        bucket_seconds=_bucket_seconds(family, settings),
        settle_seconds=settings.search_cadence_settle_seconds,
        last_slot_start=row.last_slot_start,
        next_dispatch_at=service.next_dispatch_at(row, now),
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        last_run=last_run,
    )


@router.get("", response_model=list[SearchCadenceOut])
async def list_search_cadences(
    session: SessionDep, settings: SettingsDep
) -> list[SearchCadenceOut]:
    service = SearchCadenceService(session, settings)
    now = datetime.now(UTC)
    return [
        _as_out(row, service=service, settings=settings, now=now)
        for row in await service.list()
    ]


@router.put("/{family}", response_model=SearchCadenceOut)
async def update_search_cadence(
    family: str,
    payload: SearchCadenceUpdate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminPrincipalDep,
) -> SearchCadenceOut:
    try:
        search_family = SearchFamily(family)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "search cadence family not found"
        ) from exc

    service = SearchCadenceService(session, settings)
    row = await service.set_cadence(
        search_family,
        int(payload.cadence_seconds),
        actor=principal.subject,
        source_ip=request.client.host if request.client is not None else None,
    )
    return _as_out(
        row,
        service=service,
        settings=settings,
        now=datetime.now(UTC),
    )
