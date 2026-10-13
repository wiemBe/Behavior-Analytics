"""Outage alert policy administration (Phase C.2).

Every route needs `admin:outage`, applied at the router mount rather than per
handler so a new route cannot ship unguarded. `read:*` does not satisfy it: the
policy decides which silences page a human, which is operational control, not a
view.

A PUT upserts exactly one row, scoped to a class or a single source, optionally
to one instance. Every mutation is audited with the before and after values.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import SessionDep, SettingsDep
from app.fleet.outage_policy import resolve
from app.models.enums import OutagePolicyMode, OutagePolicyScope, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.outage import OutageAlertPolicy
from app.repositories.audit import record_audit
from app.schemas.outage import OutagePolicyOut, OutagePolicyUpsert
from app.security.auth import get_principal
from app.security.rbac import Principal

router = APIRouter(prefix="/outage-policy", tags=["outage-policy"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _snapshot(row: OutageAlertPolicy) -> dict[str, object]:
    return {
        "instance_id": str(row.instance_id) if row.instance_id is not None else None,
        "scope_kind": str(row.scope_kind),
        "scope_value": row.scope_value,
        "mode": str(row.mode),
        "silence_grace_buckets": row.silence_grace_buckets,
    }


def _query(kind: OutagePolicyScope, value: str, instance_id: uuid.UUID | None):  # type: ignore[no-untyped-def]
    conditions = [
        OutageAlertPolicy.scope_kind == kind.value,
        OutageAlertPolicy.scope_value == value,
    ]
    if instance_id is None:
        conditions.append(OutageAlertPolicy.instance_id.is_(None))
    else:
        conditions.append(OutageAlertPolicy.instance_id == instance_id)
    return select(OutageAlertPolicy).where(*conditions)


async def _check_instance(session: SessionDep, instance_id: uuid.UUID | None) -> None:
    """An instance-scoped row must name a real instance; NULL is global."""
    if instance_id is not None and await session.get(QRadarInstance, instance_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown instance_id")


async def _upsert(
    session: SessionDep,
    principal: Principal,
    *,
    kind: OutagePolicyScope,
    value: str,
    payload: OutagePolicyUpsert,
) -> OutagePolicyOut:
    await _check_instance(session, payload.instance_id)
    row = await session.scalar(_query(kind, value, payload.instance_id))
    before = _snapshot(row) if row is not None else None
    if row is None:
        row = OutageAlertPolicy(
            instance_id=payload.instance_id, scope_kind=kind.value, scope_value=value
        )
        session.add(row)
    row.mode = payload.mode.value
    row.silence_grace_buckets = payload.silence_grace_buckets
    row.updated_by = principal.subject
    await session.flush()
    after = _snapshot(row)
    await record_audit(
        session,
        action="outage_policy.upsert",
        actor=principal.subject,
        object_type="outage_alert_policy",
        # Keyed by scope, not by row id: a SOURCE row's scope value is a UUID,
        # which value-level redaction would mask in `detail`, and the row is
        # gone after a delete. "CLASS:FIREWALL" / "SOURCE:<uuid>" survives both.
        object_id=f"{kind.value}:{value}",
        detail={"before": before, "after": after},
    )
    return OutagePolicyOut.model_validate(row)


@router.get("", response_model=list[OutagePolicyOut])
async def list_policy(
    session: SessionDep, settings: SettingsDep
) -> list[OutagePolicyOut]:
    """Every policy row, with the monitored sources each class row governs.

    A class row's count excludes sources a SOURCE row already governs: it is how
    many monitored sources this class's mode actually decides.
    """
    rows = list(
        (
            await session.scalars(
                select(OutageAlertPolicy).order_by(
                    OutageAlertPolicy.scope_kind,
                    OutageAlertPolicy.scope_value,
                    OutageAlertPolicy.instance_id,
                )
            )
        ).all()
    )
    counts = await _governed_counts(session, rows, settings.outage_alert_default)
    out: list[OutagePolicyOut] = []
    for row in rows:
        item = OutagePolicyOut.model_validate(row)
        if row.scope_kind == OutagePolicyScope.CLASS.value:
            item.governed_monitored_sources = counts.get(row.scope_value, 0)
        out.append(item)
    return out


async def _governed_counts(
    session: SessionDep, rows: list[OutageAlertPolicy], default: OutagePolicyMode
) -> dict[str, int]:
    sources = (
        await session.execute(
            select(
                LogSource.id,
                LogSource.instance_id,
                func.coalesce(
                    LogSourceClassification.source_class, SourceClass.UNCLASSIFIED.value
                ),
            )
            .select_from(LogSource)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(
                LogSource.enabled.is_(True), LogSource.monitoring_enabled.is_(True)
            )
        )
    ).all()
    counts: dict[str, int] = defaultdict(int)
    for source_id, instance_id, source_class in sources:
        resolved = resolve(
            rows,
            log_source_id=source_id,
            source_class=source_class,
            instance_id=instance_id,
            default=default,
        )
        if resolved.basis == "CLASS":
            counts[source_class] += 1
    return counts


@router.put("/classes/{source_class}", response_model=OutagePolicyOut)
async def put_class_policy(
    source_class: str,
    payload: OutagePolicyUpsert,
    session: SessionDep,
    principal: PrincipalDep,
) -> OutagePolicyOut:
    try:
        klass = SourceClass(source_class)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown source class: {source_class}"
        ) from exc
    return await _upsert(
        session,
        principal,
        kind=OutagePolicyScope.CLASS,
        value=klass.value,
        payload=payload,
    )


@router.delete("/classes/{source_class}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class_policy(
    source_class: str,
    session: SessionDep,
    principal: PrincipalDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> Response:
    try:
        klass = SourceClass(source_class)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown source class: {source_class}"
        ) from exc
    return await _delete(
        session,
        principal,
        kind=OutagePolicyScope.CLASS,
        value=klass.value,
        instance_id=instance_id,
    )


@router.put("/sources/{log_source_id}", response_model=OutagePolicyOut)
async def put_source_policy(
    log_source_id: uuid.UUID,
    payload: OutagePolicyUpsert,
    session: SessionDep,
    principal: PrincipalDep,
) -> OutagePolicyOut:
    if await session.get(LogSource, log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    return await _upsert(
        session,
        principal,
        kind=OutagePolicyScope.SOURCE,
        value=str(log_source_id),
        payload=payload,
    )


@router.delete("/sources/{log_source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source_policy(
    log_source_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> Response:
    return await _delete(
        session,
        principal,
        kind=OutagePolicyScope.SOURCE,
        value=str(log_source_id),
        instance_id=instance_id,
    )


async def _delete(
    session: SessionDep,
    principal: Principal,
    *,
    kind: OutagePolicyScope,
    value: str,
    instance_id: uuid.UUID | None,
) -> Response:
    row = await session.scalar(_query(kind, value, instance_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "outage policy row not found")
    snapshot = _snapshot(row)
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        action="outage_policy.delete",
        actor=principal.subject,
        object_type="outage_alert_policy",
        object_id=f"{kind.value}:{value}",
        detail={"before": snapshot, "after": None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
