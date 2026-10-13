"""WAF collection status and source-profile administration.

The router is guarded by ``admin:waf``. Profile writes change only application
configuration; validation is bounded, read-only QRadar work dispatched to a
Celery worker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select

from app.api.deps import SessionDep, SettingsDep
from app.models.enums import SourceClass, WafLogicalField
from app.models.fleet import LogSourceClassification
from app.models.identity import AuditLog
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.providers.base import PROPERTY_NAME_PATTERN
from app.schemas.waf import (
    MappingFieldOut,
    MappingSchemaOut,
    ValueMapSchemaOut,
    WafActionValueMapIn,
    WafArielBudgetOut,
    WafCandidateOut,
    WafMappingIn,
    WafProfileHistoryEntryOut,
    WafProfileIn,
    WafProfileOut,
    WafSourceStatusOut,
    WafValidationReportOut,
)
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.waf_profiles import AUDIT_OBJECT, WafProfileService
from app.services.waf_status import (
    collection_ineligibility,
    waf_ariel_budget_status,
    waf_source_statuses,
)
from app.waf.profile import (
    ACTION_OUTCOMES,
    WAF_MAPPING_FIELDS,
    WafFieldMapping,
    WafProfileError,
)

router = APIRouter(prefix="/waf", tags=["waf"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _mapping_input(profile: WafSourceProfile) -> WafMappingIn:
    mapping = WafFieldMapping.from_profile(profile)
    return WafMappingIn(
        url=mapping.url,
        method=mapping.method,
        virtual_host=mapping.virtual_host,
        action=mapping.action,
        response_code=mapping.response_code,
        rule_id=mapping.rule_id,
        user_agent=mapping.user_agent,
    )


def _action_map_input(profile: WafSourceProfile) -> WafActionValueMapIn:
    mapping = WafFieldMapping.from_profile(profile)
    return WafActionValueMapIn(
        blocked=list(mapping.blocked_actions),
        allowed=list(mapping.allowed_actions),
    )


def _validation_out(profile: WafSourceProfile) -> WafValidationReportOut | None:
    report = profile.validation_report or {}
    if not report:
        return None
    # Typed projection deliberately drops total_query and every field's queries.
    return WafValidationReportOut.model_validate(report)


def _profile_out(
    profile: WafSourceProfile,
    log_source: LogSource,
    settings: SettingsDep,
) -> WafProfileOut:
    reason = collection_ineligibility(profile, log_source, settings)
    return WafProfileOut(
        profile_id=profile.id,
        instance_id=profile.instance_id,
        log_source_id=log_source.id,
        qradar_log_source_id=log_source.qradar_id,
        log_source_name=log_source.name,
        log_source_type_name=log_source.type_name,
        product_label=profile.product_label,
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


async def _log_source(
    session: SessionDep, log_source_id: uuid.UUID, *, for_update: bool = False
) -> LogSource:
    source = await session.get(LogSource, log_source_id, with_for_update=for_update)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    return source


async def _profile(
    session: SessionDep, source: LogSource, *, for_update: bool = False
) -> WafSourceProfile:
    query = select(WafSourceProfile).where(WafSourceProfile.log_source_id == source.id)
    if for_update:
        query = query.with_for_update()
    profile = await session.scalar(query)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source profile not found")
    return profile


def _field_mapping(payload: WafProfileIn) -> WafFieldMapping:
    mapping = payload.mapping
    values = payload.action_value_map
    return WafFieldMapping(
        url=mapping.url,
        method=mapping.method,
        virtual_host=mapping.virtual_host,
        action=mapping.action,
        response_code=mapping.response_code,
        rule_id=mapping.rule_id,
        user_agent=mapping.user_agent,
        blocked_actions=tuple(values.blocked),
        allowed_actions=tuple(values.allowed),
    )


def _conflict_detail(
    message: str, profile: WafSourceProfile | None = None
) -> dict[str, str | int | None]:
    return {
        "message": message,
        "current_version": profile.profile_version if profile is not None else None,
        "updated_by": profile.updated_by if profile is not None else None,
        "pending_since": (
            profile.validation_requested_at.isoformat()
            if profile is not None and profile.validation_requested_at is not None
            else None
        ),
    }


@router.get("/mapping-schema", response_model=MappingSchemaOut)
async def get_mapping_schema(settings: SettingsDep) -> MappingSchemaOut:
    defaults = {
        WafLogicalField.URL: settings.waf_url_property or None,
        WafLogicalField.METHOD: settings.waf_method_property or None,
        WafLogicalField.VIRTUAL_HOST: settings.waf_virtual_host_property or None,
    }
    return MappingSchemaOut(
        property_name_pattern=PROPERTY_NAME_PATTERN.pattern,
        min_coverage_ratio=settings.waf_validation_min_ratio,
        fields=[
            MappingFieldOut(
                key=field.key.value,
                label=field.label,
                description=field.description,
                required=field.required,
                used_by_collection=field.used_by_collection,
                kinds=["CUSTOM_PROPERTY"],
                normalized_options=[],
                default=defaults.get(field.key),
            )
            for field in WAF_MAPPING_FIELDS
        ],
        value_maps=[
            ValueMapSchemaOut(field=WafLogicalField.ACTION.value, outcomes=list(ACTION_OUTCOMES))
        ],
    )


@router.get("/candidates", response_model=list[WafCandidateOut])
async def list_waf_candidates(
    session: SessionDep,
    instance_id: uuid.UUID,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[WafCandidateOut]:
    query = (
        select(LogSource, LogSourceClassification.source_class)
        .outerjoin(WafSourceProfile, WafSourceProfile.log_source_id == LogSource.id)
        .outerjoin(
            LogSourceClassification,
            LogSourceClassification.log_source_id == LogSource.id,
        )
        .where(
            LogSource.instance_id == instance_id,
            WafSourceProfile.id.is_(None),
        )
        .order_by(
            case((LogSourceClassification.source_class == SourceClass.WAF, 0), else_=1),
            func.lower(LogSource.name),
            LogSource.qradar_id,
        )
        .limit(limit)
    )
    if q:
        query = query.where(LogSource.name.icontains(q, autoescape=True))
    rows = (await session.execute(query)).all()
    return [
        WafCandidateOut(
            instance_id=source.instance_id,
            log_source_id=source.id,
            qradar_id=source.qradar_id,
            name=source.name,
            type_name=source.type_name,
            source_class=str(source_class) if source_class is not None else None,
        )
        for source, source_class in rows
    ]


@router.get("/log-sources/{log_source_id}/profile", response_model=WafProfileOut)
async def get_waf_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> WafProfileOut:
    source = await _log_source(session, log_source_id)
    profile = await _profile(session, source)
    return _profile_out(profile, source, settings)


@router.put(
    "/log-sources/{log_source_id}/profile",
    response_model=WafProfileOut,
    responses={status.HTTP_201_CREATED: {"model": WafProfileOut}},
)
async def put_waf_profile(
    log_source_id: uuid.UUID,
    payload: WafProfileIn,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> WafProfileOut:
    source = await _log_source(session, log_source_id, for_update=True)
    service = WafProfileService(session, settings=settings)
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
        current = existing.profile_version
        if payload.expected_profile_version != current:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                _conflict_detail(
                    f"profile version conflict; current version is {current}", existing
                ),
            )
        created = False
        try:
            mapping_changed = WafFieldMapping.from_profile(existing) != _field_mapping(payload)
        except WafProfileError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc)}
            ) from exc

    try:
        mapping = _field_mapping(payload)
        profile, _ = await service.upsert(
            log_source=source,
            mapping=mapping,
            actor=principal.subject,
            product_label=payload.product_label,
            replace_product_label=True,
        )
        if mapping_changed:
            await service.request_validation(profile, actor=principal.subject)
    except WafProfileError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc)}
        ) from exc

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _profile_out(profile, source, settings)


@router.post(
    "/log-sources/{log_source_id}/profile/validate",
    response_model=WafProfileOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_waf_validation(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> WafProfileOut:
    source = await _log_source(session, log_source_id)
    profile = await _profile(session, source, for_update=True)
    service = WafProfileService(session, settings=settings)
    try:
        await service.request_validation(profile, actor=principal.subject)
    except WafProfileError as exc:
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
) -> WafProfileOut:
    source = await _log_source(session, log_source_id)
    profile = await _profile(session, source, for_update=True)
    try:
        await WafProfileService(session, settings=settings).set_enabled(
            profile, enabled, actor=principal.subject
        )
    except WafProfileError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, _conflict_detail(str(exc), profile)
        ) from exc
    return _profile_out(profile, source, settings)


@router.post("/log-sources/{log_source_id}/profile/enable", response_model=WafProfileOut)
async def enable_waf_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> WafProfileOut:
    return await _set_enabled(log_source_id, True, session, settings, principal)


@router.post("/log-sources/{log_source_id}/profile/disable", response_model=WafProfileOut)
async def disable_waf_profile(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> WafProfileOut:
    return await _set_enabled(log_source_id, False, session, settings, principal)


@router.get(
    "/log-sources/{log_source_id}/profile/history",
    response_model=list[WafProfileHistoryEntryOut],
)
async def get_waf_profile_history(
    log_source_id: uuid.UUID,
    session: SessionDep,
) -> list[WafProfileHistoryEntryOut]:
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
        WafProfileHistoryEntryOut(
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


@router.get("/sources", response_model=list[WafSourceStatusOut])
async def list_waf_sources(session: SessionDep, settings: SettingsDep) -> list[WafSourceStatusOut]:
    return await waf_source_statuses(session, settings=settings, now=datetime.now(UTC))


@router.get("/ariel-budget", response_model=WafArielBudgetOut)
async def get_waf_ariel_budget(session: SessionDep, settings: SettingsDep) -> WafArielBudgetOut:
    """Global WAF Ariel concurrency: configured limit and permits held now."""
    return await waf_ariel_budget_status(session, settings=settings)


@router.get("/sources/{profile_id}", response_model=WafSourceStatusOut)
async def get_waf_source(
    profile_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> WafSourceStatusOut:
    found = await waf_source_statuses(
        session, settings=settings, now=datetime.now(UTC), profile_id=profile_id
    )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source profile not found")
    return found[0]
