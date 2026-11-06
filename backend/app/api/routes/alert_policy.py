"""SOC-17c alert policy administration and digest status.

Reads need ``read:alerts`` (the alerts surface already shows what pages a human);
every write needs ``admin:outage``, the same operational capability as the
outage policy, applied per handler because the read is deliberately open to
alert readers. Every mutation is audited with the before and after values.

One PUT upserts exactly one row, identified by
``(instance_id, scope_kind, scope_value, family, detector_type)``. The detector
is validated against the family's own enum (never a SQL enum, AGENTS.md rule 6),
``scope_value`` is a known source id or a ``SourceClass`` value, and property
watch has no class scope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import String, and_, cast, select
from sqlalchemy.sql import Select

from app.api.deps import SessionDep, SettingsDep, requires
from app.models.alert_policy import AlertDigestRun, AlertPolicy
from app.models.enums import (
    AlertPolicyScope,
    AnomalyFamily,
    AnomalyType,
    IpsAnomalyType,
    PwAnomalyType,
    SourceClass,
    WafAnomalyType,
)
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch
from app.repositories.audit import record_audit
from app.schemas.alert_policy import (
    AlertPolicyOut,
    AlertPolicyUpsert,
    DigestInstanceStatusOut,
    DigestRunOut,
    DigestStatusOut,
)
from app.security.rbac import PERM_OUTAGE_ADMIN, Principal
from app.services.digest import recipient_addresses

router = APIRouter(prefix="/alert-policy", tags=["alert-policy"])

#: Writes reuse ``admin:outage``: this is the same question -- what pages a human.
WritePrincipal = Annotated[Principal, Depends(requires(PERM_OUTAGE_ADMIN))]

_DETECTORS: dict[AnomalyFamily, tuple[str, ...]] = {
    AnomalyFamily.LOG_SOURCE: tuple(item.value for item in AnomalyType),
    AnomalyFamily.WAF: tuple(item.value for item in WafAnomalyType),
    AnomalyFamily.IPS: tuple(item.value for item in IpsAnomalyType),
    AnomalyFamily.PROPERTY_WATCH: tuple(item.value for item in PwAnomalyType),
}


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def _snapshot(row: AlertPolicy) -> dict[str, object]:
    return {
        "instance_id": str(row.instance_id) if row.instance_id is not None else None,
        "scope_kind": str(row.scope_kind),
        "scope_value": row.scope_value,
        "family": str(row.family),
        "detector_type": row.detector_type,
        "mode": str(row.mode),
    }


@dataclass(frozen=True)
class _Scope:
    value: str
    source_name: str | None
    source_class: SourceClass | None


async def _instance_name(
    session: SessionDep, instance_id: uuid.UUID | None
) -> str | None:
    if instance_id is None:
        return None
    instance = await session.get(QRadarInstance, instance_id)
    if instance is None:
        raise _unprocessable("unknown instance_id")
    return instance.name


async def _source_scope(
    session: SessionDep, payload: AlertPolicyUpsert
) -> _Scope:
    """Validate ``scope_value`` and return what the response names it by."""
    try:
        value = str(uuid.UUID(payload.scope_value))
    except ValueError as exc:
        raise _unprocessable(
            f"scope_value is not a source id: {payload.scope_value}"
        ) from exc
    if payload.family is AnomalyFamily.PROPERTY_WATCH:
        watch = await session.get(PropertyWatch, uuid.UUID(value))
        if watch is None:
            raise _unprocessable("scope_value is not a known property watch")
        return _Scope(value=value, source_name=watch.name, source_class=None)
    source = await session.get(LogSource, uuid.UUID(value))
    if source is None:
        raise _unprocessable("scope_value is not a known log source")
    source_class = await session.scalar(
        select(LogSourceClassification.source_class).where(
            LogSourceClassification.log_source_id == source.id
        )
    )
    return _Scope(
        value=value,
        source_name=source.name,
        source_class=SourceClass(source_class)
        if source_class is not None
        else SourceClass.UNCLASSIFIED,
    )


async def _checked(
    session: SessionDep, payload: AlertPolicyUpsert
) -> tuple[_Scope | None, _Scope | None, str | None]:
    """Validate the body: instance, detector and scope.

    Returns ``(class_scope, source_scope, detector)``; exactly one of the two
    scopes is set and matches ``payload.scope_kind``.
    """
    await _instance_name(session, payload.instance_id)
    detector = payload.detector_type
    if detector is not None and detector not in _DETECTORS[payload.family]:
        raise _unprocessable(
            f"unknown detector for family {payload.family.value}: {detector}"
        )
    if payload.scope_kind is AlertPolicyScope.CLASS:
        if payload.family is AnomalyFamily.PROPERTY_WATCH:
            raise _unprocessable("property watch has no class scope")
        try:
            klass = SourceClass(payload.scope_value)
        except ValueError as exc:
            raise _unprocessable(
                f"unknown source class: {payload.scope_value}"
            ) from exc
        return (
            _Scope(value=klass.value, source_name=None, source_class=klass),
            None,
            detector,
        )
    scope = await _source_scope(session, payload)
    return None, scope, detector


def _query(
    payload: AlertPolicyUpsert, scope_value: str, detector: str | None
) -> Select[AlertPolicy]:
    conditions = [
        AlertPolicy.scope_kind == payload.scope_kind.value,
        AlertPolicy.scope_value == scope_value,
        AlertPolicy.family == payload.family.value,
    ]
    conditions.append(
        AlertPolicy.detector_type.is_(None)
        if detector is None
        else AlertPolicy.detector_type == detector
    )
    conditions.append(
        AlertPolicy.instance_id.is_(None)
        if payload.instance_id is None
        else AlertPolicy.instance_id == payload.instance_id
    )
    return select(AlertPolicy).where(*conditions)


@router.get("", response_model=list[AlertPolicyOut])
async def list_policy(session: SessionDep) -> list[AlertPolicyOut]:
    """Every policy row, with the source, class and instance it names.

    One outer join per possible parent (log source, classification, property
    watch, instance), so the statement count does not grow with the rows.
    """
    result = (
        await session.execute(
            select(
                AlertPolicy,
                LogSource.name,
                LogSourceClassification.source_class,
                PropertyWatch.name,
                QRadarInstance.name,
            )
            .select_from(AlertPolicy)
            .outerjoin(QRadarInstance, QRadarInstance.id == AlertPolicy.instance_id)
            .outerjoin(
                LogSource,
                and_(
                    AlertPolicy.scope_kind == AlertPolicyScope.SOURCE.value,
                    AlertPolicy.family != AnomalyFamily.PROPERTY_WATCH.value,
                    cast(LogSource.id, String) == AlertPolicy.scope_value,
                ),
            )
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .outerjoin(
                PropertyWatch,
                and_(
                    AlertPolicy.scope_kind == AlertPolicyScope.SOURCE.value,
                    AlertPolicy.family == AnomalyFamily.PROPERTY_WATCH.value,
                    cast(PropertyWatch.id, String) == AlertPolicy.scope_value,
                ),
            )
            .order_by(
                AlertPolicy.family,
                AlertPolicy.scope_kind,
                AlertPolicy.scope_value,
                AlertPolicy.detector_type,
                AlertPolicy.instance_id,
            )
        )
    ).all()
    out: list[AlertPolicyOut] = []
    for row, source_name, source_class, watch_name, instance_name in result:
        item = AlertPolicyOut.model_validate(row)
        item.instance_name = instance_name
        item.source_name = source_name if source_name is not None else watch_name
        if row.scope_kind == AlertPolicyScope.CLASS.value:
            item.source_class = SourceClass(row.scope_value)
        elif source_name is not None:
            item.source_class = (
                SourceClass(source_class)
                if source_class is not None
                else SourceClass.UNCLASSIFIED
            )
        out.append(item)
    return out


@router.put("", response_model=AlertPolicyOut)
async def upsert_policy(
    payload: AlertPolicyUpsert,
    session: SessionDep,
    principal: WritePrincipal,
) -> AlertPolicyOut:
    """Create or replace exactly one row; audited with before and after."""
    class_scope, source_scope, detector = await _checked(session, payload)
    if class_scope is not None:
        scope_value = class_scope.value
    else:
        assert source_scope is not None
        scope_value = source_scope.value
    row = await session.scalar(_query(payload, scope_value, detector))
    before = _snapshot(row) if row is not None else None
    if row is None:
        row = AlertPolicy(
            instance_id=payload.instance_id,
            scope_kind=payload.scope_kind.value,
            scope_value=scope_value,
            family=payload.family.value,
            detector_type=detector,
        )
        session.add(row)
    row.mode = payload.mode
    row.updated_by = principal.subject
    await session.flush()
    after = _snapshot(row)
    await record_audit(
        session,
        action="alert_policy.upsert",
        actor=principal.subject,
        object_type="alert_policy",
        # Keyed by the complete identity: a SOURCE row's scope value is a UUID,
        # which value-level redaction would mask in `detail`.
        object_id=(
            f"{payload.instance_id or 'global'}:{row.scope_kind}:{row.scope_value}:"
            f"{row.family}:{row.detector_type or '*'}"
        ),
        detail={"before": before, "after": after},
    )
    instance_name = await _instance_name(session, row.instance_id)
    item = AlertPolicyOut.model_validate(row)
    item.instance_name = instance_name
    if class_scope is not None:
        item.source_class = class_scope.source_class
    elif source_scope is not None:
        item.source_name = source_scope.source_name
        item.source_class = source_scope.source_class
    return item


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: uuid.UUID,
    session: SessionDep,
    principal: WritePrincipal,
) -> Response:
    row = await session.get(AlertPolicy, policy_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert policy row not found")
    snapshot = _snapshot(row)
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        action="alert_policy.delete",
        actor=principal.subject,
        object_type="alert_policy",
        object_id=(
            f"{row.instance_id or 'global'}:{row.scope_kind}:{row.scope_value}:"
            f"{row.family}:{row.detector_type or '*'}"
        ),
        detail={"before": snapshot, "after": None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _run_out(run: AlertDigestRun, instance_name: str | None) -> DigestRunOut:
    item = DigestRunOut.model_validate(run)
    item.instance_name = instance_name
    return item


@router.get("/digest", response_model=DigestStatusOut)
async def digest_status(
    session: SessionDep, settings: SettingsDep
) -> DigestStatusOut:
    """The digest's due hour, its last run per instance and the recipient count."""
    recipients = await recipient_addresses(session)
    runs = (
        await session.execute(
            select(AlertDigestRun, QRadarInstance.name)
            .join(QRadarInstance, QRadarInstance.id == AlertDigestRun.instance_id)
            .order_by(
                AlertDigestRun.created_at.desc(),
                AlertDigestRun.digest_date.desc(),
                AlertDigestRun.id.desc(),
            )
        )
    ).all()
    instances = list(
        (
            await session.scalars(select(QRadarInstance).order_by(QRadarInstance.name))
        ).all()
    )
    latest: dict[uuid.UUID, DigestRunOut] = {}
    for run, instance_name in runs:
        latest.setdefault(run.instance_id, _run_out(run, instance_name))
    last_run = runs[0][0] if runs else None
    last_run_out = (
        _run_out(last_run, runs[0][1]) if last_run is not None else None
    )
    return DigestStatusOut(
        digest_hour_utc=settings.digest_hour_utc,
        email_configured=settings.smtp_configured,
        recipient_count=len(recipients),
        last_run=last_run_out,
        instances=[
            DigestInstanceStatusOut(
                instance_id=instance.id,
                instance_name=instance.name,
                last_run=latest.get(instance.id),
            )
            for instance in instances
        ],
    )
