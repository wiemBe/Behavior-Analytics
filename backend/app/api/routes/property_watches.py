"""Property watches: authoring, the editor's pickers, history reads, the preview and the pins.

The router mount requires ``read:property_watches`` (``read:*`` satisfies it, and values are
shown to every such caller, design §G.7.2 D-22). Creating, editing, enabling, pinning,
deleting and previewing also require ``property_watch:write``, which ``read:*`` does not
grant; ``admin:*`` does, so the admin role does not list the string.

``/catalog``, ``/scope-options`` and ``/property-options`` are registered before
``/{watch_id}``, because none of them is a watch id.

Validation is ``services.property_watches.validate_draft``. Every reason comes back in one
422, the F.11 shape ``{"message", "reasons"}``. The caps of design §G.14 on enabling are
409 and name their numbers. A watch is always created disabled.

``property_watch`` carries ``watch_version`` for the semantics of its values and uses
``updated_at`` as the optimistic token, as ``custom_rule`` does: a patch carries
``expected_updated_at`` and a mismatch is 409 with ``current_version`` and ``updated_by``.

The preview is one real bounded run of the **stored** watch: two searches through the
provider, the rate limiter of a search, nothing stored. The browser sends no AQL.

Deleting a watch deletes every value it holds (cascade in the schema). Resolving its open
alerts first is PW-4's (alerting does not exist yet) and the 409 while an F.1 rule pins one
of its values is PW-6's (the column does not exist yet).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.custom_rules.alerts import alerting_mode
from app.models.enums import (
    PwAnomalyType,
    PwReferenceKind,
    PwScopeKind,
    PwWatchStatus,
    Severity,
)
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch
from app.property_watch.reference import PW_MAX_PROPERTIES, PW_NORMALIZED_FIELD_ALLOWLIST
from app.providers.base import QRadarProvider
from app.providers.factory import build_provider_for_instance
from app.repositories.audit import record_audit
from app.schemas.property_watch import (
    PagedWatchValues,
    PinsIn,
    PinsOut,
    PreviewOut,
    PropertyOptionOut,
    PropertyOptionsOut,
    PropertyWatchCreate,
    PropertyWatchOut,
    PropertyWatchUpdate,
    ScopeOptionOut,
    WatchAqlOut,
    WatchBucketOut,
    WatchCatalogOut,
    WatchScopeOut,
    WatchSeriesOut,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_PROPERTY_WATCH_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services import property_watches as svc
from app.services.property_catalog import PropertyCatalogService, get_property_catalog_service

router = APIRouter(prefix="/property-watches", tags=["property-watches"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]
HoursQuery = Annotated[int, Query(ge=svc.HOURS_MIN, le=svc.HOURS_MAX)]

_NAME_CONSTRAINT = "uq_property_watch_name"
_ENABLED_CAP_LOCK_KEY = 5_214_200_003
_NULLABLE_PATCH = frozenset(
    {"description", "log_source_id", "scope_type_id", "scope_group_id"}
)
_SCOPE_FIELDS = frozenset({"scope_kind", "log_source_id", "scope_type_id", "scope_group_id"})

ProviderFactory = Callable[[QRadarInstance], QRadarProvider]


def get_provider_factory() -> ProviderFactory:
    """How the preview and the property picker reach a console. A test overrides this."""
    return build_provider_for_instance


ProviderFactoryDep = Annotated[ProviderFactory, Depends(get_provider_factory)]


def _preview_limit() -> str:
    """The tighter limit of a search-spending route, read per request."""
    return get_settings().rate_limit_search_execution


def _guard(principal: Principal) -> None:
    try:
        require_permission(principal, PERM_PROPERTY_WATCH_WRITE)
    except PermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _reject(reasons: list[str]) -> None:
    """422 with every reason."""
    if reasons:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": "; ".join(reasons), "reasons": reasons},
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bumped(previous: datetime) -> datetime:
    """A token that moves even when two writes share a microsecond."""
    now = datetime.now(UTC)
    if now <= previous:
        return previous + timedelta(microseconds=1)
    return now


def _conflict(watch: PropertyWatch) -> dict[str, str | None]:
    current = _utc(watch.updated_at).isoformat()
    return {
        "message": f"watch version conflict; current version is {current}",
        "current_version": current,
        "updated_by": watch.updated_by,
    }


def _bound(requested: int, settings: Settings) -> int:
    return min(requested, settings.api_max_page_size)


async def _default_instance(
    session: AsyncSession, instance_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The named instance, or the single configured one."""
    if instance_id is not None:
        return instance_id
    return await session.scalar(select(QRadarInstance.id).limit(1))


async def _load(session: AsyncSession, watch_id: uuid.UUID) -> PropertyWatch:
    watch = await session.get(PropertyWatch, watch_id)
    if watch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property watch not found")
    return watch


async def _locked(session: AsyncSession, watch_id: uuid.UUID) -> PropertyWatch:
    watch = await session.scalar(
        select(PropertyWatch).where(PropertyWatch.id == watch_id).with_for_update()
    )
    if watch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property watch not found")
    return watch


async def _lock_enabled_cap(session: AsyncSession) -> None:
    """Serialize transactions that may consume one enabled-watch slot."""
    await session.execute(select(func.pg_advisory_xact_lock(_ENABLED_CAP_LOCK_KEY)))


async def _flush_name(session: AsyncSession, name: str) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if _NAME_CONSTRAINT not in str(exc):
            raise
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"a property watch named '{name}' already exists"
        ) from exc


async def _one_out(
    session: AsyncSession, watch: PropertyWatch, settings: Settings, *, detail: bool = False
) -> PropertyWatchOut:
    mode = alerting_mode(settings.pw_alerting_enabled)
    return (await svc.watch_outs(session, [watch], settings, alerting_mode=mode, detail=detail))[0]


def _raw_properties(refs: list) -> list[dict[str, str]]:
    return [{"kind": r.kind.value, "name": r.name} for r in refs]


# ------------------------------------------------------------------------ editor reads
@router.get("/catalog", response_model=WatchCatalogOut)
async def get_catalog(session: SessionDep, settings: SettingsDep) -> WatchCatalogOut:
    """The editor's switches and bounds. Registered before ``/{watch_id}``."""
    enabled, used = await svc.enabled_cost_total(session, settings)
    return WatchCatalogOut(
        collection_enabled=settings.pw_collection_enabled,
        alerting_mode=alerting_mode(settings.pw_alerting_enabled),
        max_enabled=settings.pw_max_enabled_watches,
        enabled_count=enabled,
        search_budget=settings.pw_max_searches_per_tick,
        budget_used=used,
        normalized_fields=list(PW_NORMALIZED_FIELD_ALLOWLIST),
        reference_kinds=[k.value for k in PwReferenceKind],
        detectors=[d.value for d in PwAnomalyType],
        max_properties=PW_MAX_PROPERTIES,
        max_values_per_bucket=settings.pw_max_values_per_bucket,
        max_new_values_per_bucket=settings.pw_max_new_values_per_bucket,
        max_active_values_per_watch=settings.pw_max_active_values_per_watch,
        max_pinned_values=settings.pw_max_pinned_values,
        value_max_length=settings.pw_value_max_length,
        max_sources_per_search=settings.pw_max_sources_per_search,
        max_chunks_per_watch=settings.pw_max_chunks_per_watch,
        min_coverage=settings.pw_min_coverage,
        hours_min=svc.HOURS_MIN,
        hours_max=svc.HOURS_MAX,
        hours_default=svc.HOURS_DEFAULT,
        preview_searches=svc.PREVIEW_SEARCHES,
        preview_rate_limit=settings.rate_limit_search_execution,
    )


@router.get("/scope-options", response_model=list[ScopeOptionOut])
async def scope_options(
    session: SessionDep,
    settings: SettingsDep,
    kind: PwScopeKind,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 50,
    instance_id: uuid.UUID | None = None,
) -> list[ScopeOptionOut]:
    """Search the synced inventory for sources, types or groups.

    A type carries its number of synced sources and a group its resolved member count, both
    through PW-2b's resolver over the same bulk read the collector uses. A group that does
    not resolve (stale, too large) shows no count and names the reason in ``scope_reason``.
    """
    instance = await _default_instance(session, instance_id)
    if instance is None:
        return []
    size = _bound(limit, settings)
    if kind is PwScopeKind.LOG_SOURCE:
        query = select(LogSource).where(LogSource.instance_id == instance)
        if q:
            query = query.where(LogSource.name.icontains(q, autoescape=True))
        sources = (
            await session.scalars(query.order_by(LogSource.name, LogSource.id).limit(size))
        ).all()
        return [
            ScopeOptionOut(
                kind=kind, id=str(s.id), qradar_id=s.qradar_id, name=s.name, detail=s.type_name
            )
            for s in sources
        ]
    if kind is PwScopeKind.LOG_SOURCE_TYPE:
        tquery = select(LogSourceType).where(LogSourceType.instance_id == instance)
        if q:
            tquery = tquery.where(LogSourceType.name.icontains(q, autoescape=True))
        types = (
            await session.scalars(
                tquery.order_by(LogSourceType.name, LogSourceType.type_id).limit(size)
            )
        ).all()
        counts = await _resolved_options(
            session,
            settings,
            [
                svc.ScopeSubject(instance, kind, scope_type_id=t.type_id)
                for t in types
            ],
        )
        return [
            ScopeOptionOut(
                kind=kind,
                id=str(t.type_id),
                qradar_id=t.type_id,
                name=t.name,
                synced_source_count=counts[index][0],
                scope_state=counts[index][1],
                scope_reason=counts[index][2],
            )
            for index, t in enumerate(types)
        ]
    groups = {
        g.qradar_id: g
        for g in (
            await session.scalars(
                select(LogSourceGroup).where(LogSourceGroup.instance_id == instance)
            )
        ).all()
    }
    needle = (q or "").casefold()
    found = [
        (svc.group_path(gid, groups), group)
        for gid, group in groups.items()
        if not needle or needle in group.name.casefold()
    ]
    found.sort(key=lambda item: (item[0].casefold(), item[1].qradar_id))
    page = found[:size]
    counts = await _resolved_options(
        session,
        settings,
        [svc.ScopeSubject(instance, kind, scope_group_id=g.qradar_id) for _, g in page],
    )
    return [
        ScopeOptionOut(
            kind=kind,
            id=str(g.qradar_id),
            qradar_id=g.qradar_id,
            name=g.name,
            detail=path,
            synced_source_count=counts[index][0],
            scope_state=counts[index][1],
            scope_reason=counts[index][2],
        )
        for index, (path, g) in enumerate(page)
    ]


async def _resolved_options(
    session: AsyncSession, settings: Settings, subjects: list[svc.ScopeSubject]
) -> list[tuple[int | None, PwWatchStatus | None, str | None]]:
    """`(synced_source_count, state, reason)` per subject, from the shared resolver.

    A scope that resolved reports its count. A scope that did not reports no count and names
    the reason, except an empty one whose emptiness the inventory measured: that is a real
    zero and is reported as one (design §G.4).
    """
    scopes = await svc.resolve_subjects(session, settings, subjects, now=datetime.now(UTC))
    out: list[tuple[int | None, PwWatchStatus | None, str | None]] = []
    for scope in scopes:
        measurable = scope.resolved or scope.status is PwWatchStatus.SCOPE_EMPTY
        count = scope.member_count if measurable else None
        out.append((count, scope.status, scope.reason))
    return out


@router.get("/property-options", response_model=PropertyOptionsOut)
async def property_options(
    session: SessionDep,
    settings: SettingsDep,
    scope_kind: PwScopeKind,
    scope_id: Annotated[str, Query(min_length=1, max_length=64)],
    service: Annotated[PropertyCatalogService, Depends(get_property_catalog_service)],
    factory: ProviderFactoryDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    instance_id: uuid.UUID | None = None,
    refresh: bool = False,
) -> PropertyOptionsOut:
    """The custom-property catalog filtered by the scope's types (a suggestion, never a gate)."""
    instance_key: uuid.UUID | None
    type_ids: tuple[int, ...]
    if scope_kind is PwScopeKind.LOG_SOURCE:
        try:
            source_id = uuid.UUID(scope_id)
        except ValueError:
            _reject(["scope_id of a LOG_SOURCE scope is the source's id"])
            raise
        source = await session.get(LogSource, source_id)
        if source is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
        instance_key = source.instance_id
        type_ids = () if source.type_id is None else (source.type_id,)
    else:
        try:
            number = int(scope_id)
        except ValueError:
            _reject([f"scope_id of a {scope_kind.value} scope is a number"])
            raise
        instance_key = await _default_instance(session, instance_id)
        if instance_key is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no QRadar instance is registered")
        if scope_kind is PwScopeKind.LOG_SOURCE_TYPE:
            type_ids = (number,)
        else:
            known = await session.scalar(
                select(LogSourceGroup.qradar_id).where(
                    LogSourceGroup.instance_id == instance_key,
                    LogSourceGroup.qradar_id == number,
                )
            )
            if known is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "log source group not found in the inventory"
                )
            group_scope = (
                await svc.resolve_subjects(
                    session,
                    settings,
                    [svc.ScopeSubject(instance_key, scope_kind, scope_group_id=number)],
                    now=datetime.now(UTC),
                )
            )[0]
            if not group_scope.resolved:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    group_scope.reason or "the group's scope cannot be resolved",
                )
            # The members' types come from the resolver's own walk, so the picker cannot
            # disagree with the count the editor shows (design §G.4).
            type_ids = tuple(
                sorted({m.type_id for m in group_scope.members if m.type_id is not None})
            )
    instance = await session.get(QRadarInstance, instance_key)
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "QRadar instance not found")
    catalog = await service.catalog_for_types(
        instance_id=instance.id,
        provider_factory=lambda: factory(instance),
        source_type_ids=type_ids,
        refresh=refresh,
    )
    needle = (q or "").casefold()
    wanted = set(type_ids)
    options = [
        PropertyOptionOut(
            name=item.name,
            property_type=item.property_type,
            name_allowed=item.name_allowed,
            defined_on_type_ids=list(item.defined_on_type_ids),
            covers_scope=bool(wanted) and wanted <= set(item.defined_on_type_ids),
        )
        for item in catalog.properties
        if not needle or needle in item.name.casefold()
    ]
    return PropertyOptionsOut(
        status=catalog.status,
        fetched_at=catalog.fetched_at,
        scope_type_ids=sorted(wanted),
        properties=options,
        normalized_fields=list(PW_NORMALIZED_FIELD_ALLOWLIST),
        detail=catalog.detail,
    )


# --------------------------------------------------------------------------- list/read
@router.get("", response_model=list[PropertyWatchOut])
async def list_watches(
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PropertyWatchOut]:
    rows = list(
        (
            await session.scalars(
                select(PropertyWatch)
                .order_by(PropertyWatch.name.asc(), PropertyWatch.id.asc())
                .limit(_bound(limit, settings))
                .offset(offset)
            )
        ).all()
    )
    mode = alerting_mode(settings.pw_alerting_enabled)
    return await svc.watch_outs(session, rows, settings, alerting_mode=mode)


@router.post("", response_model=PropertyWatchOut, status_code=status.HTTP_201_CREATED)
async def create_watch(
    body: PropertyWatchCreate,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> PropertyWatchOut:
    _guard(principal)
    properties = _raw_properties(body.properties)
    detectors = (
        [d.value for d in body.detectors]
        if body.detectors is not None
        else [d.value for d in svc.default_detectors(len(properties))]
    )
    detectors = list(dict.fromkeys(detectors))
    draft = svc.WatchDraft(
        scope_kind=body.scope_kind,
        log_source_id=body.log_source_id,
        scope_type_id=body.scope_type_id,
        scope_group_id=body.scope_group_id,
        properties=properties,
        pinned_values=body.pinned_values,
        detectors=detectors,
    )
    reasons = svc.validate_draft(draft, max_pinned=settings.pw_max_pinned_values)
    instance_id: uuid.UUID | None = None
    if body.scope_kind is PwScopeKind.LOG_SOURCE and body.log_source_id is not None:
        source = await session.get(LogSource, body.log_source_id)
        if source is None:
            reasons.append(f"log source {body.log_source_id} does not exist")
        else:
            instance_id = source.instance_id
            if body.instance_id is not None and body.instance_id != instance_id:
                reasons.append("instance_id differs from the log source's instance")
    else:
        instance_id = await _default_instance(session, body.instance_id)
        if instance_id is None:
            reasons.append("no QRadar instance is registered")
        else:
            reasons.extend(await svc.scope_existence_reasons(session, draft, instance_id))
    _reject(reasons)
    assert instance_id is not None
    taken = await session.scalar(select(PropertyWatch.id).where(PropertyWatch.name == body.name))
    if taken is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"a property watch named '{body.name}' already exists"
        )
    now = datetime.now(UTC)
    watch = PropertyWatch(
        instance_id=instance_id,
        name=body.name,
        description=body.description,
        scope_kind=body.scope_kind,
        log_source_id=body.log_source_id,
        scope_type_id=body.scope_type_id,
        scope_group_id=body.scope_group_id,
        properties=properties,
        pinned_values=[list(p) for p in body.pinned_values],
        detectors=detectors,
        severity=body.severity,
        watch_version=1,
        enabled=False,
        created_by=principal.subject,
        updated_by=principal.subject,
        created_at=now,
        updated_at=now,
    )
    session.add(watch)
    await _flush_name(session, body.name)
    await record_audit(
        session,
        action="property_watch.create",
        actor=principal.subject,
        object_type="property_watch",
        object_id=str(watch.id),
        detail={"before": None, "after": svc.watch_snapshot(watch)},
    )
    return await _one_out(session, watch, settings, detail=True)


@router.get("/{watch_id}", response_model=PropertyWatchOut)
async def get_watch(
    watch_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> PropertyWatchOut:
    watch = await _load(session, watch_id)
    return await _one_out(session, watch, settings, detail=True)


@router.get("/{watch_id}/scope", response_model=WatchScopeOut)
async def get_watch_scope(
    watch_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> WatchScopeOut:
    """The resolved scope, the groups walked, the inventory age and the membership history.

    ``read:property_watches`` (the mount guard). A scope that does not resolve is returned
    with its state and reason and whatever the last sync holds, never a zero.
    """
    watch = await _load(session, watch_id)
    return await svc.watch_scope(session, watch, settings, now=datetime.now(UTC))


def _null_reasons(payload: PropertyWatchUpdate) -> list[str]:
    return [
        f"{name} must not be null"
        for name in sorted(payload.model_fields_set - {"expected_updated_at", *_NULLABLE_PATCH})
        if getattr(payload, name) is None
    ]


def _editable(watch: PropertyWatch) -> dict[str, object]:
    """The fields a patch can change, comparable before and after."""
    return {
        "name": watch.name,
        "description": watch.description,
        "scope": svc.scope_label(watch),
        "properties": [dict(p) for p in watch.properties],
        "pinned_values": [list(p) for p in watch.pinned_values],
        "detectors": [str(d) for d in watch.detectors],
        "severity": str(watch.severity),
        "enabled": bool(watch.enabled),
    }


@dataclass(frozen=True)
class _Merged:
    """The watch's editable columns as a patch would leave them. Nothing is written."""

    name: str
    description: str | None
    scope_kind: PwScopeKind
    log_source_id: uuid.UUID | None
    scope_type_id: int | None
    scope_group_id: int | None
    properties: list[dict[str, str]]
    pinned_values: list[list[str]]
    detectors: list[str]
    severity: Severity
    enabled: bool


def _merge(watch: PropertyWatch, payload: PropertyWatchUpdate) -> _Merged:
    present = payload.model_fields_set - {"expected_updated_at"}
    kind = PwScopeKind(str(watch.scope_kind))
    source_id, type_id, group_id = watch.log_source_id, watch.scope_type_id, watch.scope_group_id
    if present & _SCOPE_FIELDS:
        # The scope travels as a unit: what the patch does not name is cleared.
        if payload.scope_kind is not None:
            kind = payload.scope_kind
        source_id = payload.log_source_id if "log_source_id" in present else None
        type_id = payload.scope_type_id if "scope_type_id" in present else None
        group_id = payload.scope_group_id if "scope_group_id" in present else None
    return _Merged(
        name=payload.name if "name" in present and payload.name is not None else watch.name,
        description=payload.description if "description" in present else watch.description,
        scope_kind=kind,
        log_source_id=source_id,
        scope_type_id=type_id,
        scope_group_id=group_id,
        properties=(
            _raw_properties(payload.properties)
            if "properties" in present and payload.properties is not None
            else [dict(p) for p in watch.properties]
        ),
        pinned_values=(
            [list(p) for p in payload.pinned_values]
            if "pinned_values" in present and payload.pinned_values is not None
            else [list(p) for p in watch.pinned_values]
        ),
        detectors=(
            list(dict.fromkeys(d.value for d in payload.detectors))
            if "detectors" in present and payload.detectors is not None
            else [str(d) for d in watch.detectors]
        ),
        severity=(
            payload.severity
            if "severity" in present and payload.severity is not None
            else Severity(str(watch.severity))
        ),
        enabled=(
            payload.enabled
            if "enabled" in present and payload.enabled is not None
            else bool(watch.enabled)
        ),
    )


def _label(kind: PwScopeKind, source: object, type_id: object, group_id: object) -> str:
    if kind is PwScopeKind.LOG_SOURCE:
        return f"{kind.value}:{source}"
    if kind is PwScopeKind.LOG_SOURCE_TYPE:
        return f"{kind.value}:{type_id}"
    return f"{kind.value}:{group_id}"


@router.patch("/{watch_id}", response_model=PropertyWatchOut)
async def update_watch(
    watch_id: uuid.UUID,
    payload: PropertyWatchUpdate,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> PropertyWatchOut:
    _guard(principal)
    watch = await _locked(session, watch_id)
    if _utc(payload.expected_updated_at) != _utc(watch.updated_at):
        raise HTTPException(status.HTTP_409_CONFLICT, _conflict(watch))
    _reject(_null_reasons(payload))
    merged = _merge(watch, payload)
    kind = merged.scope_kind
    draft = svc.WatchDraft(
        scope_kind=kind,
        log_source_id=merged.log_source_id,
        scope_type_id=merged.scope_type_id,
        scope_group_id=merged.scope_group_id,
        properties=merged.properties,
        pinned_values=merged.pinned_values,
        detectors=merged.detectors,
    )
    reasons = svc.validate_draft(draft, max_pinned=settings.pw_max_pinned_values)
    before = _editable(watch)
    after_scope = _label(kind, merged.log_source_id, merged.scope_type_id, merged.scope_group_id)
    scope_changed = after_scope != before["scope"]
    instance_id = watch.instance_id
    if scope_changed:
        if kind is PwScopeKind.LOG_SOURCE and draft.log_source_id is not None:
            source = await session.get(LogSource, draft.log_source_id)
            if source is None:
                reasons.append(f"log source {draft.log_source_id} does not exist")
            else:
                instance_id = source.instance_id
        else:
            reasons.extend(await svc.scope_existence_reasons(session, draft, instance_id))
    _reject(reasons)
    after: dict[str, object] = {
        "name": merged.name,
        "description": merged.description,
        "scope": after_scope,
        "properties": merged.properties,
        "pinned_values": merged.pinned_values,
        "detectors": merged.detectors,
        "severity": merged.severity.value,
        "enabled": merged.enabled,
    }
    changed = {key for key in after if after[key] != before[key]}
    if not changed:
        return await _one_out(session, watch, settings, detail=True)
    new_name = merged.name
    if "name" in changed:
        taken = await session.scalar(
            select(PropertyWatch.id).where(
                PropertyWatch.name == new_name, PropertyWatch.id != watch.id
            )
        )
        if taken is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"a property watch named '{new_name}' already exists"
            )
    was_enabled = bool(watch.enabled)
    wants_enabled = merged.enabled
    # Enabling, or changing what an enabled watch costs, takes one of the capped slots.
    needs_capacity = wants_enabled and (
        not was_enabled or bool(changed & {"scope", "properties", "pinned_values"})
    )
    if needs_capacity:
        await _lock_enabled_cap(session)
    snapshot_before = svc.watch_snapshot(watch)
    watch.name = new_name
    watch.description = merged.description
    watch.scope_kind = kind
    watch.instance_id = instance_id
    watch.log_source_id = draft.log_source_id
    watch.scope_type_id = draft.scope_type_id
    watch.scope_group_id = draft.scope_group_id
    watch.properties = list(draft.properties)
    watch.pinned_values = [list(p) for p in draft.pinned_values]
    watch.detectors = list(draft.detectors)
    watch.severity = merged.severity
    watch.enabled = wants_enabled
    if changed & {"scope", "properties"}:
        # The semantics identity (design §G.3): values never compare across it.
        watch.watch_version = int(watch.watch_version) + 1
    watch.updated_by = principal.subject
    watch.updated_at = _bumped(_utc(watch.updated_at))
    await _flush_name(session, watch.name)
    if needs_capacity:
        capacity = await svc.enable_reasons(session, watch, settings)
        if capacity:
            raise HTTPException(status.HTTP_409_CONFLICT, "; ".join(capacity))
    action = (
        ("property_watch.enable" if watch.enabled else "property_watch.disable")
        if changed == {"enabled"}
        else "property_watch.update"
    )
    await record_audit(
        session,
        action=action,
        actor=principal.subject,
        object_type="property_watch",
        object_id=str(watch.id),
        detail={"before": snapshot_before, "after": svc.watch_snapshot(watch)},
    )
    return await _one_out(session, watch, settings, detail=True)


@router.delete("/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watch(
    watch_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    """Delete the watch and, by cascade, every value, bucket, baseline and incident it holds."""
    _guard(principal)
    watch = await _locked(session, watch_id)
    snapshot = svc.watch_snapshot(watch)
    key = str(watch.id)
    await session.delete(watch)
    await record_audit(
        session,
        action="property_watch.delete",
        actor=principal.subject,
        object_type="property_watch",
        object_id=key,
        detail={"before": snapshot, "after": None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- history reads
@router.get("/{watch_id}/buckets", response_model=list[WatchBucketOut])
async def list_buckets(
    watch_id: uuid.UUID, session: SessionDep, hours: HoursQuery = svc.HOURS_DEFAULT
) -> list[WatchBucketOut]:
    """Collected buckets, oldest first. A gap is an absent bucket, never a zero."""
    watch = await _load(session, watch_id)
    return await svc.watch_buckets(session, watch, hours=hours, now=datetime.now(UTC))


@router.get("/{watch_id}/aql", response_model=WatchAqlOut)
async def get_watch_aql(
    watch_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> WatchAqlOut:
    """The generated AQL of the stored watch, read-only text.

    No search runs; nothing is read from the request but the id.
    """
    watch = await _load(session, watch_id)
    return await svc.watch_aql(session, watch, settings, datetime.now(UTC))


@router.get("/{watch_id}/values", response_model=PagedWatchValues)
async def list_values(
    watch_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    hours: HoursQuery = svc.HOURS_DEFAULT,
    sort: Literal["events", "value", "first_seen"] = "events",
    limit: Annotated[int, Query(ge=1, le=10_000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedWatchValues:
    """Tracked values of the current version, pinned first. Addressed by id, never in a URL."""
    watch = await _load(session, watch_id)
    size = _bound(limit, settings)
    items, total = await svc.list_values(
        session, watch, hours=hours, sort=sort, limit=size, offset=offset,
        now=datetime.now(UTC),
    )
    return PagedWatchValues(items=items, total=total, limit=size, offset=offset)


@router.get("/{watch_id}/series", response_model=WatchSeriesOut)
async def value_series(
    watch_id: uuid.UUID,
    session: SessionDep,
    value_ids: Annotated[list[uuid.UUID], Query()],
    hours: HoursQuery = svc.HOURS_DEFAULT,
) -> WatchSeriesOut:
    """Per-bucket series of at most 12 values with their expected bands, in one bulk read."""
    watch = await _load(session, watch_id)
    unique = list(dict.fromkeys(value_ids))
    if len(unique) > svc.SERIES_MAX_VALUES:
        _reject([f"at most {svc.SERIES_MAX_VALUES} values per request, not {len(unique)}"])
    series, missing = await svc.value_series(
        session, watch, unique, hours=hours, now=datetime.now(UTC)
    )
    if missing:
        _reject(
            [f"value {value} is not a tracked value of this watch's current version"
             for value in missing]
        )
    return WatchSeriesOut(hours=hours, series=series)


# ------------------------------------------------------------------------------ preview
@router.post("/{watch_id}/preview", response_model=PreviewOut)
@limiter.limit(_preview_limit)
async def preview_watch(
    request: Request,
    response: Response,
    watch_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    factory: ProviderFactoryDep,
) -> PreviewOut:
    """One real bounded run of the stored watch over the last closed hour.

    Two searches (totals and grouped), the search rate limit, nothing persisted, nothing
    admitted. The response is marked ``PREVIEW`` and carries the generated AQL read-only.
    """
    _guard(principal)
    watch = await _load(session, watch_id)
    instance = await session.get(QRadarInstance, watch.instance_id)
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "QRadar instance not found")
    now = datetime.now(UTC)
    try:
        scope_ids = await svc.preview_scope_ids(session, watch, settings, now)
    except svc.PreviewRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    provider = factory(instance)
    try:
        result = await svc.run_preview(watch, scope_ids, settings, provider, now=now)
    except svc.PreviewRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    finally:
        await provider.aclose()
    await record_audit(
        session,
        action="property_watch.preview",
        actor=principal.subject,
        object_type="property_watch",
        object_id=str(watch.id),
        outcome="FAILURE" if result.outcome == "NOT_MEASURED" else "SUCCESS",
        detail=svc.preview_audit_counts(result),
    )
    return result


# --------------------------------------------------------------------------------- pins
@router.put("/{watch_id}/pins", response_model=PinsOut)
async def put_pins(
    watch_id: uuid.UUID,
    payload: PinsIn,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> PinsOut:
    """Replace the pinned list. Each pin is checked against ``PW_PINNED_VALUE_PATTERN``.

    Pins do not bump ``watch_version``. F.1 rules that depend on a pinned value are not
    reported yet: the dependency column arrives with PW-6.
    """
    _guard(principal)
    watch = await _locked(session, watch_id)
    if _utc(payload.expected_updated_at) != _utc(watch.updated_at):
        raise HTTPException(status.HTTP_409_CONFLICT, _conflict(watch))
    _reject(
        svc.pin_reasons(payload.pinned_values, len(watch.properties), settings.pw_max_pinned_values)
    )
    current = {tuple(p) for p in watch.pinned_values}
    wanted = [list(p) for p in payload.pinned_values]
    incoming = {tuple(p) for p in wanted}
    added, removed = len(incoming - current), len(current - incoming)
    if wanted != [list(p) for p in watch.pinned_values]:
        snapshot_before = svc.watch_snapshot(watch)
        watch.pinned_values = wanted
        watch.updated_by = principal.subject
        watch.updated_at = _bumped(_utc(watch.updated_at))
        await session.flush()
        if watch.enabled:
            capacity = await svc.enable_reasons(session, watch, settings)
            if capacity:
                raise HTTPException(status.HTTP_409_CONFLICT, "; ".join(capacity))
        await record_audit(
            session,
            action="property_watch.update",
            actor=principal.subject,
            object_type="property_watch",
            object_id=str(watch.id),
            detail={"before": snapshot_before, "after": svc.watch_snapshot(watch)},
        )
    return PinsOut(
        watch=await _one_out(session, watch, settings, detail=True), added=added, removed=removed
    )
