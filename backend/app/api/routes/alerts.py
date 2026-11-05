"""Alert lifecycle and notification-history API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.query import (
    MAX_Q_LENGTH,
    AlertFamily,
    AlertFilters,
    AlertSort,
    SortOrder,
    facet_counts,
    normalise_q,
)
from app.alerts.routing import default_policy
from app.alerts.service import AlertResult, AlertService
from app.api.deps import SessionDep, SettingsDep
from app.i18n.refusal import permission_refusal
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, Severity
from app.repositories.audit import record_audit
from app.schemas.alert import (
    AlertActionIn,
    AlertFacetsOut,
    AlertFacetValue,
    AlertFiltersApplied,
    AlertOut,
    AlertPage,
    NotificationOut,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_ALERT_ACK,
    PERM_ALERT_RESOLVE,
    PermissionDenied,
    Principal,
    require_permission,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


async def _enqueue_transition(session: AsyncSession, result: AlertResult) -> None:
    """Record the notification intent for an operator-driven state change.

    Enqueue-only: no notifier map is built here, so an HTTP request never blocks
    on an outbound webhook. The dispatch_notifications worker delivers the rows.
    A no-op transition (a repeated acknowledge) has transition=None and enqueues
    nothing, which is the same dedup rule the anomaly engine follows.
    """
    if result.transition is None:
        return
    dispatcher = NotificationDispatcher(session, notifiers={}, policy=default_policy())
    await dispatcher.enqueue(result.alert, result.transition)


def _validate_range(
    since: datetime | None,
    until: datetime | None,
    max_days: int,
) -> tuple[datetime, datetime] | None:
    """Clamp and validate a caller-supplied creation-time window.

    `None` means the caller asked for no window at all, and no bound is applied.
    Without `since` the window covers the day before its end; without `until` it
    ends now. An unbounded range is a denial of service against our own
    database, so the ceiling is enforced here rather than trusted to the client.
    """
    if since is None and until is None:
        return None
    now = datetime.now(UTC)
    end = until or now
    start = since or (end - timedelta(days=1))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if end <= start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "range end must be after its start"
        )
    if (end - start) > timedelta(days=max_days):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"range exceeds the maximum of {max_days} days",
        )
    return start, end


def _filters(
    *,
    status_filter: AlertStatus | None,
    severity: list[Severity] | None,
    log_source_id: list[uuid.UUID] | None,
    family: AlertFamily | None,
    q: str | None,
    since: datetime | None,
    until: datetime | None,
    max_days: int,
) -> AlertFilters:
    """One validated filter object, shared by the list and its facets."""
    try:
        title_contains = normalise_q(q)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    window = _validate_range(since, until, max_days)
    # Repeated values are deduplicated in first-seen order, so the echo is the
    # filter that was applied rather than the keystrokes that sent it.
    return AlertFilters(
        status=status_filter,
        severities=tuple(dict.fromkeys(severity or ())),
        log_source_ids=tuple(dict.fromkeys(log_source_id or ())),
        family=family,
        since=window[0] if window else None,
        until=window[1] if window else None,
        title_contains=title_contains,
    )


def _applied(filters: AlertFilters) -> AlertFiltersApplied:
    return AlertFiltersApplied(
        status=filters.status,
        severity=list(filters.severities),
        log_source_id=list(filters.log_source_ids),
        family=filters.family,
        q=filters.title_contains,
        since=filters.since,
        until=filters.until,
    )


@router.get("", response_model=AlertPage)
async def list_alerts(
    session: SessionDep,
    settings: SettingsDep,
    status_filter: AlertStatus | None = Query(default=None, alias="status"),
    severity: Annotated[list[Severity] | None, Query()] = None,
    log_source_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    family: AlertFamily | None = Query(default=None),
    q: str | None = Query(default=None, max_length=MAX_Q_LENGTH),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    sort: AlertSort = Query(default=AlertSort.CREATED_AT),
    order: SortOrder = Query(default=SortOrder.DESC),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AlertPage:
    filters = _filters(
        status_filter=status_filter,
        severity=severity,
        log_source_id=log_source_id,
        family=family,
        q=q,
        since=since,
        until=until,
        max_days=settings.api_max_range_days,
    )
    clauses = filters.clauses()
    total = await session.scalar(select(func.count()).select_from(Alert).where(*clauses))
    stmt = (
        select(Alert)
        .where(*clauses)
        .order_by(*filters.order_by(sort, order))
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.scalars(stmt)).all()
    return AlertPage(
        items=[AlertOut.model_validate(r) for r in rows],
        total=int(total or 0),
        limit=limit,
        offset=offset,
        filters_applied=_applied(filters),
        sort=sort,
        order=order,
    )


@router.get("/facets", response_model=AlertFacetsOut)
async def alert_facets(
    session: SessionDep,
    settings: SettingsDep,
    status_filter: AlertStatus | None = Query(default=None, alias="status"),
    severity: Annotated[list[Severity] | None, Query()] = None,
    log_source_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    family: AlertFamily | None = Query(default=None),
    q: str | None = Query(default=None, max_length=MAX_Q_LENGTH),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> AlertFacetsOut:
    """Counts per severity, family and status for the filter chips.

    Declared before `/{alert_id}`: a route after it would parse "facets" as an
    id and answer every facet call with a uuid parse error. The counts describe
    exactly the population the list returns for the same filters (paging and
    ordering do not apply to a count), in one grouped statement.
    """
    filters = _filters(
        status_filter=status_filter,
        severity=severity,
        log_source_id=log_source_id,
        family=family,
        q=q,
        since=since,
        until=until,
        max_days=settings.api_max_range_days,
    )
    severity_counts, family_counts, status_counts = await facet_counts(session, filters)
    return AlertFacetsOut(
        severity=[
            AlertFacetValue(value=value.value, count=severity_counts.get(value.value, 0))
            for value in Severity
        ],
        family=[
            AlertFacetValue(value=value.value, count=family_counts.get(value.value, 0))
            for value in AlertFamily
        ],
        status=[
            AlertFacetValue(value=value.value, count=status_counts.get(value.value, 0))
            for value in AlertStatus
        ],
        total=sum(status_counts.values()),
        filters_applied=_applied(filters),
    )


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: uuid.UUID, session: SessionDep) -> AlertOut:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    return AlertOut.model_validate(alert)


@router.get("/{alert_id}/notifications", response_model=list[NotificationOut])
async def alert_notifications(alert_id: uuid.UUID, session: SessionDep) -> list[NotificationOut]:
    rows = (
        await session.scalars(
            select(AlertNotification)
            .where(AlertNotification.alert_id == alert_id)
            .order_by(AlertNotification.created_at.desc())
        )
    ).all()
    return [NotificationOut.model_validate(r) for r in rows]


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge_alert(
    alert_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> AlertOut:
    _guard(principal, PERM_ALERT_ACK)
    try:
        result = await AlertService(session).acknowledge(alert_id, actor=principal.subject)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _enqueue_transition(session, result)
    await record_audit(
        session, action="alert.acknowledge", actor=principal.subject,
        object_type="alert", object_id=str(alert_id),
    )
    return AlertOut.model_validate(result.alert)


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(
    alert_id: uuid.UUID, payload: AlertActionIn, session: SessionDep, principal: PrincipalDep
) -> AlertOut:
    _guard(principal, PERM_ALERT_RESOLVE)
    try:
        result = await AlertService(session).resolve(
            alert_id, actor=principal.subject, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await _enqueue_transition(session, result)
    await record_audit(
        session, action="alert.resolve", actor=principal.subject,
        object_type="alert", object_id=str(alert_id), detail={"reason": payload.reason},
    )
    return AlertOut.model_validate(result.alert)
