"""IPS profile administration, guarded as a whole by ``admin:ips``."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep
from app.ips.profile import (
    ACTION_OUTCOMES,
    IPS_MAPPING_FIELDS,
    IPS_PRESETS,
    NORMALIZED_FIELD_ALLOWLIST,
    IpsFieldMapping,
    IpsFieldReference,
    IpsProfileError,
    profile_ineligibility,
)
from app.models.enums import IpsLogicalField, IpsPreset, IpsReferenceKind, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.providers.base import PROPERTY_NAME_PATTERN
from app.providers.factory import build_provider_for_instance
from app.schemas.ips import (
    IpsActionValueMapIn,
    IpsCandidateOut,
    IpsFieldReferenceIn,
    IpsFieldStatusOut,
    IpsMappingFieldOut,
    IpsMappingIn,
    IpsMappingSchemaOut,
    IpsPresetOut,
    IpsProfileHistoryEntryOut,
    IpsProfileIn,
    IpsProfileOut,
    IpsSourceStatusOut,
    IpsValidationReportOut,
    IpsValueMapSchemaOut,
)
from app.schemas.property_catalog import PropertyCatalogOut
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.ips_profiles import AUDIT_OBJECT, IpsProfileService
from app.services.property_catalog import PropertyCatalogService, get_property_catalog_service

router = APIRouter(prefix="/ips", tags=["ips"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _reference(value: IpsFieldReferenceIn | None) -> IpsFieldReference | None:
    if value is None:
        return None
    return IpsFieldReference(kind=IpsReferenceKind(value.kind), name=value.name)


def _field_mapping(payload: IpsProfileIn) -> IpsFieldMapping:
    mapping = payload.mapping
    values = payload.action_value_map
    return IpsFieldMapping(
        source_ip=_reference(mapping.source_ip),  # type: ignore[arg-type]
        destination_ip=_reference(mapping.destination_ip),  # type: ignore[arg-type]
        destination_port=_reference(mapping.destination_port),  # type: ignore[arg-type]
        protocol=_reference(mapping.protocol),
        signature=_reference(mapping.signature),  # type: ignore[arg-type]
        action=_reference(mapping.action),
        severity=_reference(mapping.severity),
        blocked_actions=tuple(values.blocked),
        allowed_actions=tuple(values.allowed),
    )


def _mapping_input(profile: IpsSourceProfile) -> IpsMappingIn:
    mapping = IpsFieldMapping.from_profile(profile)
    return IpsMappingIn.model_validate(mapping.mapping_dict())


def _action_map_input(profile: IpsSourceProfile) -> IpsActionValueMapIn:
    mapping = IpsFieldMapping.from_profile(profile)
    return IpsActionValueMapIn(
        blocked=list(mapping.blocked_actions), allowed=list(mapping.allowed_actions)
    )


def _validation_out(profile: IpsSourceProfile) -> IpsValidationReportOut | None:
    report = profile.validation_report or {}
    return IpsValidationReportOut.model_validate(report) if report else None


def _profile_out(
    profile: IpsSourceProfile, source: LogSource, settings: SettingsDep
) -> IpsProfileOut:
    del settings  # reserved for T-050's global IPS collection gate
    reason = profile_ineligibility(profile)
    return IpsProfileOut(
        profile_id=profile.id,
        instance_id=profile.instance_id,
        log_source_id=source.id,
        qradar_log_source_id=source.qradar_id,
        log_source_name=source.name,
        log_source_type_name=source.type_name,
        product_label=profile.product_label,
        preset=profile.preset,
        profile_version=profile.profile_version,
        enabled=profile.enabled,
        mapping=_mapping_input(profile),
        action_value_map=_action_map_input(profile),
        validation_status=str(profile.validation_status),
        validated_profile_version=profile.validated_profile_version,
        validated_at=profile.validated_at,
        validation=_validation_out(profile),
        validation_pending=profile.validation_requested_at is not None,
        validation_requested_at=profile.validation_requested_at,
        validation_requested_by=profile.validation_requested_by,
        collection_eligible=reason is None,
        ineligible_reason=reason,
        updated_by=profile.updated_by,
        updated_at=profile.updated_at,
    )


def _source_status(profile: IpsSourceProfile, source: LogSource) -> IpsSourceStatusOut:
    reason = profile_ineligibility(profile)
    report = profile.validation_report or {}
    raw_fields = report.get("fields")
    raw_reasons = report.get("reasons")
    report_fields = raw_fields if isinstance(raw_fields, list) else []
    report_reasons = raw_reasons if isinstance(raw_reasons, list) else []
    return IpsSourceStatusOut(
        profile_id=profile.id,
        instance_id=profile.instance_id,
        log_source_id=source.id,
        qradar_log_source_id=source.qradar_id,
        log_source_name=source.name,
        log_source_type_name=source.type_name,
        product_label=profile.product_label,
        preset=profile.preset,
        enabled=profile.enabled,
        profile_version=profile.profile_version,
        validation_status=str(profile.validation_status),
        validated_profile_version=profile.validated_profile_version,
        validated_at=profile.validated_at,
        validation_pending=profile.validation_requested_at is not None,
        validation_requested_at=profile.validation_requested_at,
        validation_requested_by=profile.validation_requested_by,
        # Only these leaf values leave the report: discovered raw values, counts
        # and references stay behind the per-profile editor view (AGENTS.md rule 3).
        validation_fields=[
            IpsFieldStatusOut(
                field=str(entry.get("field")),
                status=str(entry.get("status")),
                detail=entry.get("detail"),
            )
            for entry in report_fields
            if isinstance(entry, dict)
        ],
        validation_reasons=[item for item in report_reasons if isinstance(item, str)],
        collection_eligible=reason is None,
        ineligible_reason=reason,
        updated_by=profile.updated_by,
        updated_at=profile.updated_at,
    )


async def _log_source(
    session: SessionDep, log_source_id: uuid.UUID, *, for_update: bool = False
) -> LogSource:
    source = await session.get(LogSource, log_source_id, with_for_update=for_update)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    return source


async def _profile(
    session: SessionDep, source: LogSource, *, for_update: bool = False
) -> IpsSourceProfile:
    query = select(IpsSourceProfile).where(IpsSourceProfile.log_source_id == source.id)
    if for_update:
        query = query.with_for_update()
    profile = await session.scalar(query)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS source profile not found")
    return profile


def _conflict_detail(
    message: str, profile: IpsSourceProfile | None = None
) -> dict[str, str | int | None]:
    return {
        "message": message,
        "current_version": profile.profile_version if profile else None,
        "updated_by": profile.updated_by if profile else None,
        "pending_since": (
            profile.validation_requested_at.isoformat()
            if profile is not None and profile.validation_requested_at is not None
            else None
        ),
    }


@router.get("/mapping-schema", response_model=IpsMappingSchemaOut)
async def get_mapping_schema(settings: SettingsDep) -> IpsMappingSchemaOut:
    defaults = IPS_PRESETS[IpsPreset.CISCO_FTD]
    labels = {
        IpsPreset.CISCO_FTD: "Cisco Firepower Threat Defense",
        IpsPreset.PALO_ALTO_THREAT: "Palo Alto THREAT",
    }
    fields: list[IpsMappingFieldOut] = []
    for field in IPS_MAPPING_FIELDS:
        reference = defaults.reference_for(field.key)
        fields.append(
            IpsMappingFieldOut(
                key=field.key.value,
                label=field.label,
                description=field.description,
                required=field.required,
                used_by_collection=field.used_by_collection,
                kinds=["NORMALIZED", "CUSTOM_PROPERTY"],
                normalized_options=list(NORMALIZED_FIELD_ALLOWLIST),
                default=reference.as_dict() if reference is not None else None,
            )
        )
    return IpsMappingSchemaOut(
        property_name_pattern=PROPERTY_NAME_PATTERN.pattern,
        min_coverage_ratio=settings.ips_validation_min_ratio,
        fields=fields,
        value_maps=[
            IpsValueMapSchemaOut(
                field=IpsLogicalField.ACTION.value, outcomes=list(ACTION_OUTCOMES)
            )
        ],
        presets=[
            IpsPresetOut(
                key=preset,
                label=labels[preset],
                mapping=mapping.mapping_dict(),
                action_value_map=mapping.action_value_map(),
            )
            for preset, mapping in IPS_PRESETS.items()
        ],
    )


async def _resolve_instance(
    session: AsyncSession, instance_id: uuid.UUID | None
) -> uuid.UUID | None:
    if instance_id is not None:
        return instance_id
    return await session.scalar(select(QRadarInstance.id).limit(1))


@router.get("/candidates", response_model=list[IpsCandidateOut])
async def list_ips_candidates(
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[IpsCandidateOut]:
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no instance registered")
    query = (
        select(LogSource, LogSourceClassification.source_class)
        .outerjoin(IpsSourceProfile, IpsSourceProfile.log_source_id == LogSource.id)
        .outerjoin(
            LogSourceClassification,
            LogSourceClassification.log_source_id == LogSource.id,
        )
        .where(LogSource.instance_id == resolved, IpsSourceProfile.id.is_(None))
        .order_by(
            case((LogSourceClassification.source_class == SourceClass.IPS, 0), else_=1),
            func.lower(LogSource.name),
            LogSource.qradar_id,
        )
        .limit(limit)
    )
    if q:
        query = query.where(LogSource.name.icontains(q, autoescape=True))
    rows = (await session.execute(query)).all()
    return [
        IpsCandidateOut(
            instance_id=source.instance_id,
            log_source_id=source.id,
            qradar_id=source.qradar_id,
            name=source.name,
            type_name=source.type_name,
            source_class=str(source_class) if source_class is not None else None,
        )
        for source, source_class in rows
    ]


@router.get("/log-sources/{log_source_id}/profile", response_model=IpsProfileOut)
async def get_ips_profile(
    log_source_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> IpsProfileOut:
    source = await _log_source(session, log_source_id)
    return _profile_out(await _profile(session, source), source, settings)


@router.put(
    "/log-sources/{log_source_id}/profile",
    response_model=IpsProfileOut,
    responses={status.HTTP_201_CREATED: {"model": IpsProfileOut}},
)
async def put_ips_profile(
    log_source_id: uuid.UUID,
    payload: IpsProfileIn,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> IpsProfileOut:
    source = await _log_source(session, log_source_id, for_update=True)
    service = IpsProfileService(session, settings=settings)
    existing = await service.for_log_source(source)
    if existing is None:
        if payload.expected_profile_version is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                _conflict_detail(
                    "the profile does not exist; create it with expected_profile_version null"
                ),
            )
        created = True
        mapping_changed = True
    else:
        if payload.expected_profile_version != existing.profile_version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                _conflict_detail(
                    f"profile version conflict; current version is {existing.profile_version}",
                    existing,
                ),
            )
        created = False
        try:
            mapping_changed = IpsFieldMapping.from_profile(existing) != _field_mapping(payload)
        except IpsProfileError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc)}
            ) from exc
    try:
        profile, _ = await service.upsert(
            log_source=source,
            mapping=_field_mapping(payload),
            actor=principal.subject,
            product_label=payload.product_label,
            preset=payload.preset,
            replace_metadata=True,
        )
        if mapping_changed:
            await service.request_validation(profile, actor=principal.subject)
    except IpsProfileError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc)}
        ) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _profile_out(profile, source, settings)


@router.post(
    "/log-sources/{log_source_id}/profile/validate",
    response_model=IpsProfileOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_ips_validation(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> IpsProfileOut:
    source = await _log_source(session, log_source_id)
    profile = await _profile(session, source, for_update=True)
    try:
        await IpsProfileService(session, settings=settings).request_validation(
            profile, actor=principal.subject
        )
    except IpsProfileError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, _conflict_detail(str(exc), profile)
        ) from exc
    return _profile_out(profile, source, settings)


async def _set_enabled(
    log_source_id: uuid.UUID,
    enabled: bool,
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal,
) -> IpsProfileOut:
    source = await _log_source(session, log_source_id)
    profile = await _profile(session, source, for_update=True)
    try:
        await IpsProfileService(session, settings=settings).set_enabled(
            profile, enabled, actor=principal.subject
        )
    except IpsProfileError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, _conflict_detail(str(exc), profile)
        ) from exc
    return _profile_out(profile, source, settings)


@router.post("/log-sources/{log_source_id}/profile/enable", response_model=IpsProfileOut)
async def enable_ips_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> IpsProfileOut:
    return await _set_enabled(log_source_id, True, session, settings, principal)


@router.post("/log-sources/{log_source_id}/profile/disable", response_model=IpsProfileOut)
async def disable_ips_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> IpsProfileOut:
    return await _set_enabled(log_source_id, False, session, settings, principal)


@router.get(
    "/log-sources/{log_source_id}/profile/history",
    response_model=list[IpsProfileHistoryEntryOut],
)
async def get_ips_profile_history(
    log_source_id: uuid.UUID, session: SessionDep
) -> list[IpsProfileHistoryEntryOut]:
    source = await _log_source(session, log_source_id)
    profile = await _profile(session, source)
    rows = list(
        (
            await session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.object_type == AUDIT_OBJECT,
                    AuditLog.object_id == str(profile.id),
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(50)
            )
        ).all()
    )
    return [
        IpsProfileHistoryEntryOut(
            at=row.created_at,
            actor=row.actor,
            action=row.action,
            profile_version=(
                row.detail.get("profile_version")
                if isinstance(row.detail.get("profile_version"), int)
                else None
            ),
            mapping_before=(
                row.detail.get("mapping_before")
                if isinstance(row.detail.get("mapping_before"), dict)
                else None
            ),
            mapping_after=(
                row.detail.get("mapping_after", row.detail.get("mapping"))
                if isinstance(
                    row.detail.get("mapping_after", row.detail.get("mapping")), dict
                )
                else None
            ),
            outcome=row.outcome,
            reasons=(
                row.detail.get("reasons")
                if isinstance(row.detail.get("reasons"), list)
                else None
            ),
        )
        for row in rows
    ]


@router.get(
    "/log-sources/{log_source_id}/property-catalog", response_model=PropertyCatalogOut
)
async def get_ips_property_catalog(
    log_source_id: uuid.UUID,
    session: SessionDep,
    service: Annotated[PropertyCatalogService, Depends(get_property_catalog_service)],
    refresh: bool = Query(default=False),
) -> PropertyCatalogOut:
    source = await session.get(LogSource, log_source_id)
    instance = await session.get(QRadarInstance, source.instance_id) if source else None
    if source is None or instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Log source not found")
    return await service.catalog(
        instance_id=instance.id,
        provider_factory=lambda: build_provider_for_instance(instance),
        source_type_id=source.type_id,
        refresh=refresh,
    )


@router.get("/sources", response_model=list[IpsSourceStatusOut])
async def list_ips_sources(session: SessionDep) -> list[IpsSourceStatusOut]:
    rows = (
        await session.execute(
            select(IpsSourceProfile, LogSource)
            .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
            .order_by(func.lower(LogSource.name), LogSource.qradar_id)
        )
    ).all()
    return [_source_status(profile, source) for profile, source in rows]
