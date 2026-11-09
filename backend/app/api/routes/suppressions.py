"""SOC-17d suppressions: the quiet periods and their "don't alert this again".

Mounted with ``read:alerts`` (the quiet-periods list explains what pages a
human, exactly like the policy list). Every write additionally checks
``alert:suppress`` in the handler, and a suppression written over an anomaly
also needs the anomaly surface's ``read:anomalies``: the caller must be able to
read the object they quiet (design §5.7, §5.8).

Every route derives the condition -- ``(family, subject_id, detector_type)`` --
server-side from the alert or anomaly row. A client-supplied detector string is
never trusted: the body has no detector field at all and refuses unknown keys.

The service below is three moves over the table, audited with the before and
after of the structural fields and never the reason text (the triage comment
rule):

  * **create** -- close any expired open row of the condition
    (``ended_at = ends_at``, no actor: it ran out), then insert the new row.
  * **extend** -- an open row of the condition exists: update it in place
    (``ends_at = now + days``), never a second row.
  * **end** -- an early end writes ``ended_at``/``ended_by``. Rows are never
    deleted, and an already-ended row is returned unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select

from app.alerts.suppression import active_suppression
from app.api.deps import SessionDep, requires
from app.i18n.refusal import permission_refusal
from app.models.alert import Alert
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import (
    AnomalyFamily,
    AnomalyType,
    IpsAnomalyType,
    PwAnomalyType,
    RuleAnomalyType,
    WafAnomalyType,
)
from app.models.ips import IpsAnomaly, IpsEntity
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.property_watch import PropertyWatch, PwAnomaly, PwValue
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly
from app.models.waf import WafAnomaly, WafEndpoint
from app.repositories.audit import record_audit
from app.schemas.suppression import (
    BreakthroughAlertOut,
    ConditionSuppressionOut,
    SuppressionCreate,
    SuppressionOut,
)
from app.security.rbac import (
    PERM_ALERT_SUPPRESS,
    PERM_ANOMALY_READ,
    PermissionDenied,
    Principal,
    require_permission,
)

router = APIRouter(tags=["suppressions"])

WritePrincipal = Annotated[Principal, Depends(requires(PERM_ALERT_SUPPRESS))]

#: The families whose subject is a log source: only these may widen a
#: suppression to the whole source (design §5.1). Property watch's subject is a
#: watch, and "the whole source" has no meaning there.
SOURCE_ANCHORED = frozenset(
    {AnomalyFamily.LOG_SOURCE, AnomalyFamily.WAF, AnomalyFamily.IPS}
)

_DETECTORS: dict[AnomalyFamily, tuple[str, ...]] = {
    AnomalyFamily.LOG_SOURCE: tuple(item.value for item in AnomalyType),
    AnomalyFamily.WAF: tuple(item.value for item in WafAnomalyType),
    AnomalyFamily.IPS: tuple(item.value for item in IpsAnomalyType),
    AnomalyFamily.PROPERTY_WATCH: tuple(item.value for item in PwAnomalyType),
    AnomalyFamily.RULE: tuple(item.value for item in RuleAnomalyType),
}

#: The `alert.source_type` values of the four anomaly producers, by family
#: (`ips_entity` and `ips_source` are one family). The four non-anomaly
#: producers have no detector condition to suppress (design §1.1).
_ALERT_FAMILY: dict[str, AnomalyFamily] = {
    "log_source": AnomalyFamily.LOG_SOURCE,
    "waf_endpoint": AnomalyFamily.WAF,
    "ips_entity": AnomalyFamily.IPS,
    "ips_source": AnomalyFamily.IPS,
    "pw_value": AnomalyFamily.PROPERTY_WATCH,
    "analytics_rule": AnomalyFamily.RULE,
}


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def _gone(message: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, message)


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


def _family(value: str) -> AnomalyFamily:
    """Parse the family after authorization, so a refused caller learns nothing."""
    try:
        return AnomalyFamily(value)
    except ValueError as exc:
        raise _unprocessable(
            "unknown anomaly family: expected log_source, waf, ips, property_watch or rule"
        ) from exc


def _snapshot(row: AlertSuppression) -> dict[str, object]:
    """The structural fields an audit before/after carries. Never the reason."""
    return {
        "family": row.family,
        "subject_id": str(row.subject_id),
        "detector_type": row.detector_type,
        "kind": str(row.kind),
        "owner": row.owner,
        "starts_at": row.starts_at.isoformat(),
        "ends_at": row.ends_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "ended_by": row.ended_by,
        "reference": row.reference,
    }


def _object_id(row: AlertSuppression) -> str:
    return f"{row.family}:{row.subject_id}:{row.detector_type or '*'}"


# ------------------------------------------------------------ condition lookup
async def _condition_from_anomaly(
    session: SessionDep, family: AnomalyFamily, anomaly_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """The (subject, detector) a suppression over this anomaly governs.

    The subject is the producer's log source for Phase A, WAF and both IPS
    kinds (an entity anomaly maps back through ``IpsEntity.log_source_id``),
    and the watch id for property watch (design §5.2).
    """
    if family is AnomalyFamily.LOG_SOURCE:
        phase_a = await session.get(LogSourceAnomaly, anomaly_id)
        if phase_a is None:
            raise _gone("anomaly not found")
        return phase_a.log_source_id, str(phase_a.anomaly_type)
    if family is AnomalyFamily.WAF:
        waf = await session.get(WafAnomaly, anomaly_id)
        if waf is None:
            raise _gone("anomaly not found")
        endpoint = await session.get(WafEndpoint, waf.waf_endpoint_id)
        if endpoint is None:
            raise _gone("the anomaly's endpoint no longer exists")
        return endpoint.log_source_id, str(waf.detector_type)
    if family is AnomalyFamily.IPS:
        ips = await session.get(IpsAnomaly, anomaly_id)
        if ips is None:
            raise _gone("anomaly not found")
        if ips.log_source_id is not None:
            return ips.log_source_id, str(ips.detector_type)
        if ips.ips_entity_id is not None:
            entity = await session.get(IpsEntity, ips.ips_entity_id)
            if entity is not None and entity.log_source_id is not None:
                return entity.log_source_id, str(ips.detector_type)
        raise _gone("the anomaly names no log source")
    if family is AnomalyFamily.RULE:
        rule = await session.get(RuleAnomaly, anomaly_id)
        if rule is None:
            raise _gone("anomaly not found")
        return rule.rule_id, str(rule.detector_type)
    pw = await session.get(PwAnomaly, anomaly_id)
    if pw is None:
        raise _gone("anomaly not found")
    return pw.watch_id, str(pw.detector_type)


async def _condition_from_alert(
    session: SessionDep, alert: Alert, *, whole_source: bool
) -> tuple[AnomalyFamily, uuid.UUID, str | None]:
    """The (family, subject, detector) this alert's condition names.

    The detector is recovered through ``source_anomaly_ids`` (the link every
    anomaly producer fills), never from the title. A whole-source scope needs
    only the subject, so it survives a pruned anomaly row; the narrow condition
    does not. Inventory alerts carry no anomaly ids and have no condition.
    """
    family = _ALERT_FAMILY.get(alert.source_type)
    if family is None:
        raise _unprocessable(
            f"an alert of type {alert.source_type} has no detector condition to suppress"
        )
    if whole_source:
        if family is AnomalyFamily.PROPERTY_WATCH:
            raise _unprocessable("property watch has no whole-source scope")
        if family is AnomalyFamily.RULE:
            raise _unprocessable("a rule has no whole-source scope")
        if alert.source_type == "log_source":
            if alert.source_id is None:
                raise _gone("the alert names no log source")
            return family, alert.source_id, None
        if alert.source_type == "waf_endpoint":
            endpoint = (
                await session.get(WafEndpoint, alert.source_id)
                if alert.source_id is not None
                else None
            )
            if endpoint is None:
                raise _gone("the alert's endpoint no longer exists")
            return family, endpoint.log_source_id, None
        # ips_entity / ips_source: an entity alert maps back to its log source.
        if alert.source_type == "ips_entity" and alert.source_id is not None:
            entity = await session.get(IpsEntity, alert.source_id)
            if entity is not None and entity.log_source_id is not None:
                return family, entity.log_source_id, None
        if alert.source_type == "ips_source" and alert.source_id is not None:
            return family, alert.source_id, None
        raise _gone("the alert names no log source")
    if not alert.source_anomaly_ids:
        raise _unprocessable("the alert carries no anomaly to derive a condition from")
    anomaly_id = uuid.UUID(alert.source_anomaly_ids[0])
    subject, detector = await _condition_from_anomaly(session, family, anomaly_id)
    return family, subject, detector


# ------------------------------------------------------------- create or extend
async def _create_or_extend(
    session: SessionDep,
    *,
    family: AnomalyFamily,
    subject_id: uuid.UUID,
    detector: str | None,
    payload: SuppressionCreate,
    principal: Principal,
) -> AlertSuppression:
    """Per §5.1-§5.2: close an expired row, then create or extend the open one."""
    if detector is not None and detector not in _DETECTORS[family]:
        raise _unprocessable(f"unknown detector for family {family.value}: {detector}")
    now = datetime.now(UTC)
    # The partial unique index cannot test time, so an expired row is closed
    # here, in the same transaction, before the open one is created.
    row = await session.scalar(
        select(AlertSuppression).where(
            AlertSuppression.family == family.value,
            AlertSuppression.subject_id == subject_id,
            AlertSuppression.ended_at.is_(None),
            (
                AlertSuppression.detector_type.is_(None)
                if detector is None
                else AlertSuppression.detector_type == detector
            ),
        )
    )
    closed_expired: dict[str, object] | None = None
    if row is not None and row.ends_at <= now:
        row.ended_at = row.ends_at
        closed_expired = _snapshot(row)
        row = None
    if row is None:
        row = AlertSuppression(
            family=family.value,
            subject_id=subject_id,
            detector_type=detector,
            kind=payload.kind,
            reason=payload.reason,
            owner=payload.owner or principal.subject,
            starts_at=now,
            ends_at=now + timedelta(days=payload.days),
            reference=payload.reference,
            created_by=principal.subject,
        )
        session.add(row)
        await session.flush()
        action = "alert_suppression.create"
        before: dict[str, object] | None = None
    else:
        before = _snapshot(row)
        # Extend or shorten the open row in place: `ends_at` moves to
        # now + days, and the 30-day cap keeps the window a timer.
        ends = now + timedelta(days=payload.days)
        if ends > row.starts_at + timedelta(days=30):
            raise _unprocessable(
                "extending this suppression past its start by more than 30 days is not "
                "allowed; end it and create a new one"
            )
        row.ends_at = ends
        row.reason = payload.reason
        row.owner = payload.owner or principal.subject
        row.kind = payload.kind
        row.reference = payload.reference
        await session.flush()
        action = "alert_suppression.extend"
    after = _snapshot(row)
    # The reason text is on the row only: never in the audit detail.
    detail: dict[str, object] = {"before": before, "after": after}
    if closed_expired is not None:
        detail["closed_expired"] = closed_expired
    await record_audit(
        session,
        action=action,
        actor=principal.subject,
        object_type="alert_suppression",
        object_id=_object_id(row),
        detail=detail,
    )
    return row


def _out(row: AlertSuppression, *, subject_name: str | None, hit_count: int) -> SuppressionOut:
    item = SuppressionOut.model_validate(row)
    item.subject_name = subject_name
    item.hit_count = hit_count
    return item


@router.post("/alerts/{alert_id}/suppress", response_model=SuppressionOut)
async def suppress_from_alert(
    alert_id: uuid.UUID,
    payload: SuppressionCreate,
    session: SessionDep,
    principal: WritePrincipal,
) -> SuppressionOut:
    """"Don't alert this again", starting from an alert row.

    The router mount already checked ``read:alerts`` (the alert surface's read);
    ``alert:suppress`` came with ``WritePrincipal``. The condition is derived
    from the row, never from the body.
    """
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise _gone("alert not found")
    family, subject_id, detector = await _condition_from_alert(
        session, alert, whole_source=payload.whole_source
    )
    row = await _create_or_extend(
        session,
        family=family,
        subject_id=subject_id,
        detector=None if payload.whole_source else detector,
        payload=payload,
        principal=principal,
    )
    name = await _subject_name(session, row.family, row.subject_id)
    return _out(row, subject_name=name, hit_count=0)


async def _subject_name(
    session: SessionDep, family: str, subject_id: uuid.UUID
) -> str | None:
    if family == AnomalyFamily.PROPERTY_WATCH.value:
        watch = await session.get(PropertyWatch, subject_id)
        return watch.name if watch is not None else None
    if family == AnomalyFamily.RULE.value:
        rule = await session.get(AnalyticsRule, subject_id)
        return rule.name if rule is not None else None
    source = await session.get(LogSource, subject_id)
    return source.name if source is not None else None


@router.post(
    "/anomalies/{family}/{anomaly_id}/suppress", response_model=SuppressionOut
)
async def suppress_from_anomaly(
    family: str,
    anomaly_id: uuid.UUID,
    payload: SuppressionCreate,
    session: SessionDep,
    principal: WritePrincipal,
) -> SuppressionOut:
    """The same action, starting from an anomaly row of one family.

    Needs the anomaly surface's read permission beside ``alert:suppress``: the
    caller must be able to read the incident they quiet. The family is parsed
    after both checks, so a refused caller learns nothing.
    """
    _guard(principal, PERM_ANOMALY_READ)
    resolved = _family(family)
    subject_id, detector = await _condition_from_anomaly(session, resolved, anomaly_id)
    wide: str | None = detector
    if payload.whole_source:
        if resolved is AnomalyFamily.RULE:
            raise _unprocessable("a rule has no whole-source scope")
        if resolved not in SOURCE_ANCHORED:
            raise _unprocessable("property watch has no whole-source scope")
        wide = None
    row = await _create_or_extend(
        session,
        family=resolved,
        subject_id=subject_id,
        detector=wide,
        payload=payload,
        principal=principal,
    )
    name = await _subject_name(session, row.family, row.subject_id)
    return _out(row, subject_name=name, hit_count=0)


@router.get("/suppressions", response_model=list[SuppressionOut])
async def list_suppressions(
    session: SessionDep,
    active: bool = Query(default=True),
) -> list[SuppressionOut]:
    """The quiet-periods list: suppressions with their hit counts, plus the
    active maintenance windows as read-only rows (design §5.5, §5.6).

    One grouped query over the suppressions, one over the hits and one over the
    maintenance sources, whatever the row count.
    """
    now = datetime.now(UTC)
    conditions = []
    if active:
        conditions += [
            AlertSuppression.ended_at.is_(None),
            AlertSuppression.starts_at <= now,
            AlertSuppression.ends_at > now,
        ]
    rows = (
        await session.execute(
            select(
                AlertSuppression,
                LogSource.name,
                PropertyWatch.name,
            )
            .select_from(AlertSuppression)
            .outerjoin(
                LogSource,
                and_(
                    AlertSuppression.family != AnomalyFamily.PROPERTY_WATCH.value,
                    LogSource.id == AlertSuppression.subject_id,
                ),
            )
            .outerjoin(
                PropertyWatch,
                and_(
                    AlertSuppression.family
                    == AnomalyFamily.PROPERTY_WATCH.value,
                    PropertyWatch.id == AlertSuppression.subject_id,
                ),
            )
            .where(*conditions)
            .order_by(AlertSuppression.ends_at, AlertSuppression.id)
        )
    ).all()
    hits = dict(
        (
            await session.execute(
                select(
                    AlertSuppressionHit.suppression_id,
                    func.count().label("hits"),
                ).group_by(AlertSuppressionHit.suppression_id)
            )
        ).all()
    )
    items = [
        _out(
            row,
            subject_name=source_name if source_name is not None else watch_name,
            hit_count=hits.get(row.id, 0),
        )
        for row, source_name, watch_name in rows
    ]
    if active:
        items.extend(await _maintenance_rows(session, now))
    return items


async def _maintenance_rows(session: SessionDep, now: datetime) -> list[SuppressionOut]:
    """Active maintenance windows as read-only MAINTENANCE rows (design §5.6).

    Same activity rule as ``app.anomaly.seasonal.in_maintenance``: the flag is
    on and no end has passed. The row links to the source's page through
    ``subject_id``; it has no id of its own and counts no hits.
    """
    sources = (
        await session.execute(
            select(
                LogSource.id,
                LogSource.name,
                LogSource.owner,
                LogSource.maintenance_until,
                LogSource.maintenance_reason,
            ).where(
                LogSource.maintenance_mode.is_(True),
                or_(LogSource.maintenance_until.is_(None), LogSource.maintenance_until > now),
            )
        )
    ).all()
    return [
        SuppressionOut(
            id=None,
            family=AnomalyFamily.LOG_SOURCE,
            subject_id=source_id,
            detector_type=None,
            kind="MAINTENANCE",
            reason=reason,
            owner=owner,
            starts_at=None,
            ends_at=until,
            ended_at=None,
            ended_by=None,
            reference=None,
            created_by=None,
            created_at=None,
            updated_at=None,
            subject_name=name,
            read_only=True,
            hit_count=None,
        )
        for source_id, name, owner, until, reason in sources
    ]


@router.post("/suppressions/{suppression_id}/end", response_model=SuppressionOut)
async def end_suppression(
    suppression_id: uuid.UUID,
    session: SessionDep,
    principal: WritePrincipal,
) -> SuppressionOut:
    """End the quiet period early. Rows are never deleted; an already-ended row
    is returned unchanged (and audited once only)."""
    row = await session.get(AlertSuppression, suppression_id)
    if row is None:
        raise _gone("suppression not found")
    now = datetime.now(UTC)
    if row.ended_at is None:
        before = _snapshot(row)
        row.ended_at = min(now, row.ends_at)
        row.ended_by = principal.subject
        await session.flush()
        await record_audit(
            session,
            action="alert_suppression.end",
            actor=principal.subject,
            object_type="alert_suppression",
            object_id=_object_id(row),
            detail={"before": before, "after": _snapshot(row)},
        )
    name = await _subject_name(session, row.family, row.subject_id)
    hit_count = await session.scalar(
        select(func.count())
        .select_from(AlertSuppressionHit)
        .where(AlertSuppressionHit.suppression_id == row.id)
    )
    return _out(row, subject_name=name, hit_count=int(hit_count or 0))


@router.get(
    "/suppressions/for/{family}/{anomaly_id}", response_model=ConditionSuppressionOut
)
async def suppression_for_condition(
    family: str,
    anomaly_id: uuid.UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(requires(PERM_ANOMALY_READ))],
) -> ConditionSuppressionOut:
    """The active suppression over this anomaly's condition, or an honest null.

    Carries the hit count and the break-through alerts opened during the window
    (read from ``alert`` by source and ``opened_at``, design §5.5), so the
    detail pages need no other backend change.
    """
    resolved = _family(family)
    subject_id, detector = await _condition_from_anomaly(session, resolved, anomaly_id)
    rows = list(
        (
            await session.scalars(
                select(AlertSuppression).where(
                    AlertSuppression.family == resolved.value,
                    AlertSuppression.subject_id == subject_id,
                    AlertSuppression.ended_at.is_(None),
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    active = active_suppression(
        rows, family=resolved, subject_id=subject_id, detector=detector, at=now
    )
    if active is None:
        return ConditionSuppressionOut(
            family=resolved,
            anomaly_id=anomaly_id,
            detector_type=detector,
            suppression=None,
            hit_count=0,
            breakthrough_alerts=[],
        )
    hit_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AlertSuppressionHit)
            .where(AlertSuppressionHit.suppression_id == active.id)
        )
        or 0
    )
    name = await _subject_name(session, active.family, active.subject_id)
    alerts = await _breakthrough_alerts(session, resolved, subject_id, active)
    return ConditionSuppressionOut(
        family=resolved,
        anomaly_id=anomaly_id,
        detector_type=detector,
        suppression=_out(active, subject_name=name, hit_count=hit_count),
        hit_count=hit_count,
        breakthrough_alerts=alerts,
    )


async def _breakthrough_alerts(
    session: SessionDep,
    family: AnomalyFamily,
    subject_id: uuid.UUID,
    row: AlertSuppression,
) -> list[BreakthroughAlertOut]:
    """Alerts of this subject opened inside the window (design §5.5).

    No new column marks a break-through, so the read is by source and
    ``opened_at`` exactly as the design states: every alert the subject's
    condition (or any of its detectors, when the row is subject-wide) opened
    while the suppression held. ``source_id`` holds the endpoint, entity or
    value id for the non-Phase-A producers, so the subject's rows map to alert
    source ids first, in one statement per family.
    """
    source_ids: list[uuid.UUID]
    if family is AnomalyFamily.LOG_SOURCE:
        source_types: list[str] = ["log_source"]
        source_ids = [subject_id]
    elif family is AnomalyFamily.WAF:
        source_types = ["waf_endpoint"]
        source_ids = list(
            (
                await session.scalars(
                    select(WafEndpoint.id).where(WafEndpoint.log_source_id == subject_id)
                )
            ).all()
        )
    elif family is AnomalyFamily.IPS:
        source_types = ["ips_entity", "ips_source"]
        source_ids = [
            subject_id,
            *(
                await session.scalars(
                    select(IpsEntity.id).where(IpsEntity.log_source_id == subject_id)
                )
            ).all(),
        ]
    else:
        source_types = ["pw_value"]
        source_ids = list(
            (
                await session.scalars(
                    select(PwValue.id).where(PwValue.watch_id == subject_id)
                )
            ).all()
        )
    if not source_ids:
        return []
    alerts = (
        await session.execute(
            select(Alert)
            .where(
                Alert.source_type.in_(source_types),
                Alert.source_id.in_(source_ids),
                Alert.opened_at >= row.starts_at,
                Alert.opened_at < row.ends_at,
            )
            .order_by(Alert.opened_at, Alert.id)
        )
    ).scalars()
    return [
        BreakthroughAlertOut(
            id=alert.id,
            title=alert.title,
            severity=str(alert.severity),
            opened_at=alert.opened_at,
            occurrence_count=alert.occurrence_count,
        )
        for alert in alerts
    ]
