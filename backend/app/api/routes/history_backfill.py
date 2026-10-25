"""Read the history backfill, estimate its cost, and record the operator's requests.

HISTORY-BACKFILL §B.10.3. No route takes an AQL, a window or a number of days from the
browser: `days` is always the stored setting. The writes touch only the decision row; the
scope rows change at each family's next collection.

The router is guarded with `read:history_backfill`; the three writes add
`admin:history_backfill` on the route. An unknown or disabled console is a 404 that
names the console and nothing else; a refused write is a 409 that says why and shows
the console's current state, so the operator sees what was recorded (§B.10.1).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import SessionDep, SettingsDep, requires
from app.api.request_meta import client_ip
from app.schemas.history_backfill import (
    ConflictOut,
    ConsoleEstimateOut,
    ConsoleOut,
    DecisionAnswerIn,
)
from app.security.rbac import PERM_HISTORY_BACKFILL_ADMIN, Principal
from app.services.history_backfill import ConsoleNotFoundError, HistoryBackfillService

router = APIRouter(prefix="/admin/history-backfill", tags=["history-backfill"])

AdminPrincipalDep = Annotated[Principal, Depends(requires(PERM_HISTORY_BACKFILL_ADMIN))]

_CONFLICT: dict[int | str, dict[str, Any]] = {status.HTTP_409_CONFLICT: {"model": ConflictOut}}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No enabled console has this id."}
}
_CREATED: dict[int | str, dict[str, Any]] = {status.HTTP_201_CREATED: {"model": ConsoleOut}}


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "console not found")


def _conflict(detail: str, console: ConsoleOut) -> JSONResponse:
    body = ConflictOut(detail=detail, console=console)
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=jsonable_encoder(body))


@router.get("", response_model=list[ConsoleOut])
async def list_history_backfill(session: SessionDep, settings: SettingsDep) -> list[ConsoleOut]:
    return await HistoryBackfillService(session, settings).consoles()


@router.get("/estimate", response_model=list[ConsoleEstimateOut])
async def history_backfill_estimate(
    session: SessionDep, settings: SettingsDep
) -> list[ConsoleEstimateOut]:
    return await HistoryBackfillService(session, settings).estimates()


@router.put(
    "/{instance_id}",
    response_model=ConsoleOut,
    responses={**_NOT_FOUND, **_CONFLICT, **_CREATED},
)
async def decide_history_backfill(
    instance_id: uuid.UUID,
    payload: DecisionAnswerIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminPrincipalDep,
) -> ConsoleOut | JSONResponse:
    try:
        outcome = await HistoryBackfillService(session, settings).decide(
            instance_id, payload.answer, actor=principal.subject, source_ip=client_ip(request)
        )
    except ConsoleNotFoundError:
        raise _not_found() from None
    if outcome.status == "conflict":
        return _conflict(outcome.detail or "the decision differs from the request", outcome.console)
    response.status_code = (
        status.HTTP_201_CREATED if outcome.status == "created" else status.HTTP_200_OK
    )
    return outcome.console


async def _request(
    kind: Literal["start", "stop"],
    instance_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal,
) -> ConsoleOut | JSONResponse:
    try:
        outcome = await HistoryBackfillService(session, settings).request(
            instance_id, kind, actor=principal.subject, source_ip=client_ip(request)
        )
    except ConsoleNotFoundError:
        raise _not_found() from None
    if outcome.refused:
        return _conflict(outcome.detail or "record Yes first", outcome.console)
    return outcome.console


@router.post(
    "/{instance_id}/start",
    response_model=ConsoleOut,
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def start_history_backfill(
    instance_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminPrincipalDep,
) -> ConsoleOut | JSONResponse:
    return await _request("start", instance_id, request, session, settings, principal)


@router.post(
    "/{instance_id}/stop",
    response_model=ConsoleOut,
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def stop_history_backfill(
    instance_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminPrincipalDep,
) -> ConsoleOut | JSONResponse:
    return await _request("stop", instance_id, request, session, settings, principal)
