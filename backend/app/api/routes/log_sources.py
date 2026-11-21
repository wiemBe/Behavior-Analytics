"""Log-source inventory API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.anomaly.thresholds import ThresholdResolver, direction_excluded
from app.api.deps import EffectiveSettingsDep, SessionDep, SettingsDep
from app.api.routes import jdbc as jdbc_routes
from app.fleet.review import review_classification
from app.fleet.service import reclassify_instance
from app.fleet.sources import load_rows
from app.i18n.messages import MessageRef
from app.i18n.refusal import permission_refusal
from app.log_source_policy import source_pause_enabled
from app.models.enums import AnomalyState, Criticality
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource
from app.pivot.service import PivotNotFound, log_source_pivot
from app.repositories.audit import record_audit
from app.repositories.log_source import LogSourceRepository
from app.schemas.fleet import (
    ClassificationOverrideIn,
    FleetSourceOut,
    FleetSourceRow,
    FleetSyncQueued,
    fleet_source_out,
)
from app.schemas.log_source import (
    AnomalySummary,
    LogSourceDetail,
    LogSourcePage,
    LogSourceSummary,
    LogSourceUpdate,
    business_days_usable,
)
from app.schemas.pivot import PivotOut
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_ADMIN,
    PERM_FLEET_READ,
    PERM_LOG_SOURCE_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services.source_incident_closure import close_source_incidents

router = APIRouter(prefix="/log-sources", tags=["log-sources"])
# Included before any `/{log_source_id}` route is declared, so `/log-sources/jdbc` is a path of
# its own and never read as an id.
router.include_router(jdbc_routes.router)

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


def _reject_unusable_business_days(ls: LogSource, updates: dict[str, object]) -> None:
    """422 when the merged row would be business-hours-only with no usable days.

    Item bounds and an explicit empty list sent together with
    `business_hours_only: true` are rejected by the schema. This covers a PATCH
    that sets only one of the two fields.
    """
    hours_only = updates.get("business_hours_only", ls.business_hours_only)
    if hours_only is not True:
        return
    days = updates.get("business_days", ls.business_days)
    if business_days_usable(days):
        return
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[
            {
                "type": "value_error",
                "loc": ["body", "business_days"],
                "msg": (
                    "business_days must list at least one ISO weekday from 1 to 7 "
                    "when business_hours_only is true"
                ),
                "input": days,
            }
        ],
    )


def _jsonable(value: object) -> object:
    """Audit-detail form of a field value: enums and datetimes become strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    return getattr(value, "value", value)


def _to_summary(ls: LogSource, anomaly_counts: dict[uuid.UUID, int]) -> LogSourceSummary:
    s = LogSourceSummary.model_validate(ls)
    s.open_anomaly_count = anomaly_counts.get(ls.id, 0)
    return s


#: The recorded reason of a tuning closure (design §3.1, D-54).
MESSAGE_DIRECTION_DISABLED = MessageRef("noise.tuning.direction_disabled")


def _effective_direction(criticality: Criticality, custom: object) -> str:
    """The direction the engine will use for this source, via the same resolver.

    A malformed stored value is not rejected on read; the resolver returns it and
    ``direction_excluded`` treats anything but the two known values as ``both``.
    """
    return ThresholdResolver().resolve(
        criticality=criticality,
        custom=custom if isinstance(custom, dict) else None,
    ).direction


#: The most ids one `GET /log-sources?ids=` accepts (a repeated query parameter).
MAX_LIST_IDS = 200


@router.get("", response_model=LogSourcePage)
async def list_log_sources(
    session: SessionDep,
    settings: EffectiveSettingsDep,
    monitored_only: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=200),
    ids: Annotated[list[uuid.UUID] | None, Query(max_length=MAX_LIST_IDS)] = None,
    limit: int = Query(default=200, ge=1),
    offset: int = Query(default=0, ge=0),
) -> LogSourcePage:
    """One page of the inventory by name, with the exact total of what matches.

    Three inventory statements however many sources exist: the page, its count and the open-anomaly
    counts of the page's sources only. A `limit` above `api_max_page_size` is clamped to it
    and the envelope says the limit used.
    """
    source_pause = source_pause_enabled(settings)
    bounded = max(1, min(limit, settings.api_max_page_size))
    repo = LogSourceRepository(session)
    sources, total = await repo.list_page(
        limit=bounded, offset=offset, q=q, ids=ids, monitored_only=monitored_only
    )
    counts = await repo.open_anomaly_counts(
        [ls.id for ls in sources], source_pause=source_pause
    )
    return LogSourcePage(
        items=[_to_summary(ls, counts) for ls in sources],
        total=total,
        limit=bounded,
        offset=offset,
    )


@router.get("/{log_source_id}", response_model=LogSourceDetail)
async def get_log_source(
    log_source_id: uuid.UUID, session: SessionDep, settings: EffectiveSettingsDep
) -> LogSourceDetail:
    source_pause = source_pause_enabled(settings)
    repo = LogSourceRepository(session)
    ls = await repo.get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    counts = await repo.open_anomaly_counts([log_source_id], source_pause=source_pause)
    detail = LogSourceDetail.model_validate(ls)
    detail.open_anomaly_count = counts.get(ls.id, 0)
    return detail


@router.get("/{log_source_id}/pivot", response_model=PivotOut)
async def get_log_source_pivot(log_source_id: uuid.UUID, session: SessionDep) -> PivotOut:
    """The source's last hour of events as AQL plus a console link. Never run by the product."""
    try:
        return await log_source_pivot(session, log_source_id)
    except PivotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{log_source_id}/anomalies", response_model=list[AnomalySummary])
async def get_anomalies(
    log_source_id: uuid.UUID, session: SessionDep, settings: EffectiveSettingsDep
) -> list[AnomalySummary]:
    source_pause = source_pause_enabled(settings)
    repo = LogSourceRepository(session)
    if await repo.get(log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    anomalies = await repo.open_anomalies_for(log_source_id, source_pause=source_pause)
    return [AnomalySummary.model_validate(a) for a in anomalies]


@router.patch("/{log_source_id}", response_model=LogSourceDetail)
async def update_log_source(
    log_source_id: uuid.UUID,
    payload: LogSourceUpdate,
    session: SessionDep,
    settings: EffectiveSettingsDep,
    principal: PrincipalDep,
) -> LogSourceDetail:
    """Update operator-owned SOC metadata. QRadar-mirrored fields are read-only
    and not present on the update schema. Requires log_source:write, and the
    change is audited as {field: {from, to}}."""
    source_pause = source_pause_enabled(settings)
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    repo = LogSourceRepository(session)
    ls = await repo.get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")

    changes: dict[str, dict[str, object]] = {}
    updates: dict[str, object] = payload.model_dump(exclude_unset=True)
    # Audit-only: a preview never changes what is saved.
    updates.pop("preview", None)
    _reject_unusable_business_days(ls, updates)
    old_custom = ls.custom_thresholds or {}
    new_custom = updates.get("custom_thresholds", old_custom)
    old_direction = _effective_direction(ls.criticality, old_custom)
    new_direction = _effective_direction(ls.criticality, new_custom)
    newly_excluded = set(direction_excluded(new_direction)) - set(
        direction_excluded(old_direction)
    )
    for field, value in updates.items():
        changes[field] = {"from": _jsonable(getattr(ls, field, None)), "to": _jsonable(value)}
        setattr(ls, field, value)
    await session.flush()
    now = datetime.now(UTC)
    closed = await close_source_incidents(
        session, ls.id, newly_excluded, reason=MESSAGE_DIRECTION_DISABLED,
        actor=principal.subject, now=now, reset_state=AnomalyState.NORMAL, keep_values=True,
    )
    audit_detail: dict[str, object] = {"changes": changes}
    if payload.preview is not None:
        audit_detail["preview"] = payload.preview.model_dump(mode="json")
    if closed:
        audit_detail["closed_incidents"] = closed
    await record_audit(
        session, action="log_source.update", actor=principal.subject,
        object_type="log_source", object_id=str(log_source_id), detail=audit_detail,
    )

    counts = await repo.open_anomaly_counts([log_source_id], source_pause=source_pause)
    detail = LogSourceDetail.model_validate(ls)
    detail.open_anomaly_count = counts.get(ls.id, 0)
    return detail


@router.post("/sync", response_model=FleetSyncQueued, status_code=status.HTTP_202_ACCEPTED)
async def sync_inventory(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> FleetSyncQueued:
    """Queue the log-source inventory sync the beat runs, for every enabled console (admin:*).

    The task runs in a worker and only reads from QRadar. While any console's pass holds the
    collector lock nothing is queued and ``already_running`` is true. No provider is built in
    the request, so no QRadar call happens here.
    """
    _guard(principal, PERM_ADMIN)
    from app.api.routes import fleet

    return await fleet.fleet_sources_sync(session, settings, principal)


async def _classification(
    session: SessionDep, log_source_id: uuid.UUID
) -> LogSourceClassification | None:
    """The source's classification row, re-read past the identity map.

    The bulk upsert writes the row behind the ORM's back, so a cached instance
    would show the pre-change class.
    """
    return await session.scalar(
        select(LogSourceClassification)
        .where(LogSourceClassification.log_source_id == log_source_id)
        .execution_options(populate_existing=True)
    )


@router.get("/{log_source_id}/classification", response_model=FleetSourceRow)
async def get_classification(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> FleetSourceRow:
    """One source in the fleet list's row shape: class, basis, override and profile states.

    Needs `read:fleet`, like the list. It reuses the list's row loader, so the cost is the
    loader's fixed statement set whatever else exists.
    """
    _guard(principal, PERM_FLEET_READ)
    rows = await load_rows(
        session, [log_source_id], now=datetime.now(UTC),
        bucket_seconds=settings.collection_interval_seconds,
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    return rows[0]


@router.patch("/{log_source_id}/classification", response_model=FleetSourceOut)
async def set_classification_override(
    log_source_id: uuid.UUID,
    payload: ClassificationOverrideIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> FleetSourceOut:
    """Set or clear (null) the operator's class override for one source.

    The source is re-classified in the same request, so the response shows the
    class the platform now uses. Audited as {from, to}. Reviewing is a separate,
    deliberate act: overriding does not mark the source reviewed.
    """
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    ls = await LogSourceRepository(session).get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    before = await _classification(session, log_source_id)
    previous = before.override_class if before else None
    await reclassify_instance(
        session,
        ls.instance_id,
        classified_at=datetime.now(UTC),
        source_ids=[log_source_id],
        override_updates={log_source_id: payload.override_class},
        override_actor=principal.subject,
    )
    after = await _classification(session, log_source_id)
    await record_audit(
        session, action="log_source.classification_override", actor=principal.subject,
        object_type="log_source", object_id=str(log_source_id),
        detail={
            "from": _jsonable(previous),
            "to": _jsonable(payload.override_class),
            "class": _jsonable(after.source_class) if after else None,
        },
    )
    return fleet_source_out(ls, after)


@router.post("/{log_source_id}/review", response_model=FleetSourceOut)
async def mark_reviewed(
    log_source_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> FleetSourceOut:
    """Mark a source reviewed and resolve its new-source alert.

    Idempotent: a source that is already reviewed is returned unchanged, with no
    second audit row and no alert work. Classifying never resolves the alert;
    only this deliberate act does.
    """
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    ls = await LogSourceRepository(session).get(log_source_id)
    if ls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    row = await _classification(session, log_source_id)
    if row is None:
        # Created REVIEWED by the bulk path; nothing is left to acknowledge.
        await reclassify_instance(
            session, ls.instance_id, classified_at=datetime.now(UTC),
            source_ids=[log_source_id],
        )
        row = await _classification(session, log_source_id)
    else:
        await review_classification(session, ls, row, principal.subject)
    return fleet_source_out(ls, row)
