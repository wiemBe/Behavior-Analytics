"""Phase C fleet API: per-class summary and the classified source list."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, literal, or_, select

from app.api.deps import SessionDep, SettingsDep
from app.fleet.summary import build_fleet_summary
from app.models.enums import ClassificationReviewStatus, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource
from app.schemas.fleet import (
    ClassificationBasis,
    FleetSourcePage,
    FleetSummary,
    FleetWindow,
    fleet_source_out,
)

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


@router.get("/sources", response_model=FleetSourcePage)
async def fleet_sources(
    session: SessionDep,
    settings: SettingsDep,
    source_class: Annotated[SourceClass | None, Query(alias="class")] = None,
    review_status: ClassificationReviewStatus | None = Query(default=None),
    has_conflict: bool | None = Query(default=None),
    basis: ClassificationBasis | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> FleetSourcePage:
    bounded = max(1, min(limit, settings.api_max_page_size))
    klass = func.coalesce(
        LogSourceClassification.source_class, literal(SourceClass.UNCLASSIFIED.value)
    )
    basis_col = func.coalesce(LogSourceClassification.basis, literal("UNCLASSIFIED"))
    conflicted = func.coalesce(
        func.jsonb_array_length(LogSourceClassification.conflicts) > 0, False
    )

    conditions = []
    if source_class is not None:
        conditions.append(klass == source_class.value)
    if review_status is not None:
        conditions.append(LogSourceClassification.review_status == review_status.value)
    if has_conflict is not None:
        conditions.append(conflicted.is_(has_conflict))
    if basis is not None:
        conditions.append(basis_col == basis)
    needle = (q or "").strip()
    if needle:
        # A case-insensitive substring of the name or the QRadar type name. The
        # LIKE wildcards in the input are literal characters, not patterns.
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append(
            or_(
                LogSource.name.ilike(pattern, escape="\\"),
                LogSource.type_name.ilike(pattern, escape="\\"),
            )
        )

    base = select(LogSource, LogSourceClassification).outerjoin(
        LogSourceClassification, LogSourceClassification.log_source_id == LogSource.id
    )
    total = await session.scalar(
        select(func.count())
        .select_from(LogSource)
        .outerjoin(
            LogSourceClassification, LogSourceClassification.log_source_id == LogSource.id
        )
        .where(*conditions)
    )
    rows = (
        await session.execute(
            base.where(*conditions)
            .order_by(LogSource.name, LogSource.id)
            .limit(bounded)
            .offset(offset)
        )
    ).all()
    items = [fleet_source_out(ls, cl) for ls, cl in rows]
    return FleetSourcePage(items=items, total=total or 0, limit=bounded, offset=offset)
