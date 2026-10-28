"""Classification-rule administration.

Reads need `read:fleet` (router level). Writes need `admin:classification`,
checked in the handler before any lookup so a forbidden caller learns nothing
about which ids exist. Every write is audited and re-classifies what the rule can
reach, in bulk, before the response returns.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep
from app.fleet.service import reclassify_for_rule
from app.i18n.refusal import permission_refusal
from app.models.enums import ClassificationRuleKind
from app.models.fleet import ClassificationRule
from app.models.instance import QRadarInstance
from app.repositories.audit import record_audit
from app.schemas.fleet import (
    ClassificationRuleCreate,
    ClassificationRuleOut,
    ClassificationRuleUpdate,
    check_pattern,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_CLASSIFICATION_ADMIN,
    PermissionDenied,
    Principal,
    require_permission,
)

router = APIRouter(prefix="/classification-rules", tags=["classification-rules"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


def _snapshot(rule: ClassificationRule) -> dict[str, object]:
    return {
        "instance_id": str(rule.instance_id) if rule.instance_id else None,
        "kind": str(rule.kind),
        "pattern": rule.pattern,
        "target_class": str(rule.target_class),
        "priority": rule.priority,
        "enabled": rule.enabled,
    }


def _duplicate() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        "a rule with this instance, kind and pattern already exists",
    )


async def _get(session: SessionDep, rule_id: uuid.UUID) -> ClassificationRule:
    rule = await session.get(ClassificationRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "classification rule not found")
    return rule


@router.get("", response_model=list[ClassificationRuleOut])
async def list_rules(
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
    kind: ClassificationRuleKind | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    built_in: bool | None = Query(default=None),
) -> list[ClassificationRuleOut]:
    statement = select(ClassificationRule).order_by(
        ClassificationRule.priority.desc(),
        ClassificationRule.kind,
        ClassificationRule.pattern,
        ClassificationRule.id,
    )
    if instance_id is not None:
        statement = statement.where(ClassificationRule.instance_id == instance_id)
    if kind is not None:
        statement = statement.where(ClassificationRule.kind == kind.value)
    if enabled is not None:
        statement = statement.where(ClassificationRule.enabled.is_(enabled))
    if built_in is not None:
        statement = statement.where(ClassificationRule.built_in.is_(built_in))
    return [
        ClassificationRuleOut.model_validate(row)
        for row in (await session.scalars(statement)).all()
    ]


@router.post("", response_model=ClassificationRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: ClassificationRuleCreate, session: SessionDep, principal: PrincipalDep
) -> ClassificationRuleOut:
    _guard(principal, PERM_CLASSIFICATION_ADMIN)
    if payload.instance_id is not None and (
        await session.get(QRadarInstance, payload.instance_id) is None
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown instance_id")
    rule = ClassificationRule(
        instance_id=payload.instance_id,
        kind=payload.kind.value,
        pattern=payload.pattern,
        target_class=payload.target_class.value,
        priority=payload.priority,
        enabled=payload.enabled,
        built_in=False,
        description=payload.description,
        created_by=principal.subject,
    )
    try:
        async with session.begin_nested():
            session.add(rule)
            await session.flush()
    except IntegrityError as exc:
        raise _duplicate() from exc
    await record_audit(
        session, action="classification_rule.create", actor=principal.subject,
        object_type="classification_rule", object_id=str(rule.id), detail=_snapshot(rule),
    )
    await reclassify_for_rule(session, rule.instance_id, classified_at=datetime.now(UTC))
    await session.refresh(rule)
    return ClassificationRuleOut.model_validate(rule)


@router.patch("/{rule_id}", response_model=ClassificationRuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    payload: ClassificationRuleUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> ClassificationRuleOut:
    _guard(principal, PERM_CLASSIFICATION_ADMIN)
    rule = await _get(session, rule_id)
    changes = payload.model_dump(exclude_unset=True)
    if rule.built_in and set(changes) - {"enabled"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a built-in rule can only be enabled or disabled",
        )
    for name in ("enabled", "priority", "pattern"):
        if name in changes and changes[name] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} cannot be null"
            )
    if "pattern" in changes:
        try:
            check_pattern(ClassificationRuleKind(rule.kind), changes["pattern"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if changes.get("target_class") is not None and changes["target_class"].value == "UNCLASSIFIED":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "a rule cannot target UNCLASSIFIED"
        )

    before = _snapshot(rule)
    for name, value in changes.items():
        if name == "target_class" and value is not None:
            value = value.value
        setattr(rule, name, value)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise _duplicate() from exc
    after = _snapshot(rule)
    await record_audit(
        session, action="classification_rule.update", actor=principal.subject,
        object_type="classification_rule", object_id=str(rule.id),
        detail={
            "changes": {
                key: {"from": before[key], "to": after[key]}
                for key in after
                if before[key] != after[key]
            }
        },
    )
    await reclassify_for_rule(session, rule.instance_id, classified_at=datetime.now(UTC))
    await session.refresh(rule)
    return ClassificationRuleOut.model_validate(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    _guard(principal, PERM_CLASSIFICATION_ADMIN)
    rule = await _get(session, rule_id)
    if rule.built_in:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a built-in rule cannot be deleted; disable it instead",
        )
    instance_id = rule.instance_id
    snapshot = _snapshot(rule)
    await session.delete(rule)
    await session.flush()
    await record_audit(
        session, action="classification_rule.delete", actor=principal.subject,
        object_type="classification_rule", object_id=str(rule_id), detail=snapshot,
    )
    await reclassify_for_rule(session, instance_id, classified_at=datetime.now(UTC))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
