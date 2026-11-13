"""Identity sources: stored profiles, collection state, and the top-N the fold kept.

Reads need ``read:identity`` (the router). Writes need ``admin:identity`` in the
handler, so a reader is refused with 403 before any lookup. A missing count is
null. Audit rows store versions and ids, never a username.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, requires
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.identity.collector import COLLECTOR_NAME
from app.identity.profiles import IdentityProfileError, IdentityProfileService
from app.identity.reference import IdentityReferenceError
from app.models.identity_detection import (
    IdentityBucket,
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderCapability, ProviderError
from app.providers.factory import build_provider_for_instance
from app.schemas.identity import (
    IdentityBucketSummaryOut,
    IdentityCollectionOut,
    IdentityProfileIn,
    IdentityProfileOut,
    IdentitySeriesOut,
    IdentitySourceDetailOut,
    IdentitySourceOut,
    IdentityStoredNameOut,
)
from app.security.rbac import PERM_IDENTITY_ADMIN, Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/identity", tags=["identity"])
AdminDep = Annotated[Principal, Depends(requires(PERM_IDENTITY_ADMIN))]

_MAX_WINDOW = timedelta(days=7)
_DEFAULT_WINDOW = timedelta(hours=24)
_SOURCE_NOT_FOUND = MessageRef("identity.source.not_found")
_PROFILE_NOT_FOUND = MessageRef("identity.profile.not_found")
_PROFILE_INVALID = MessageRef("identity.profile.invalid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """Last 24 hours when the caller omits the range. Wider than 7 days is refused."""
    end_at = _utc(end) if end is not None else datetime.now(UTC)
    start_at = _utc(start) if start is not None else end_at - _DEFAULT_WINDOW
    if start_at >= end_at:
        raise CodedHTTPException(422, MessageRef("identity.window.order"))
    if end_at - start_at > _MAX_WINDOW:
        raise CodedHTTPException(422, MessageRef("identity.window.max", {"days": 7}))
    return start_at, end_at


def _profile_out(profile: IdentitySourceProfile, source: LogSource) -> IdentityProfileOut:
    report = profile.validation_report if isinstance(profile.validation_report, dict) else {}
    return IdentityProfileOut(
        id=profile.id,
        log_source_id=source.id,
        instance_id=profile.instance_id,
        qradar_log_source_id=source.qradar_id,
        log_source_name=source.name,
        enabled=profile.enabled,
        profile_version=profile.profile_version,
        product_label=profile.product_label,
        event_id_property=profile.event_id_property,
        username_property=profile.username_property,
        logon_type_property=profile.logon_type_property,
        enabled_event_ids=[int(event_id) for event_id in profile.enabled_event_ids],
        validation_status=str(profile.validation_status),
        validated_profile_version=profile.validated_profile_version,
        validated_at=profile.validated_at,
        validation_report=report,
    )


def _collection_out(row: CollectionWatermark | None) -> IdentityCollectionOut:
    if row is None:
        return IdentityCollectionOut()
    return IdentityCollectionOut(
        watermark_at=row.watermark_at,
        last_run_at=row.last_run_at,
        lag_seconds=row.lag_seconds,
        consecutive_failures=row.consecutive_failures,
        last_error=row.last_error,
    )


def _bucket_out(row: IdentityBucket) -> IdentityBucketSummaryOut:
    return IdentityBucketSummaryOut(
        bucket_start=row.bucket_start,
        bucket_seconds=row.bucket_seconds,
        measurement=row.measurement,
        population=row.population,
        admission_truncated=row.admission_truncated,
    )


async def _source(session: AsyncSession, log_source_id: uuid.UUID) -> LogSource:
    source = await session.get(LogSource, log_source_id)
    if source is None:
        raise CodedHTTPException(404, _SOURCE_NOT_FOUND)
    return source


async def _profile(
    session: AsyncSession, source: LogSource
) -> IdentitySourceProfile:
    profile = await IdentityProfileService(session).for_log_source(source)
    if profile is None:
        raise CodedHTTPException(404, _PROFILE_NOT_FOUND)
    return profile


def _invalid(_exc: IdentityReferenceError | IdentityProfileError) -> CodedHTTPException:
    """The refusal sentence is fixed. The operator's text is not in it."""
    return CodedHTTPException(422, _PROFILE_INVALID)


@router.get("/sources", response_model=list[IdentitySourceOut])
async def list_identity_sources(session: SessionDep) -> list[IdentitySourceOut]:
    """Profiles, one watermark read, and each source's newest bucket.

    Three statements however many sources are stored. A source with no watermark
    reports null failures, and a bucket that was not collected reports a null
    population.
    """
    rows = (
        await session.execute(
            select(IdentitySourceProfile, LogSource)
            .join(LogSource, LogSource.id == IdentitySourceProfile.log_source_id)
            .order_by(LogSource.name, LogSource.id)
        )
    ).all()
    if not rows:
        return []
    source_ids = [source.id for _profile_row, source in rows]
    instance_ids = list({profile.instance_id for profile, _source in rows})
    buckets = (
        await session.scalars(
            select(IdentityBucket)
            .ext(distinct_on(IdentityBucket.log_source_id))
            .where(IdentityBucket.log_source_id.in_(source_ids))
            .order_by(IdentityBucket.log_source_id, IdentityBucket.bucket_start.desc())
        )
    ).all()
    watermarks = (
        await session.scalars(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == COLLECTOR_NAME,
                CollectionWatermark.instance_id.in_(instance_ids),
            )
        )
    ).all()
    by_source = {row.log_source_id: row for row in buckets}
    by_instance = {row.instance_id: row for row in watermarks}
    return [
        IdentitySourceOut(
            profile=_profile_out(profile, source),
            collection=_collection_out(by_instance.get(profile.instance_id)),
            last_bucket=_bucket_out(by_source[source.id]) if source.id in by_source else None,
        )
        for profile, source in rows
    ]


@router.get("/sources/{log_source_id}", response_model=IdentitySourceDetailOut)
async def get_identity_source(
    log_source_id: uuid.UUID,
    session: SessionDep,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> IdentitySourceDetailOut:
    """Event-ID counts and the stored top-N over one bounded window.

    The window defaults to the last 24 hours and cannot exceed 7 days. Names
    come only from ``identity_top_username``. A count that was not measured is
    null. The read is the profile, the counts, and the names: three statements
    however many event IDs or names the window holds.
    """
    start_at, end_at = _window(start, end)
    found = (
        await session.execute(
            select(LogSource, IdentitySourceProfile)
            .outerjoin(
                IdentitySourceProfile,
                IdentitySourceProfile.log_source_id == LogSource.id,
            )
            .where(LogSource.id == log_source_id)
        )
    ).first()
    if found is None:
        raise CodedHTTPException(404, _SOURCE_NOT_FOUND)
    source, profile = found
    if profile is None:
        raise CodedHTTPException(404, _PROFILE_NOT_FOUND)
    counts = (
        await session.scalars(
            select(IdentityEventCount)
            .where(
                IdentityEventCount.log_source_id == source.id,
                IdentityEventCount.bucket_start >= start_at,
                IdentityEventCount.bucket_start < end_at,
            )
            .order_by(IdentityEventCount.event_id, IdentityEventCount.bucket_start)
        )
    ).all()
    names = (
        await session.scalars(
            select(IdentityTopUsername)
            .where(
                IdentityTopUsername.log_source_id == source.id,
                IdentityTopUsername.bucket_start >= start_at,
                IdentityTopUsername.bucket_start < end_at,
            )
            .order_by(
                IdentityTopUsername.event_id,
                IdentityTopUsername.bucket_start,
                IdentityTopUsername.rank,
            )
        )
    ).all()
    series: dict[tuple[int, datetime], IdentitySeriesOut] = {
        (row.event_id, row.bucket_start): IdentitySeriesOut(
            event_id=row.event_id,
            bucket_start=row.bucket_start,
            event_count=row.event_count,
            measurement=row.measurement,
            names_measurement=row.names_measurement,
            admission_truncated=row.admission_truncated,
        )
        for row in counts
    }
    for row in names:
        key = (row.event_id, row.bucket_start)
        item = series.get(key)
        if item is None:
            item = IdentitySeriesOut(event_id=row.event_id, bucket_start=row.bucket_start)
            series[key] = item
        item.usernames.append(
            IdentityStoredNameOut(
                rank=row.rank,
                username=row.username,
                count=row.event_count,
                truncated=row.truncated,
            )
        )
    ordered = [series[key] for key in sorted(series)]
    return IdentitySourceDetailOut(profile=_profile_out(profile, source), series=ordered)


@router.put("/sources/{log_source_id}/profile", response_model=IdentityProfileOut)
async def put_identity_profile(
    log_source_id: uuid.UUID,
    payload: IdentityProfileIn,
    session: SessionDep,
    principal: AdminDep,
) -> IdentityProfileOut:
    source = await _source(session, log_source_id)
    try:
        profile, _changed = await IdentityProfileService(session).set_mapping(
            log_source=source,
            event_id_property=payload.event_id_property,
            username_property=payload.username_property,
            logon_type_property=payload.logon_type_property,
            enabled_event_ids=payload.enabled_event_ids,
            actor=principal.subject,
            product_label=payload.product_label,
        )
    except (IdentityReferenceError, IdentityProfileError) as exc:
        raise _invalid(exc) from exc
    return _profile_out(profile, source)


@router.post("/sources/{log_source_id}/validate", response_model=IdentityProfileOut)
async def validate_identity_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    principal: AdminDep,
) -> IdentityProfileOut:
    source = await _source(session, log_source_id)
    profile = await _profile(session, source)
    instance = await session.get(QRadarInstance, source.instance_id)
    if instance is None:
        raise CodedHTTPException(404, _SOURCE_NOT_FOUND)
    try:
        provider = build_provider_for_instance(instance)
    except ValueError as exc:
        logger.warning(
            "identity validation provider was not built",
            extra={"error_class": type(exc).__name__},
        )
        raise CodedHTTPException(422, _PROFILE_INVALID) from exc
    try:
        if not provider.supports(ProviderCapability.AQL_EXECUTION):
            raise CodedHTTPException(422, MessageRef("identity.provider.unsupported"))
        try:
            await IdentityProfileService(session).validate(
                profile, provider, actor=principal.subject
            )
        except IdentityProfileError as exc:
            raise _invalid(exc) from exc
        except ProviderError as exc:
            logger.warning(
                "identity validation probe failed",
                extra={"error_class": type(exc).__name__},
            )
            raise CodedHTTPException(422, _PROFILE_INVALID) from exc
    finally:
        await provider.aclose()
    return _profile_out(profile, source)


@router.post("/sources/{log_source_id}/enable", response_model=IdentityProfileOut)
async def enable_identity_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    principal: AdminDep,
) -> IdentityProfileOut:
    source = await _source(session, log_source_id)
    profile = await _profile(session, source)
    try:
        await IdentityProfileService(session).enable(profile, actor=principal.subject)
    except IdentityProfileError as exc:
        raise CodedHTTPException(
            409,
            MessageRef(
                "identity.profile.enable_refused",
                {"version": str(profile.profile_version)},
            ),
        ) from exc
    return _profile_out(profile, source)


@router.post("/sources/{log_source_id}/disable", response_model=IdentityProfileOut)
async def disable_identity_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    principal: AdminDep,
) -> IdentityProfileOut:
    source = await _source(session, log_source_id)
    profile = await _profile(session, source)
    await IdentityProfileService(session).disable(profile, actor=principal.subject)
    return _profile_out(profile, source)
