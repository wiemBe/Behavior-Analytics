"""Saved dashboards (UR.5.5): list, read, create, replace, patch, delete, duplicate, default.

Reads need `read:dashboards` (router-level). Every write needs `dashboard:write`, checked in
the handler; ownership, visibility and `admin:dashboards` are the service's rules. A refused
write rolls back with the request, so it leaves no audit row.

`PUT /default` is declared before `/{dashboard_id}` so `default` is never parsed as an id.
Layouts are never logged.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import SessionDep
from app.schemas.dashboard import (
    DashboardCreate,
    DashboardOut,
    DashboardPatch,
    DashboardReplace,
    DefaultDashboardIn,
    DefaultDashboardOut,
    PagedDashboards,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_DASHBOARD_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services import dashboards as service
from app.services.dashboards import DashboardError

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard_write(principal: Principal) -> None:
    try:
        require_permission(principal, PERM_DASHBOARD_WRITE)
    except PermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _refuse(exc: DashboardError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


@router.get("", response_model=PagedDashboards)
async def list_dashboards(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedDashboards:
    """The caller's dashboards and every shared one, most recently changed first."""
    return await service.list_dashboards(session, principal, limit=limit, offset=offset)


@router.post("", response_model=DashboardOut, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    body: DashboardCreate, session: SessionDep, principal: PrincipalDep
) -> DashboardOut:
    _guard_write(principal)
    try:
        return await service.create_dashboard(session, principal, body)
    except DashboardError as exc:
        raise _refuse(exc) from exc


@router.put("/default", response_model=DefaultDashboardOut)
async def put_default(
    body: DefaultDashboardIn, session: SessionDep, principal: PrincipalDep
) -> DefaultDashboardOut:
    """Set the caller's default dashboard to one they can read, or clear it with null."""
    _guard_write(principal)
    try:
        chosen = await service.set_default(session, principal, body.dashboard_id)
    except DashboardError as exc:
        raise _refuse(exc) from exc
    return DefaultDashboardOut(dashboard_id=chosen)


@router.get("/{dashboard_id}", response_model=DashboardOut)
async def get_dashboard(
    dashboard_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> DashboardOut:
    try:
        return await service.get_dashboard(session, principal, dashboard_id)
    except DashboardError as exc:
        raise _refuse(exc) from exc


@router.put("/{dashboard_id}", response_model=DashboardOut)
async def replace_dashboard(
    dashboard_id: uuid.UUID,
    body: DashboardReplace,
    session: SessionDep,
    principal: PrincipalDep,
) -> DashboardOut:
    """Replace name, description and layout. 409 with `current_revision` on a stale write."""
    _guard_write(principal)
    try:
        return await service.replace_dashboard(session, principal, dashboard_id, body)
    except DashboardError as exc:
        raise _refuse(exc) from exc


@router.patch("/{dashboard_id}", response_model=DashboardOut)
async def patch_dashboard(
    dashboard_id: uuid.UUID,
    body: DashboardPatch,
    session: SessionDep,
    principal: PrincipalDep,
) -> DashboardOut:
    """Rename, describe, or change visibility (which needs `admin:dashboards`)."""
    _guard_write(principal)
    try:
        return await service.patch_dashboard(session, principal, dashboard_id, body)
    except DashboardError as exc:
        raise _refuse(exc) from exc


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    _guard_write(principal)
    try:
        await service.delete_dashboard(session, principal, dashboard_id)
    except DashboardError as exc:
        raise _refuse(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{dashboard_id}/duplicate", response_model=DashboardOut, status_code=status.HTTP_201_CREATED
)
async def duplicate_dashboard(
    dashboard_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> DashboardOut:
    """Copy a dashboard the caller can read into their own, PRIVATE, as 'Copy of ...'."""
    _guard_write(principal)
    try:
        return await service.duplicate_dashboard(session, principal, dashboard_id)
    except DashboardError as exc:
        raise _refuse(exc) from exc
