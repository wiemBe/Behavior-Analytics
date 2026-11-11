"""Authenticated, immutable shift handover log and triage-guarded composition."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.api.routes.anomalies import PrincipalDep
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException, permission_refusal
from app.schemas.handover import (
    HandoverBody,
    HandoverCreateIn,
    HandoverLatestOut,
    HandoverListOut,
    HandoverOut,
)
from app.security.rbac import PERM_ANOMALY_TRIAGE, PermissionDenied, require_permission
from app.services import handover

router = APIRouter(prefix="/handover", tags=["handover"])


def _write_guard(principal: PrincipalDep) -> None:
    try:
        require_permission(principal, PERM_ANOMALY_TRIAGE)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


@router.get("", response_model=HandoverListOut)
async def get_handovers(
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> HandoverListOut:
    return await handover.list_handovers(session, limit=limit, offset=offset)


@router.get("/latest", response_model=HandoverLatestOut)
async def get_latest_handover(session: SessionDep) -> HandoverLatestOut:
    row = await handover.latest(session)
    return HandoverLatestOut(handover=handover._out(row) if row is not None else None)


@router.get("/preview", response_model=HandoverBody)
async def preview_handover(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> HandoverBody:
    _write_guard(principal)
    try:
        body, _previous = await handover.build_body(session, settings, principal)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return body


@router.post("", response_model=HandoverOut, status_code=status.HTTP_201_CREATED)
async def post_handover(
    payload: HandoverCreateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> HandoverOut:
    _write_guard(principal)
    if payload.note is None or not payload.note.strip():
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, MessageRef("handover.note_required")
        )
    note = payload.note
    if len(note) > 4000:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MessageRef("handover.note_too_long", {"max": 4000}),
        )
    if len(payload.watch) > 20:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MessageRef("handover.watch_too_many", {"max": 20}),
        )
    try:
        return await handover.create(
            session,
            settings,
            principal,
            note=note,
            watch=payload.watch,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/{handover_id}", response_model=HandoverOut)
async def get_handover(handover_id: uuid.UUID, session: SessionDep) -> HandoverOut:
    result = await handover.get(session, handover_id)
    if result is None:
        raise CodedHTTPException(
            status.HTTP_404_NOT_FOUND, MessageRef("handover.not_found")
        )
    return result
