"""Administration of notification routing rules (SOC-12c).

Rules are stored and evaluated here; delivery to their destinations is wired by the
dispatcher in a later slice. Responses and audit entries name destinations by id and
name only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from app.repositories.audit import record_audit
from app.schemas.notification_rule import RuleCreate, RuleDestinationOut, RuleOut, RulePatch
from app.security.auth import get_principal
from app.security.rbac import Principal

router = APIRouter(prefix="/admin/notifications/rules", tags=["notification-rules"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]

MAX_RULES = 50


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _coded(
    status_code: int, code: str, params: dict[str, object] | None = None
) -> CodedHTTPException:
    return CodedHTTPException(status_code, MessageRef(code, params or {}))


def _duplicate(name: str) -> CodedHTTPException:
    return _coded(status.HTTP_409_CONFLICT, "notifications.rule_name_taken", {"name": name})


def _window_invalid() -> CodedHTTPException:
    return _coded(status.HTTP_422_UNPROCESSABLE_CONTENT, "notifications.rule_window_invalid")


def _check_window(start: int | None, end: int | None) -> None:
    if start is None and end is None:
        return
    if start is None or end is None or start == end:
        raise _window_invalid()
    if not (0 <= start <= 1439 and 0 <= end <= 1439):
        raise _window_invalid()


async def _names(session: SessionDep, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(
        select(NotificationDestination.id, NotificationDestination.name).where(
            NotificationDestination.id.in_(ids)
        )
    )
    return {row.id: row.name for row in rows}


def _out(row: NotificationRule, names: dict[uuid.UUID, str]) -> RuleOut:
    return RuleOut(
        id=row.id,
        name=row.name,
        enabled=row.enabled,
        priority=row.priority,
        min_severity=row.min_severity,
        families=row.families,
        owners=row.owners,
        source_classes=row.source_classes,
        group_ids=row.group_ids,
        window_start_minute=row.window_start_minute,
        window_end_minute=row.window_end_minute,
        destinations=[
            RuleDestinationOut(id=d, name=names[d]) for d in row.destination_ids if d in names
        ],
        stop_default=row.stop_default,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def _get(
    session: SessionDep, rule_id: uuid.UUID, *, for_update: bool = False
) -> NotificationRule:
    statement = select(NotificationRule).where(NotificationRule.id == rule_id)
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise _coded(status.HTTP_404_NOT_FOUND, "notifications.rule_unknown")
    return row


async def _name_taken(
    session: SessionDep, name: str, *, except_id: uuid.UUID | None = None
) -> bool:
    statement = select(NotificationRule.id).where(NotificationRule.name == name)
    if except_id is not None:
        statement = statement.where(NotificationRule.id != except_id)
    return await session.scalar(statement) is not None


async def _require_destinations(
    session: SessionDep, ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Every id must exist. A share lock keeps a destination from being deleted mid-save."""
    rows = await session.execute(
        select(NotificationDestination.id, NotificationDestination.name)
        .where(NotificationDestination.id.in_(ids))
        .with_for_update(read=True)
    )
    names = {row.id: row.name for row in rows}
    if len(names) != len(set(ids)):
        raise _coded(status.HTTP_422_UNPROCESSABLE_CONTENT, "notifications.destination_unknown")
    return names


def _audit_detail(row: NotificationRule, names: dict[uuid.UUID, str]) -> dict[str, object]:
    # A bare UUID is redacted as token-shaped; the urn prefix keeps the id in the record.
    return {
        "id": f"urn:uuid:{row.id}",
        "name": row.name,
        "destinations": [
            {"id": f"urn:uuid:{d}", "name": names[d]} for d in row.destination_ids if d in names
        ],
    }


@router.get("", response_model=list[RuleOut])
async def list_rules(session: SessionDep) -> list[RuleOut]:
    rows = list(
        (
            await session.scalars(
                select(NotificationRule).order_by(NotificationRule.priority, NotificationRule.id)
            )
        ).all()
    )
    names = await _names(session, {d for row in rows for d in row.destination_ids})
    return [_out(row, names) for row in rows]


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreate, session: SessionDep, principal: PrincipalDep
) -> RuleOut:
    _check_window(payload.window_start_minute, payload.window_end_minute)
    # The cap is a service invariant; serialising creates closes the count/insert race.
    await session.execute(text("LOCK TABLE notification_rule IN SHARE ROW EXCLUSIVE MODE"))
    count = await session.scalar(select(func.count()).select_from(NotificationRule)) or 0
    if count >= MAX_RULES:
        raise _coded(status.HTTP_422_UNPROCESSABLE_CONTENT, "notifications.rule_limit")
    if await _name_taken(session, payload.name):
        raise _duplicate(payload.name)
    names = await _require_destinations(session, payload.destination_ids)
    row = NotificationRule(
        name=payload.name,
        enabled=payload.enabled,
        priority=payload.priority,
        min_severity=payload.min_severity,
        families=payload.families,
        owners=payload.owners,
        source_classes=payload.source_classes,
        group_ids=payload.group_ids,
        window_start_minute=payload.window_start_minute,
        window_end_minute=payload.window_end_minute,
        destination_ids=payload.destination_ids,
        stop_default=payload.stop_default,
        created_by=principal.subject,
        updated_by=principal.subject,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate(payload.name) from exc
    await session.refresh(row)
    await record_audit(
        session,
        action="notification_rule.create",
        actor=principal.subject,
        object_type="notification_rule",
        object_id=str(row.id),
        detail=_audit_detail(row, names),
    )
    return _out(row, names)


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: uuid.UUID, session: SessionDep) -> RuleOut:
    row = await _get(session, rule_id)
    return _out(row, await _names(session, set(row.destination_ids)))


@router.patch("/{rule_id}", response_model=RuleOut)
async def patch_rule(
    rule_id: uuid.UUID, payload: RulePatch, session: SessionDep, principal: PrincipalDep
) -> RuleOut:
    row = await _get(session, rule_id, for_update=True)
    given = payload.model_fields_set
    start = (
        payload.window_start_minute if "window_start_minute" in given else row.window_start_minute
    )
    end = payload.window_end_minute if "window_end_minute" in given else row.window_end_minute
    _check_window(start, end)
    if "name" in given:
        assert payload.name is not None
        if await _name_taken(session, payload.name, except_id=row.id):
            raise _duplicate(payload.name)
        row.name = payload.name
    # RulePatch refuses an explicit null for these, so only real values arrive.
    for field in ("enabled", "priority", "min_severity", "stop_default"):
        if field in given:
            value = getattr(payload, field)
            assert value is not None
            setattr(row, field, value)
    for field in ("families", "owners", "source_classes", "group_ids"):
        if field in given:
            setattr(row, field, getattr(payload, field))
    row.window_start_minute = start
    row.window_end_minute = end
    if "destination_ids" in given:
        assert payload.destination_ids is not None
        names = await _require_destinations(session, payload.destination_ids)
        row.destination_ids = payload.destination_ids
    else:
        names = await _names(session, set(row.destination_ids))
    row.updated_by = principal.subject
    row.updated_at = _utcnow()
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate(row.name) from exc
    await session.refresh(row)
    await record_audit(
        session,
        action="notification_rule.patch",
        actor=principal.subject,
        object_type="notification_rule",
        object_id=str(row.id),
        detail=_audit_detail(row, names),
    )
    return _out(row, names)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    row = await _get(session, rule_id, for_update=True)
    detail = _audit_detail(row, await _names(session, set(row.destination_ids)))
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        action="notification_rule.delete",
        actor=principal.subject,
        object_type="notification_rule",
        object_id=str(rule_id),
        detail=detail,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
