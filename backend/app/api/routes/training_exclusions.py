"""Baseline training exclusion API (SOC-13b, WAF in SOC-13e, IPS in SOC-13g).

Create and revoke need `anomaly:triage`; reading is covered by the router's
`read:anomalies` guard. Phase A excludes a log source's volume. A resolved WAF
anomaly excludes its endpoint, one scope for every metric of that baseline.
A resolved IPS anomaly excludes its entity (`ips:entity`) or its source
(`ips:source`); one source interval covers every source metric together.
The reason is analyst text: it lives on the exclusion row and on the triage event
`comment`, and never reaches the audit detail or a log line.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import and_, false, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly import training_exclusions as te
from app.api.deps import SessionDep, SettingsDep
from app.api.routes.anomalies import PrincipalDep, _guard
from app.history.origin import is_live_clause
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.enums import AnomalyFamily, AnomalyState, TriageEventKind, TriageStatus
from app.models.ips import (
    IpsAnomaly,
    IpsEntity,
    IpsEntityBaseline,
    IpsSourceBaseline,
    IpsSourceBucket,
)
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline, LogSourceMetric
from app.models.training_exclusion import REASON_MAX_LENGTH, BaselineTrainingExclusion
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.models.waf import WafAnomaly, WafEndpoint, WafEndpointBaseline, WafSourceBucket
from app.repositories.audit import record_audit
from app.repositories.triage import TriageRepository
from app.schemas.training_exclusion import (
    ExclusionCreateIn,
    ExclusionListOut,
    ExclusionOut,
    ExclusionRevokeIn,
    ExclusionState,
)
from app.security.rbac import PERM_ANOMALY_TRIAGE

anomaly_router = APIRouter(prefix="/anomalies", tags=["anomalies"])
behavior_router = APIRouter(prefix="/behavior", tags=["behavior"])

_UNPROCESSABLE = status.HTTP_422_UNPROCESSABLE_CONTENT
_CONFLICT = status.HTTP_409_CONFLICT
_STATUS_BY_CODE = {
    te.INTERVAL_INVALID: _UNPROCESSABLE,
    te.INTERVAL_TOO_LONG: _UNPROCESSABLE,
    te.INTERVAL_NOT_WIDENED: _UNPROCESSABLE,
    te.OVERLAP: _CONFLICT,
    te.LIMIT_REACHED: _CONFLICT,
    te.ANOMALY_ACTIVE: _CONFLICT,
    te.NOT_FOUND: status.HTTP_404_NOT_FOUND,
}
_PARAMS_BY_CODE: dict[str, dict[str, object]] = {
    te.INTERVAL_TOO_LONG: {"max": 7},
    te.LIMIT_REACHED: {"max": te.MAX_RELEVANT_PER_SCOPE},
}


def _refuse(code: str, http_status: int | None = None) -> CodedHTTPException:
    return CodedHTTPException(
        http_status or _STATUS_BY_CODE[code], MessageRef(code, _PARAMS_BY_CODE.get(code, {}))
    )


def _reason(value: str) -> str:
    text = value.strip()
    if not 1 <= len(text) <= REASON_MAX_LENGTH:
        raise CodedHTTPException(
            _UNPROCESSABLE, MessageRef(te.CODE_PREFIX + "reason_invalid")
        )
    return text


def _lookback_start(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _state(
    row: BaselineTrainingExclusion,
    lookback_start: datetime,
    rebuilt_at: datetime | None = None,
) -> ExclusionState:
    """`rebuilt_at` is the subject's latest baseline `computed_at`, None when unknown.

    An active row is APPLIED once the baseline was rebuilt after the row was created:
    training reads every active row, so that rebuild used it.
    """
    if row.revoked_at is not None:
        return ExclusionState.REVOKED
    if row.ends_at <= lookback_start:
        return ExclusionState.OUTSIDE_LOOKBACK
    if rebuilt_at is not None and rebuilt_at > row.created_at:
        return ExclusionState.APPLIED
    return ExclusionState.PENDING_REBUILD


async def _rebuilt_at(
    session: AsyncSession, rows: Sequence[BaselineTrainingExclusion]
) -> dict[tuple[str, uuid.UUID], datetime]:
    """Latest baseline `computed_at` per subject. One aggregate per kind present.

    Keyed by `(subject_kind, subject_id)` so a log source, an endpoint, an IPS
    entity and an IPS source that happened to share an id cannot overwrite each
    other. `source` is the IPS source subject, not Phase A's `log_source`.
    """
    found: dict[tuple[str, uuid.UUID], datetime] = {}
    source_ids = {row.subject_id for row in rows if row.subject_kind == "log_source"}
    if source_ids:
        sources = await session.execute(
            select(LogSourceBaseline.log_source_id, func.max(LogSourceBaseline.computed_at))
            .where(LogSourceBaseline.log_source_id.in_(source_ids))
            .group_by(LogSourceBaseline.log_source_id)
        )
        found.update(
            {("log_source", source_id): at for source_id, at in sources.all() if at is not None}
        )
    endpoint_ids = {row.subject_id for row in rows if row.subject_kind == "endpoint"}
    if endpoint_ids:
        endpoints = await session.execute(
            select(
                WafEndpointBaseline.waf_endpoint_id,
                func.max(WafEndpointBaseline.computed_at),
            )
            .where(WafEndpointBaseline.waf_endpoint_id.in_(endpoint_ids))
            .group_by(WafEndpointBaseline.waf_endpoint_id)
        )
        found.update(
            {("endpoint", endpoint_id): at for endpoint_id, at in endpoints.all() if at is not None}
        )
    entity_ids = {row.subject_id for row in rows if row.subject_kind == "entity"}
    if entity_ids:
        entities = await session.execute(
            select(IpsEntityBaseline.ips_entity_id, func.max(IpsEntityBaseline.computed_at))
            .where(IpsEntityBaseline.ips_entity_id.in_(entity_ids))
            .group_by(IpsEntityBaseline.ips_entity_id)
        )
        found.update(
            {("entity", entity_id): at for entity_id, at in entities.all() if at is not None}
        )
    ips_source_ids = {row.subject_id for row in rows if row.subject_kind == "source"}
    if ips_source_ids:
        ips_sources = await session.execute(
            select(IpsSourceBaseline.log_source_id, func.max(IpsSourceBaseline.computed_at))
            .where(IpsSourceBaseline.log_source_id.in_(ips_source_ids))
            .group_by(IpsSourceBaseline.log_source_id)
        )
        found.update(
            {
                ("source", source_id): at
                for source_id, at in ips_sources.all()
                if at is not None
            }
        )
    return found


def _out(
    row: BaselineTrainingExclusion,
    lookback_start: datetime,
    rebuilt_at: datetime | None = None,
) -> ExclusionOut:
    return ExclusionOut(
        id=row.id,
        origin_family=row.origin_family,
        origin_anomaly_id=row.origin_anomaly_id,
        subject_kind=row.subject_kind,
        subject_id=row.subject_id,
        scope_key=row.scope_key,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        reason=row.reason,
        created_by=row.created_by,
        created_at=row.created_at,
        revoked_by=row.revoked_by,
        revoked_at=row.revoked_at,
        revocation_reason=row.revocation_reason,
        state=_state(row, lookback_start, rebuilt_at),
    )


async def _append_event(
    session: AsyncSession,
    row: BaselineTrainingExclusion,
    kind: TriageEventKind,
    *,
    actor: str,
    reason: str,
) -> None:
    """Append the activity event at the anomaly's current triage version."""
    family = AnomalyFamily(row.origin_family)
    repo = TriageRepository(session)
    triage = await repo.row(family, row.origin_anomaly_id, for_update=True)
    now = datetime.now(UTC)
    if triage is None:
        try:
            async with session.begin_nested():
                session.add(
                    AnomalyTriage(
                        family=family,
                        anomaly_id=row.origin_anomaly_id,
                        status=TriageStatus.NEW,
                        updated_by=actor,
                        updated_at=now,
                        version=1,
                    )
                )
        except IntegrityError:
            pass
        triage = await repo.row(family, row.origin_anomaly_id, for_update=True)
        assert triage is not None
    session.add(
        AnomalyTriageEvent(
            triage_id=triage.id,
            kind=kind,
            actor=actor,
            created_at=now,
            to_value=str(row.id),
            comment=reason,
            triage_version=triage.version,
        )
    )
    await session.flush()


class _Target(NamedTuple):
    """The subject and incident span the server derives from one anomaly."""

    origin_family: str
    subject_kind: str
    subject_id: uuid.UUID
    scope_key: str
    incident_start: datetime
    incident_end: datetime
    bucket_seconds: int


async def _waf_bucket_seconds(
    session: AsyncSession, endpoint_id: uuid.UUID, detected_at: datetime
) -> int | None:
    """Live source-bucket width at or before the incident, or None when there is none.

    A history row carries the replay's width, not the width the live detector used.
    No live row means no width: the caller fails closed rather than guessing one.
    """
    return await session.scalar(
        select(WafSourceBucket.bucket_seconds)
        .join(WafEndpoint, WafEndpoint.log_source_id == WafSourceBucket.log_source_id)
        .where(
            WafEndpoint.id == endpoint_id,
            WafSourceBucket.bucket_start <= detected_at,
            is_live_clause(WafSourceBucket),
        )
        .order_by(WafSourceBucket.bucket_start.desc())
        .limit(1)
    )


async def _waf_target(session: AsyncSession, anomaly_id: uuid.UUID) -> _Target:
    """Subject, scope and incident span of a resolved WAF anomaly."""
    anomaly = await session.get(WafAnomaly, anomaly_id)
    if anomaly is None:
        raise _refuse(te.NOT_FOUND)
    if anomaly.state != AnomalyState.RESOLVED:
        raise _refuse(te.ANOMALY_ACTIVE)
    bucket_seconds = await _waf_bucket_seconds(
        session, anomaly.waf_endpoint_id, anomaly.detected_at
    )
    if bucket_seconds is None:
        raise _refuse(te.INTERVAL_INVALID)
    return _Target(
        origin_family=AnomalyFamily.WAF.value,
        subject_kind="endpoint",
        subject_id=anomaly.waf_endpoint_id,
        scope_key=te.WAF_ENDPOINT_SCOPE,
        incident_start=anomaly.detected_at,
        incident_end=anomaly.resolved_at or anomaly.anomaly_end or anomaly.detected_at,
        bucket_seconds=bucket_seconds,
    )


async def _ips_bucket_seconds(
    session: AsyncSession,
    *,
    detected_at: datetime,
    entity_id: uuid.UUID | None = None,
    log_source_id: uuid.UUID | None = None,
) -> int | None:
    """Live IPS source-bucket width at or before the incident, or None.

    An entity anomaly has no log source of its own, so the width is read from
    the entity's source. A history row carries the replay's width, not the
    width the live detector used. No live row means no width: the caller fails
    closed rather than guessing one.
    """
    if entity_id is None and log_source_id is None:
        return None
    stmt = select(IpsSourceBucket.bucket_seconds).where(
        IpsSourceBucket.bucket_start <= detected_at,
        is_live_clause(IpsSourceBucket),
    )
    if entity_id is not None:
        stmt = stmt.join(
            IpsEntity, IpsEntity.log_source_id == IpsSourceBucket.log_source_id
        ).where(IpsEntity.id == entity_id)
    else:
        stmt = stmt.where(IpsSourceBucket.log_source_id == log_source_id)
    return await session.scalar(
        stmt.order_by(IpsSourceBucket.bucket_start.desc()).limit(1)
    )


async def _ips_target(session: AsyncSession, anomaly_id: uuid.UUID) -> _Target:
    """Subject, scope and incident span of a resolved IPS anomaly.

    The subject check is mutually exclusive. An entity incident — including a
    new destination port, which has no rate baseline of its own — maps to
    `ips:entity` and the entity id. A source incident maps to `ips:source` and
    the log source id, one scope for every source metric.
    """
    anomaly = await session.get(IpsAnomaly, anomaly_id)
    if anomaly is None:
        raise _refuse(te.NOT_FOUND)
    if anomaly.state != AnomalyState.RESOLVED:
        raise _refuse(te.ANOMALY_ACTIVE)
    if anomaly.ips_entity_id is not None:
        subject_kind = "entity"
        subject_id = anomaly.ips_entity_id
        scope_key = te.IPS_ENTITY_SCOPE
        bucket_seconds = await _ips_bucket_seconds(
            session, detected_at=anomaly.detected_at, entity_id=anomaly.ips_entity_id
        )
    else:
        source_id = anomaly.log_source_id
        assert source_id is not None
        subject_kind = "source"
        subject_id = source_id
        scope_key = te.IPS_SOURCE_SCOPE
        bucket_seconds = await _ips_bucket_seconds(
            session, detected_at=anomaly.detected_at, log_source_id=source_id
        )
    if bucket_seconds is None:
        raise _refuse(te.INTERVAL_INVALID)
    return _Target(
        origin_family=AnomalyFamily.IPS.value,
        subject_kind=subject_kind,
        subject_id=subject_id,
        scope_key=scope_key,
        incident_start=anomaly.detected_at,
        incident_end=anomaly.resolved_at or anomaly.anomaly_end or anomaly.detected_at,
        bucket_seconds=bucket_seconds,
    )


def _audit_detail(row: BaselineTrainingExclusion, actor: str) -> dict[str, object]:
    """Ids, UTC bounds, subject, scope and actor. Never the reason.

    A bare UUID string would be masked by the audit redactor (it looks like a token), so
    the ids go in as `kind:uuid` pairs, which keeps them joinable.
    """
    return {
        "anomaly": f"{row.origin_family}:{row.origin_anomaly_id}",
        "subject": f"{row.subject_kind}:{row.subject_id}",
        "scope_key": row.scope_key,
        "starts_at": row.starts_at.astimezone(UTC).isoformat(),
        "ends_at": row.ends_at.astimezone(UTC).isoformat(),
        "actor": actor,
    }


@anomaly_router.post(
    "/{family}/{anomaly_id}/training-exclusions",
    response_model=ExclusionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_training_exclusion(
    family: str,
    anomaly_id: uuid.UUID,
    payload: ExclusionCreateIn,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> ExclusionOut:
    """Exclude a UTC interval of a resolved incident from baseline training.

    Phase A (`log_source`), WAF (`waf`) and IPS (`ips`) are accepted. 201 for a
    new row, 200 for the exact active row that already exists. The server
    derives the subject, scope and incident span; the client may only widen it.
    """
    _guard(principal, PERM_ANOMALY_TRIAGE)
    if family not in (
        AnomalyFamily.LOG_SOURCE.value,
        AnomalyFamily.WAF.value,
        AnomalyFamily.IPS.value,
    ):
        raise _refuse(te.FAMILY_UNSUPPORTED, _UNPROCESSABLE)
    reason = _reason(payload.reason)
    if family == AnomalyFamily.WAF.value:
        target = await _waf_target(session, anomaly_id)
    elif family == AnomalyFamily.IPS.value:
        target = await _ips_target(session, anomaly_id)
    else:
        anomaly = await session.get(LogSourceAnomaly, anomaly_id)
        if anomaly is None:
            raise _refuse(te.NOT_FOUND)
        if anomaly.state != AnomalyState.RESOLVED:
            raise _refuse(te.ANOMALY_ACTIVE)
        bucket_seconds = await session.scalar(
            select(LogSourceMetric.bucket_seconds)
            .where(
                LogSourceMetric.log_source_id == anomaly.log_source_id,
                LogSourceMetric.bucket_start <= anomaly.detected_at,
                # Align to the width the live detector used: a learning-only history
                # bucket carries the replay's width, not the incident's (§B.4.7).
                is_live_clause(LogSourceMetric),
            )
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
        )
        if bucket_seconds is None:
            # No live metric to read the bucket width from: fail closed rather than guess one.
            raise _refuse(te.INTERVAL_INVALID)
        target = _Target(
            origin_family=AnomalyFamily.LOG_SOURCE.value,
            subject_kind="log_source",
            subject_id=anomaly.log_source_id,
            scope_key=te.LOG_SOURCE_VOLUME_SCOPE,
            incident_start=anomaly.detected_at,
            incident_end=anomaly.resolved_at or anomaly.anomaly_end or anomaly.detected_at,
            bucket_seconds=bucket_seconds,
        )
    code = te.validate_interval(
        payload.starts_at,
        payload.ends_at,
        bucket_seconds=target.bucket_seconds,
        incident_start=target.incident_start,
        incident_end=target.incident_end,
    )
    if code is not None:
        raise _refuse(code)
    now = datetime.now(UTC)
    lookback_start = _lookback_start(now, settings.baseline_lookback_days)
    try:
        # A savepoint, so a lost race on the unique index leaves the request usable.
        async with session.begin_nested():
            result = await te.create(
                session,
                origin_family=target.origin_family,
                origin_anomaly_id=anomaly_id,
                subject_kind=target.subject_kind,
                subject_id=target.subject_id,
                scope_key=target.scope_key,
                starts_at=payload.starts_at,
                ends_at=payload.ends_at,
                reason=reason,
                created_by=principal.subject,
                lookback_start=lookback_start,
            )
        row, created = result.row, result.created
    except te.ExclusionError as exc:
        raise _refuse(exc.code) from exc
    except IntegrityError as exc:
        existing = await te.find_active_exact(
            session,
            subject_kind=target.subject_kind,
            subject_id=target.subject_id,
            scope_key=target.scope_key,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
        if existing is None:
            raise _refuse(te.OVERLAP) from exc
        row, created = existing, False
    if created:
        await _append_event(
            session, row, TriageEventKind.BASELINE_EXCLUSION_CREATED,
            actor=principal.subject, reason=reason,
        )
        await record_audit(
            session,
            action="baseline_exclusion.create",
            actor=principal.subject,
            object_type="baseline_training_exclusion",
            object_id=str(row.id),
            detail=_audit_detail(row, principal.subject),
        )
    else:
        response.status_code = status.HTTP_200_OK
    rebuilt = await _rebuilt_at(session, [row])
    return _out(row, lookback_start, rebuilt.get((row.subject_kind, row.subject_id)))


@behavior_router.get("/training-exclusions", response_model=ExclusionListOut)
async def list_training_exclusions(
    session: SessionDep,
    settings: SettingsDep,
    subject_kind: str | None = Query(default=None, max_length=24),
    subject_id: uuid.UUID | None = None,
    scope_key: str | None = Query(default=None, max_length=80),
    state: ExclusionState | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> ExclusionListOut:
    """Newest first, bounded. Filtered by the effect state when one is named."""
    lookback_start = _lookback_start(datetime.now(UTC), settings.baseline_lookback_days)
    model = BaselineTrainingExclusion
    stmt = select(model)
    if subject_kind is not None:
        stmt = stmt.where(model.subject_kind == subject_kind)
    if subject_id is not None:
        stmt = stmt.where(model.subject_id == subject_id)
    if scope_key is not None:
        stmt = stmt.where(model.scope_key == scope_key)
    if state is ExclusionState.REVOKED:
        stmt = stmt.where(model.revoked_at.is_not(None))
    elif state is ExclusionState.OUTSIDE_LOOKBACK:
        stmt = stmt.where(model.revoked_at.is_(None), model.ends_at <= lookback_start)
    elif state in (ExclusionState.PENDING_REBUILD, ExclusionState.APPLIED):
        # The same rule as `_state`, in SQL: applied when the subject's latest baseline
        # rebuild is newer than the row. Each family reads its own computed-at column.
        source_latest = (
            select(func.max(LogSourceBaseline.computed_at))
            .where(LogSourceBaseline.log_source_id == model.subject_id)
            .correlate(model)
            .scalar_subquery()
        )
        endpoint_latest = (
            select(func.max(WafEndpointBaseline.computed_at))
            .where(WafEndpointBaseline.waf_endpoint_id == model.subject_id)
            .correlate(model)
            .scalar_subquery()
        )
        # `source` is the IPS source baseline. Phase A stays on `log_source`.
        entity_latest = (
            select(func.max(IpsEntityBaseline.computed_at))
            .where(IpsEntityBaseline.ips_entity_id == model.subject_id)
            .correlate(model)
            .scalar_subquery()
        )
        ips_source_latest = (
            select(func.max(IpsSourceBaseline.computed_at))
            .where(IpsSourceBaseline.log_source_id == model.subject_id)
            .correlate(model)
            .scalar_subquery()
        )
        applied = or_(
            and_(model.subject_kind == "log_source", source_latest > model.created_at),
            and_(model.subject_kind == "endpoint", endpoint_latest > model.created_at),
            and_(model.subject_kind == "entity", entity_latest > model.created_at),
            and_(model.subject_kind == "source", ips_source_latest > model.created_at),
        )
        stmt = stmt.where(
            model.revoked_at.is_(None),
            model.ends_at > lookback_start,
            applied if state is ExclusionState.APPLIED else not_(func.coalesce(applied, false())),
        )
    rows = (
        await session.scalars(
            stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit)
        )
    ).all()
    rebuilt = await _rebuilt_at(session, rows)
    return ExclusionListOut(
        items=[
            _out(row, lookback_start, rebuilt.get((row.subject_kind, row.subject_id)))
            for row in rows
        ],
        limit=limit,
    )


@behavior_router.post("/training-exclusions/{exclusion_id}/revoke", response_model=ExclusionOut)
async def revoke_training_exclusion(
    exclusion_id: uuid.UUID,
    payload: ExclusionRevokeIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> ExclusionOut:
    """Revoke an exclusion. Idempotent: a second call returns the revoked row, 200.

    Revoking never overrides the automatic exclusion of a detected incident span.
    """
    _guard(principal, PERM_ANOMALY_TRIAGE)
    reason = _reason(payload.reason)
    try:
        result = await te.revoke(
            session, exclusion_id, revoked_by=principal.subject, reason=reason
        )
    except te.ExclusionError as exc:
        raise _refuse(exc.code) from exc
    if result.changed:
        await _append_event(
            session, result.row, TriageEventKind.BASELINE_EXCLUSION_REVOKED,
            actor=principal.subject, reason=reason,
        )
        await record_audit(
            session,
            action="baseline_exclusion.revoke",
            actor=principal.subject,
            object_type="baseline_training_exclusion",
            object_id=str(result.row.id),
            detail=_audit_detail(result.row, principal.subject),
        )
    rebuilt = await _rebuilt_at(session, [result.row])
    lookback_start = _lookback_start(datetime.now(UTC), settings.baseline_lookback_days)
    return _out(
        result.row,
        lookback_start,
        rebuilt.get((result.row.subject_kind, result.row.subject_id)),
    )
