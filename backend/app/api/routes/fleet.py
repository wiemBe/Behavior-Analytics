"""Phase C fleet API: per-class summary and the classified source list."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import Field
from sqlalchemy import select

from app.api.csv_export import (
    _csv_time,
    csv_document,
    csv_enum,
    csv_export_response,
    csv_stamp,
)
from app.api.deps import SessionDep, SettingsDep
from app.api.routes.log_sources import PrincipalDep, _guard, _jsonable
from app.fleet.review import review_classification
from app.fleet.series import build_fleet_eps_series
from app.fleet.service import reclassify_instance
from app.fleet.sources import (
    SourceFilters,
    build_facets,
    count_sources,
    load_rows,
    page_ids,
)
from app.fleet.summary import build_fleet_summary
from app.models.enums import ClassificationReviewStatus, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource
from app.repositories.audit import record_audit
from app.schemas.fleet import (
    ClassificationBasis,
    FleetBulkClassificationIn,
    FleetBulkClassificationOut,
    FleetEpsSeries,
    FleetEpsState,
    FleetFlow,
    FleetProfileKind,
    FleetSeriesGroup,
    FleetSourcePage,
    FleetSourceRow,
    FleetSourceSort,
    FleetSummary,
    FleetWindow,
)
from app.security.rbac import PERM_LOG_SOURCE_WRITE

router = APIRouter(prefix="/fleet", tags=["fleet"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


@router.get("/summary", response_model=FleetSummary)
async def fleet_summary(
    session: SessionDep,
    settings: SettingsDep,
    window: FleetWindow = Query(default="24h"),
) -> FleetSummary:
    """One screen of fleet health by class.

    The window ends at the last closed bucket, so it holds a whole number of
    buckets and the completeness counts are exact. A figure with no complete
    bucket behind it is null.
    """
    bucket_seconds = settings.collection_interval_seconds
    now = _utcnow()
    closed = datetime.fromtimestamp(now.timestamp() // bucket_seconds * bucket_seconds, UTC)
    return await build_fleet_summary(
        session,
        window=window,
        now=closed,
        bucket_seconds=bucket_seconds,
        outage_default=settings.outage_alert_default,
        default_silence_grace_buckets=settings.anomaly_silence_grace_buckets,
    )


@router.get("/eps-series", response_model=FleetEpsSeries)
async def fleet_eps_series(
    session: SessionDep,
    settings: SettingsDep,
    window: FleetWindow = Query(default="24h"),
    group_by: FleetSeriesGroup = Query(default="none"),
) -> FleetEpsSeries:
    """Observed fleet EPS per step, from stored buckets only.

    The window ends at the last closed bucket floored to the step width, so every step
    is a whole step; up to one step minus one bucket of the newest data is left out.
    A step with any in-scope source not measured has null events and EPS; expected EPS
    is a sum of per-source medians, never a band.
    """
    bucket_seconds = settings.collection_interval_seconds
    now = _utcnow()
    closed = datetime.fromtimestamp(now.timestamp() // bucket_seconds * bucket_seconds, UTC)
    return await build_fleet_eps_series(
        session,
        window=window,
        group_by=group_by,
        now=closed,
        bucket_seconds=bucket_seconds,
    )


_Id32 = Annotated[int, Field(ge=0, le=2**31 - 1)]


def source_filters(
    source_class: Annotated[list[SourceClass] | None, Query(alias="class")] = None,
    type_id: Annotated[list[_Id32] | None, Query()] = None,
    group: Annotated[list[_Id32] | None, Query()] = None,
    flow: Annotated[list[FleetFlow] | None, Query()] = None,
    eps: Annotated[list[FleetEpsState] | None, Query()] = None,
    profile: Annotated[list[FleetProfileKind] | None, Query()] = None,
    review_status: ClassificationReviewStatus | None = Query(default=None),
    has_conflict: bool | None = Query(default=None),
    basis: ClassificationBasis | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
) -> SourceFilters:
    """The list's filters, parsed once for the page and the CSV export alike."""
    return SourceFilters(
        q=q,
        classes=list(dict.fromkeys(source_class or [])),
        type_ids=list(dict.fromkeys(type_id or [])),
        group_ids=list(dict.fromkeys(group or [])),
        flows=list(dict.fromkeys(flow or [])),
        eps=list(dict.fromkeys(eps or [])),
        profiles=list(dict.fromkeys(profile or [])),
        review_status=review_status,
        has_conflict=has_conflict,
        basis=basis,
    )


FiltersDep = Annotated[SourceFilters, Depends(source_filters)]
SortOrder = Literal["asc", "desc"]


@router.get("/sources", response_model=FleetSourcePage)
async def fleet_sources(
    session: SessionDep,
    settings: SettingsDep,
    filters: FiltersDep,
    sort: FleetSourceSort = Query(default="name"),
    order: SortOrder = Query(default="asc"),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> FleetSourcePage:
    """The classified source list: filtered, sorted, paged, with facets.

    Statements: one page of ids, one enriched read of that page (plus its group names),
    one exact count and four facet reads, however many sources match.
    """
    bounded = max(1, min(limit, settings.api_max_page_size))
    now = _utcnow()
    width = settings.collection_interval_seconds
    ids = await page_ids(
        session, filters, sort=sort, order=order, limit=bounded, offset=offset,
        now=now, bucket_seconds=width,
    )
    items = await load_rows(session, ids, now=now, bucket_seconds=width)
    total = await count_sources(session, filters, now=now, bucket_seconds=width)
    facets = await build_facets(session, filters, now=now, bucket_seconds=width)
    return FleetSourcePage(
        items=items, total=total, limit=bounded, offset=offset, facets=facets
    )


SOURCE_EXPORT_COLUMNS = (
    "id",
    "qradar_id",
    "name",
    "type_name",
    "identifier",
    "class",
    "basis",
    "override_class",
    "review_status",
    "enabled",
    "flow",
    "last_event_at",
    "silent_for_seconds",
    "current_eps",
    "expected_eps",
    "eps_state",
    "waf_profile",
    "ips_profile",
    "identity_profile",
    "groups",
)


def _profile_cell(state: object) -> str | None:
    if state is None:
        return None
    return f"{state.status}{'' if state.enabled else ' (off)'}"  # type: ignore[attr-defined]


def _export_row(r: FleetSourceRow) -> list[object]:
    return [
        str(r.id), r.qradar_id, r.name, r.type_name, r.identifier,
        csv_enum(r.source_class), r.basis, csv_enum(r.override_class),
        csv_enum(r.review_status), r.enabled, r.flow, _csv_time(r.last_event_at),
        r.silent_for_seconds, r.current_eps, r.expected_eps, r.eps_state,
        _profile_cell(r.waf_profile), _profile_cell(r.ips_profile),
        _profile_cell(r.identity_profile), "; ".join(r.group_names),
    ]


@router.get("/sources.csv")
async def fleet_sources_csv(
    session: SessionDep,
    settings: SettingsDep,
    filters: FiltersDep,
    sort: FleetSourceSort = Query(default="name"),
    order: SortOrder = Query(default="asc"),
) -> Response:
    """The filtered list as a CSV attachment, in the list's order, no paging.

    The file is the first `api_max_export_rows` rows; one more is read so a cut file says
    `X-Export-Truncated: true`. Timestamps are UTC with `Z`. Group names are the bounded
    names of the list row. Reads are not audited, as for the other exports.
    """
    cap = settings.api_max_export_rows
    now = _utcnow()
    width = settings.collection_interval_seconds
    ids = await page_ids(
        session, filters, sort=sort, order=order, limit=cap + 1, offset=None,
        now=now, bucket_seconds=width,
    )
    rows = await load_rows(session, ids[:cap], now=now, bucket_seconds=width)
    return csv_export_response(
        csv_document(SOURCE_EXPORT_COLUMNS, (_export_row(r) for r in rows)),
        filename=f"fleet-sources-{csv_stamp(now)}.csv",
        truncated=len(ids) > cap,
    )


@router.post("/sources/classification", response_model=FleetBulkClassificationOut)
async def bulk_classification(
    payload: FleetBulkClassificationIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> FleetBulkClassificationOut:
    """Set or clear (null) the class override of up to 500 sources in one transaction.

    The same per-source semantics as `PATCH /log-sources/{id}/classification`: the sources
    are re-classified by `reclassify_instance` with the override applied, each override is
    audited as {from, to, class}, and `mark_reviewed` runs the single review path
    (`review_classification`). All sources must exist (404 with only the count of missing
    ones) and belong to one instance (422). One summary audit row carries the counts.
    """
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    ids = list(dict.fromkeys(payload.log_source_ids))
    sources = {
        ls.id: ls for ls in (await session.scalars(select(LogSource).where(LogSource.id.in_(ids))))
    }
    if len(sources) != len(ids):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{len(ids) - len(sources)} log sources not found"
        )
    instances = {ls.instance_id for ls in sources.values()}
    if len(instances) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "the log sources belong to more than one instance",
        )

    async def classifications() -> dict[uuid.UUID, LogSourceClassification]:
        rows = await session.scalars(
            select(LogSourceClassification)
            .where(LogSourceClassification.log_source_id.in_(ids))
            .execution_options(populate_existing=True)
        )
        return {row.log_source_id: row for row in rows}

    before = {k: v.override_class for k, v in (await classifications()).items()}
    await reclassify_instance(
        session,
        next(iter(instances)),
        classified_at=datetime.now(UTC),
        source_ids=ids,
        override_updates=dict.fromkeys(ids, payload.source_class),
        override_actor=principal.subject,
    )
    after = await classifications()
    for sid in ids:
        await record_audit(
            session, action="log_source.classification_override", actor=principal.subject,
            object_type="log_source", object_id=str(sid),
            detail={
                "from": _jsonable(before.get(sid)),
                "to": _jsonable(payload.source_class),
                "class": _jsonable(after[sid].source_class) if sid in after else None,
            },
        )
    reviewed = 0
    if payload.mark_reviewed:
        for sid in ids:
            if sid in after and await review_classification(
                session, sources[sid], after[sid], principal.subject
            ):
                reviewed += 1
    await record_audit(
        session, action="fleet.classification_bulk", actor=principal.subject,
        object_type="fleet", object_id=None,
        detail={
            "requested": len(payload.log_source_ids),
            "updated": len(ids),
            "class": _jsonable(payload.source_class),
            "mark_reviewed": payload.mark_reviewed,
            "reviewed": reviewed,
        },
    )
    now = _utcnow()
    items = await load_rows(
        session, ids, now=now, bucket_seconds=settings.collection_interval_seconds
    )
    return FleetBulkClassificationOut(updated=len(ids), items=items)
