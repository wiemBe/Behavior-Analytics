"""Alert lifecycle and notification-history API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.destination_sender import destination_target_id
from app.alerts.query import (
    MAX_Q_LENGTH,
    AlertFamily,
    AlertFilters,
    AlertSort,
    SortOrder,
    alert_family,
    facet_counts,
    normalise_q,
)
from app.alerts.routing import default_policy
from app.alerts.service import AlertResult, AlertService
from app.api.csv_export import (
    _csv_time,
    csv_document,
    csv_enum,
    csv_export_response,
    csv_range_filename,
)
from app.api.deps import SessionDep, SettingsDep
from app.i18n.refusal import permission_refusal
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, NotificationChannel, Severity
from app.models.notification_attempt import MAX_ATTEMPTS_KEPT, NotificationAttempt
from app.models.notification_destination import NotificationDestination
from app.pivot.service import PivotNotFound, alert_pivot
from app.repositories.audit import record_audit
from app.schemas.alert import (
    AlertActionIn,
    AlertFacetsOut,
    AlertFacetValue,
    AlertFiltersApplied,
    AlertOut,
    AlertPage,
    NotificationAttemptOut,
    NotificationOut,
    notification_error_message,
    notification_target_label,
)
from app.schemas.pivot import PivotOut
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


# --------------------------------------------------------------- CSV export
#: The export's column order, fixed by the SOC-8 design (§5): identity, subject,
#: severity, lifecycle and dedup. `evidence_snapshot` and `description` are
#: deliberately absent -- the snapshot is raw evidence, and the export is a list.
ALERT_EXPORT_COLUMNS = (
    "id",
    "family",
    "source_type",
    "source_id",
    "title",
    "severity",
    "status",
    "opened_at",
    "acknowledged_at",
    "acknowledged_by",
    "resolved_at",
    "resolved_by",
    "occurrence_count",
    "fingerprint",
)


def _alert_export_row(alert: Alert) -> tuple[object, ...]:
    """One alert as the export's cells, in column order.

    The family is resolved through the same map the list's `family` filter uses
    (`FAMILY_BY_SOURCE_TYPE`); a `source_type` outside it belongs to no family,
    and its cell is empty rather than forced into one.
    """
    family = alert_family(alert.source_type)
    return (
        alert.id,
        None if family is None else family.value,
        alert.source_type,
        alert.source_id,
        alert.title,
        csv_enum(alert.severity),
        csv_enum(alert.status),
        _csv_time(alert.opened_at),
        _csv_time(alert.acknowledged_at),
        alert.acknowledged_by,
        _csv_time(alert.resolved_at),
        alert.resolved_by,
        alert.occurrence_count,
        alert.fingerprint,
    )


@router.get(
    ".csv",
    response_class=Response,
    responses={
        200: {
            "description": (
                "The filtered alert list as RFC 4180 CSV, at most "
                "API_MAX_EXPORT_ROWS rows in the requested order. The header row is "
                "always present; X-Export-Truncated says the export was cut."
            ),
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def export_alerts_csv(
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
) -> Response:
    """The filtered list as a CSV attachment, one row per alert.

    The filters, their validation and the ordering are the list's own
    (`_filters` and `AlertFilters`), so an export of a narrowed list is exactly
    what the page showed. There is no paging: the file is the first
    `api_max_export_rows` rows in the requested order. One row more than the cap
    is read, so "there are more" is known without a second statement over the
    same rows; when it comes back, the response says `X-Export-Truncated: true`.

    Declared before `/{alert_id}`: a UUID path parameter must never be the route
    that answers an export request. Reads are not audited on this surface (the
    JSON list records none either), so the export records none.
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
    cap = settings.api_max_export_rows
    rows = (
        await session.scalars(
            select(Alert)
            .where(*filters.clauses())
            .order_by(*filters.order_by(sort, order))
            .limit(cap + 1)
        )
    ).all()
    return csv_export_response(
        csv_document(ALERT_EXPORT_COLUMNS, (_alert_export_row(r) for r in rows[:cap])),
        filename=csv_range_filename("alerts", filters.since, filters.until),
        truncated=len(rows) > cap,
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
    id and answer every facet call with a uuid parse error. Each facet is
    counted with all of the request's filters except its own, so a chip shows
    what choosing it would leave; `total` applies every filter and is the same
    total `GET /alerts` reports for this query. Paging and ordering do not
    apply to a count, and the whole answer is one statement.
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
    severity_counts, family_counts, status_counts, total = await facet_counts(session, filters)
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
        total=total,
        filters_applied=_applied(filters),
    )


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: uuid.UUID, session: SessionDep) -> AlertOut:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    return AlertOut.model_validate(alert)


@router.get("/{alert_id}/pivot", response_model=PivotOut)
async def get_alert_pivot(alert_id: uuid.UUID, session: SessionDep) -> PivotOut:
    """AQL text and a console link through the alert's anomaly. Never run by the product."""
    try:
        return await alert_pivot(session, alert_id)
    except PivotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{alert_id}/notifications", response_model=list[NotificationOut])
async def alert_notifications(alert_id: uuid.UUID, session: SessionDep) -> list[NotificationOut]:
    rows = (
        await session.scalars(
            select(AlertNotification)
            .where(AlertNotification.alert_id == alert_id)
            .order_by(AlertNotification.created_at.desc())
        )
    ).all()
    # One query names every destination the listed rows point at.
    wanted = {
        d
        for row in rows
        if row.channel == NotificationChannel.WEBHOOK
        and (d := destination_target_id(row.target)) is not None
    }
    names: dict[uuid.UUID, str] = {}
    if wanted:
        found = await session.execute(
            select(NotificationDestination.id, NotificationDestination.name).where(
                NotificationDestination.id.in_(wanted)
            )
        )
        names = {r.id: r.name for r in found}
    # One query for the attempts of every listed notification, newest first.
    log: dict[uuid.UUID, list[NotificationAttemptOut]] = {}
    if rows:
        attempts = await session.scalars(
            select(NotificationAttempt)
            .where(NotificationAttempt.notification_id.in_([row.id for row in rows]))
            .order_by(NotificationAttempt.attempted_at.desc(), NotificationAttempt.id.desc())
        )
        for attempt in attempts:
            entries = log.setdefault(attempt.notification_id, [])
            if len(entries) < MAX_ATTEMPTS_KEPT:
                entries.append(NotificationAttemptOut.model_validate(attempt))
    return [
        NotificationOut.model_validate(row).model_copy(
            update={
                "attempt_log": log.get(row.id, []),
                "target": notification_target_label(row.channel, row.target, names),
                "error_message": notification_error_message(
                    row.channel, row.target, row.error_message
                ),
            }
        )
        for row in rows
    ]


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
