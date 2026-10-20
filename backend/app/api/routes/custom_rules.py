"""Custom threshold rules: authoring, preview replay and breach history.

The router mount requires ``read:custom_rules`` (``read:*`` satisfies it).
Creating, editing and deleting also require ``custom_rule:write``, which
``read:*`` does not grant. ``admin:*`` does, so the admin role does not
list the string.

``GET /catalog`` is that same read. It lists the metric catalog, the subject
pairing and the platform switches for the rule editor. It is registered
before ``/{rule_id}``, because ``catalog`` is not a rule id.

Validation is F-1's ``validate_rule_draft``. Every domain reason comes back
in one 422, the F-4 shape ``{"message", "reasons"}``. The enabled-rule cap
on its own is 409 and names the limit. A missing subject is one more
reason; a retired subject is still a legal rule.

``custom_rule`` has no integer version. A patch carries ``expected_updated_at``,
the row's ``updated_at``, and a mismatch is 409 in the WAF-profile shape.
There is no migration.

Preview calls ``replay``, which calls ``fold_rule`` over one bulk read.
It persists nothing and it is not audited. Delete resolves the open alert
with the deleting principal, then removes the row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.routing import default_policy
from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.custom_rules.alerts import alerting_mode, resolve_deleted_rule_alert
from app.custom_rules.catalog import CUSTOM_RULE_METRICS, catalog_entry
from app.custom_rules.engine import _KIND_FAMILY
from app.custom_rules.preview import PreviewResult, replay
from app.custom_rules.validation import (
    _SUBJECT_COLUMNS,
    RULE_OPERATORS,
    CustomRuleDraft,
    validate_rule_draft,
)
from app.models.custom_rule import CustomRule, CustomRuleBreach
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind, SourceClass
from app.models.ips import IpsEntity
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch, PwValue
from app.models.waf import WafEndpoint
from app.property_watch.facts import split_tuple_key
from app.repositories.audit import record_audit
from app.schemas.custom_rule import (
    BreachIntervalOut,
    BreachOut,
    CatalogMetricOut,
    CatalogSubjectKindOut,
    CustomRuleCatalogOut,
    CustomRuleCreate,
    CustomRuleOut,
    CustomRulePreviewIn,
    CustomRuleUpdate,
    PreviewBucketOut,
    PreviewOut,
)
from app.schemas.search_cadence import CadenceOut
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_CUSTOM_RULE_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services.search_cadence import SearchCadenceService, bucket_seconds

router = APIRouter(prefix="/custom-rules", tags=["custom-rules"])

#: Inclusive bounds of the preview ``hours`` query. The catalog reports them.
PREVIEW_HOURS_MIN = 1
PREVIEW_HOURS_MAX = 168

PrincipalDep = Annotated[Principal, Depends(get_principal)]

_NAME_CONSTRAINT = "uq_custom_rule_name"
_PW_VALUE_FK_CONSTRAINT = "fk_custom_rule_pw_value_id_pw_value"
_ENABLED_CAP_LOCK_KEY = 5_214_200_001
_NULLABLE_PATCH = frozenset(
    {
        "description",
        "log_source_id",
        "source_class",
        "waf_endpoint_id",
        "ips_entity_id",
        "pw_value_id",
    }
)
_SOURCE_KINDS = frozenset(
    {CustomRuleSubjectKind.LOG_SOURCE, CustomRuleSubjectKind.IPS_SOURCE}
)


def _guard(principal: Principal) -> None:
    try:
        require_permission(principal, PERM_CUSTOM_RULE_WRITE)
    except PermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _is_cap_reason(reason: str) -> bool:
    return reason.startswith("at most ") and "rules may be enabled at once" in reason


def _reject(reasons: list[str]) -> None:
    """422 with every reason, or 409 when the only reason is the enabled cap."""
    if not reasons:
        return
    if all(_is_cap_reason(reason) for reason in reasons):
        raise HTTPException(status.HTTP_409_CONFLICT, reasons[0])
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        {"message": "; ".join(reasons), "reasons": reasons},
    )


def _as_str(value: object) -> str:
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


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


def _conflict(rule: CustomRule) -> dict[str, str | None]:
    current = _utc(rule.updated_at).isoformat()
    return {
        "message": f"rule version conflict; current version is {current}",
        "current_version": current,
        "updated_by": rule.updated_by,
    }


def _bound(requested: int, settings: Settings) -> int:
    return min(requested, settings.api_max_page_size)


def _threshold_unit(metric: str) -> str | None:
    try:
        return catalog_entry(CustomRuleMetric(metric)).unit
    except (ValueError, KeyError):
        return None


def _snapshot(rule: CustomRule) -> dict[str, object]:
    return {
        "name": rule.name,
        "description": rule.description,
        "subject_kind": _as_str(rule.subject_kind),
        "log_source_id": None if rule.log_source_id is None else str(rule.log_source_id),
        "source_class": rule.source_class,
        "waf_endpoint_id": None if rule.waf_endpoint_id is None else str(rule.waf_endpoint_id),
        "ips_entity_id": None if rule.ips_entity_id is None else str(rule.ips_entity_id),
        "pw_value_id": None if rule.pw_value_id is None else str(rule.pw_value_id),
        "metric": _as_str(rule.metric),
        "operator": rule.operator,
        "threshold_value": float(rule.threshold_value),
        "sustain_buckets": int(rule.sustain_buckets),
        "recovery_buckets": int(rule.recovery_buckets),
        "severity": _as_str(rule.severity),
        "enabled": bool(rule.enabled),
    }


def _draft_from_create(body: CustomRuleCreate | CustomRulePreviewIn) -> CustomRuleDraft:
    return CustomRuleDraft(
        subject_kind=body.subject_kind,
        metric=body.metric,
        operator=body.operator,
        threshold_value=body.threshold_value,
        sustain_buckets=body.sustain_buckets,
        recovery_buckets=body.recovery_buckets,
        log_source_id=body.log_source_id,
        source_class=body.source_class,
        waf_endpoint_id=body.waf_endpoint_id,
        ips_entity_id=body.ips_entity_id,
        pw_value_id=body.pw_value_id,
        enabled=body.enabled,
    )


def _draft_from_patch(rule: CustomRule, payload: CustomRuleUpdate) -> CustomRuleDraft:
    kind = CustomRuleSubjectKind(_as_str(rule.subject_kind))
    metric = _as_str(rule.metric)
    operator = rule.operator
    threshold = float(rule.threshold_value)
    sustain = int(rule.sustain_buckets)
    recovery = int(rule.recovery_buckets)
    log_source_id = rule.log_source_id
    source_class = rule.source_class
    waf_endpoint_id = rule.waf_endpoint_id
    ips_entity_id = rule.ips_entity_id
    pw_value_id = rule.pw_value_id
    enabled = bool(rule.enabled)
    present = payload.model_fields_set
    if "subject_kind" in present and payload.subject_kind is not None:
        kind = payload.subject_kind
    if "metric" in present and payload.metric is not None:
        metric = payload.metric
    if "operator" in present and payload.operator is not None:
        operator = payload.operator
    if "threshold_value" in present and payload.threshold_value is not None:
        threshold = payload.threshold_value
    if "sustain_buckets" in present and payload.sustain_buckets is not None:
        sustain = payload.sustain_buckets
    if "recovery_buckets" in present and payload.recovery_buckets is not None:
        recovery = payload.recovery_buckets
    if "log_source_id" in present:
        log_source_id = payload.log_source_id
    if "source_class" in present:
        source_class = payload.source_class
    if "waf_endpoint_id" in present:
        waf_endpoint_id = payload.waf_endpoint_id
    if "ips_entity_id" in present:
        ips_entity_id = payload.ips_entity_id
    if "pw_value_id" in present:
        pw_value_id = payload.pw_value_id
    if "enabled" in present and payload.enabled is not None:
        enabled = payload.enabled
    return CustomRuleDraft(
        subject_kind=kind,
        metric=metric,
        operator=operator,
        threshold_value=threshold,
        sustain_buckets=sustain,
        recovery_buckets=recovery,
        log_source_id=log_source_id,
        source_class=source_class,
        waf_endpoint_id=waf_endpoint_id,
        ips_entity_id=ips_entity_id,
        pw_value_id=pw_value_id,
        enabled=enabled,
    )


def _null_reasons(payload: CustomRuleUpdate) -> list[str]:
    reasons: list[str] = []
    for name in payload.model_fields_set - {"expected_updated_at", *_NULLABLE_PATCH}:
        if getattr(payload, name) is None:
            reasons.append(f"{name} must not be null")
    return reasons


def _merged(rule: CustomRule, payload: CustomRuleUpdate) -> dict[str, object]:
    merged = _snapshot(rule)
    present = payload.model_fields_set - {"expected_updated_at"}
    for name in present:
        value = getattr(payload, name)
        if name in {"log_source_id", "waf_endpoint_id", "ips_entity_id", "pw_value_id"}:
            merged[name] = None if value is None else str(value)
        elif name == "threshold_value" and value is not None:
            merged[name] = float(value)
        elif name in {"sustain_buckets", "recovery_buckets"} and value is not None:
            merged[name] = int(value)
        elif name == "enabled" and value is not None:
            merged[name] = bool(value)
        elif isinstance(value, StrEnum):
            merged[name] = value.value
        else:
            merged[name] = value
    return merged


def _apply(rule: CustomRule, payload: CustomRuleUpdate) -> None:
    present = payload.model_fields_set - {"expected_updated_at"}
    if "name" in present and payload.name is not None:
        rule.name = payload.name
    if "description" in present:
        rule.description = payload.description
    if "subject_kind" in present and payload.subject_kind is not None:
        rule.subject_kind = payload.subject_kind
    if "log_source_id" in present:
        rule.log_source_id = payload.log_source_id
    if "source_class" in present:
        rule.source_class = payload.source_class
    if "waf_endpoint_id" in present:
        rule.waf_endpoint_id = payload.waf_endpoint_id
    if "ips_entity_id" in present:
        rule.ips_entity_id = payload.ips_entity_id
    if "pw_value_id" in present:
        rule.pw_value_id = payload.pw_value_id
    if "metric" in present and payload.metric is not None:
        rule.metric = payload.metric
    if "operator" in present and payload.operator is not None:
        rule.operator = payload.operator
    if "threshold_value" in present and payload.threshold_value is not None:
        rule.threshold_value = payload.threshold_value
    if "sustain_buckets" in present and payload.sustain_buckets is not None:
        rule.sustain_buckets = payload.sustain_buckets
    if "recovery_buckets" in present and payload.recovery_buckets is not None:
        rule.recovery_buckets = payload.recovery_buckets
    if "severity" in present and payload.severity is not None:
        rule.severity = payload.severity
    if "enabled" in present and payload.enabled is not None:
        rule.enabled = payload.enabled


def _breach_out(row: CustomRuleBreach) -> BreachOut:
    details = row.details if isinstance(row.details, dict) else {}
    return BreachOut(
        id=row.id,
        rule_id=row.rule_id,
        state=_as_str(row.state),
        first_bucket_start=row.first_bucket_start,
        last_bucket_start=row.last_bucket_start,
        opened_at=row.opened_at,
        recovering_at=row.recovering_at,
        resolved_at=row.resolved_at,
        worst_value=None if row.worst_value is None else float(row.worst_value),
        worst_measurement=(
            None if row.worst_measurement is None else _as_str(row.worst_measurement)
        ),
        last_value=None if row.last_value is None else float(row.last_value),
        last_measurement=(
            None if row.last_measurement is None else _as_str(row.last_measurement)
        ),
        unmeasured_buckets=int(row.unmeasured_buckets),
        subject_label=row.subject_label,
        details=dict(details),
    )


def _rule_out(
    rule: CustomRule,
    breach: CustomRuleBreach | None,
    settings: Settings,
) -> CustomRuleOut:
    active = breach if breach is not None and breach.id == rule.active_breach_id else None
    # A dangling pointer is not a breach. The row was removed; the response
    # says so by leaving the object null rather than failing the read.
    if rule.active_breach_id is None:
        active = None
    return CustomRuleOut(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        subject_kind=_as_str(rule.subject_kind),
        log_source_id=rule.log_source_id,
        source_class=rule.source_class,
        waf_endpoint_id=rule.waf_endpoint_id,
        ips_entity_id=rule.ips_entity_id,
        pw_value_id=rule.pw_value_id,
        metric=_as_str(rule.metric),
        operator=rule.operator,
        threshold_value=float(rule.threshold_value),
        threshold_unit=_threshold_unit(_as_str(rule.metric)),
        sustain_buckets=int(rule.sustain_buckets),
        recovery_buckets=int(rule.recovery_buckets),
        severity=_as_str(rule.severity),
        enabled=bool(rule.enabled),
        consecutive_breaching=int(rule.consecutive_breaching),
        consecutive_under=int(rule.consecutive_under),
        last_evaluated_bucket_start=rule.last_evaluated_bucket_start,
        last_evaluated_at=rule.last_evaluated_at,
        last_status=rule.last_status,
        last_status_reason=rule.last_status_reason,
        active_breach=None if active is None else _breach_out(active),
        alerting_mode=alerting_mode(settings.custom_rules_alerting_enabled),
        created_at=rule.created_at,
        updated_at=rule.updated_at,
        created_by=rule.created_by,
        updated_by=rule.updated_by,
    )


def _preview_out(result: PreviewResult) -> PreviewOut:
    return PreviewOut(
        threshold_value=result.threshold_value,
        unit=result.unit,
        operator=result.operator,
        fired_count=result.fired_count,
        measured_count=result.measured_count,
        unmeasured_count=result.unmeasured_count,
        buckets=[
            PreviewBucketOut(
                bucket_start=bucket.bucket_start,
                value=bucket.value,
                measurement=bucket.measurement,
                completeness=bucket.completeness,
                in_maintenance=bucket.in_maintenance,
            )
            for bucket in result.buckets
        ],
        breach_intervals=[
            BreachIntervalOut(start=interval.start, end=interval.end)
            for interval in result.breach_intervals
        ],
    )


async def _enabled_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count()).select_from(CustomRule).where(CustomRule.enabled.is_(True))
    )
    return int(count or 0)


async def _lock_enabled_cap(session: AsyncSession) -> None:
    """Serialize transactions that may consume one enabled-rule slot."""
    # No row lock can protect a not-yet-created slot. This transaction lock
    # keeps the single count and the subsequent write atomic at READ COMMITTED.
    await session.execute(select(func.pg_advisory_xact_lock(_ENABLED_CAP_LOCK_KEY)))


def _catalog_metrics() -> list[CatalogMetricOut]:
    """One row per catalog entry, in catalog order. Internals stay off the wire."""
    return [
        CatalogMetricOut(
            metric=entry.metric.value,
            subject_kind=entry.subject_kind.value,
            unit=entry.unit,
            is_ratio=entry.is_ratio,
            measurement=entry.measurement,
            note=entry.note,
        )
        for entry in CUSTOM_RULE_METRICS.values()
    ]


def _catalog_subject_kinds() -> list[CatalogSubjectKindOut]:
    """Each kind with the column ``validate_rule_draft`` requires for it.

    The pairing is ``_SUBJECT_COLUMNS``. A second handwritten map would drift
    from the validator, so this reads that one.
    """
    rows: list[CatalogSubjectKindOut] = []
    for kind in CustomRuleSubjectKind:
        columns = _SUBJECT_COLUMNS[kind]
        # One column per kind. A longer pairing must not lose a name on the wire.
        if len(columns) != 1:
            raise RuntimeError(f"{kind.value} pairs with {list(columns)}")
        rows.append(
            CatalogSubjectKindOut(
                subject_kind=kind.value,
                column=columns[0],
                search_family=_KIND_FAMILY[kind.value],
            )
        )
    return rows


def _catalog_source_classes() -> list[str]:
    """Classes a FLEET_CLASS rule may name, in enum order.

    Validation accepts a ``source_class`` exactly when it is a ``SourceClass``.
    """
    return [member.value for member in SourceClass]


def _catalog_operators() -> list[str]:
    """``RULE_OPERATORS``, sorted. A set has no stable wire order."""
    return sorted(RULE_OPERATORS)


def build_custom_rule_catalog(
    *,
    cadences: list[CadenceOut],
    evaluation_enabled: bool,
    alerting_enabled: bool,
    max_enabled: int,
    enabled_count: int,
) -> CustomRuleCatalogOut:
    """The editor's catalog. The caller supplies the count and the switches."""
    return CustomRuleCatalogOut(
        metrics=_catalog_metrics(),
        subject_kinds=_catalog_subject_kinds(),
        cadences=cadences,
        source_classes=_catalog_source_classes(),
        operators=_catalog_operators(),
        evaluation_enabled=evaluation_enabled,
        alerting_mode=alerting_mode(alerting_enabled),
        max_enabled=max_enabled,
        enabled_count=enabled_count,
        preview_hours_min=PREVIEW_HOURS_MIN,
        preview_hours_max=PREVIEW_HOURS_MAX,
    )


async def _pw_value_reason(session: AsyncSession, draft: CustomRuleDraft) -> str | None:
    """A rule pins a pinned property-watch value only (design §G.10.2).

    A value that is not pinned can retire by idleness and has no provable zero, and a rule
    must not depend on the value ceiling. The analyst pins it first. Both the value's own
    flag and the watch's list must say pinned: a flag the collector has not yet cleared is
    not a pin the operator still holds.
    """
    if draft.pw_value_id is None:
        return None
    value = await session.get(PwValue, draft.pw_value_id)
    if value is None:
        return f"property-watch value {draft.pw_value_id} does not exist"
    watch = await session.get(PropertyWatch, value.watch_id)
    held = watch is not None and any(
        split_tuple_key(value.value_key) == tuple(pin) for pin in watch.pinned_values
    )
    if value.pinned and held:
        return None
    return (
        f"property-watch value {draft.pw_value_id} is not pinned; a rule can watch only a "
        "pinned value, so pin it on its property watch first"
    )


async def _subject_reason(session: AsyncSession, draft: CustomRuleDraft) -> str | None:
    """The subject row is missing. A null id is the pairing reason, already listed."""
    if draft.subject_kind is CustomRuleSubjectKind.PW_VALUE:
        return await _pw_value_reason(session, draft)
    if draft.subject_kind in _SOURCE_KINDS:
        if draft.log_source_id is None:
            return None
        if await session.get(LogSource, draft.log_source_id) is None:
            return f"log source {draft.log_source_id} does not exist"
        return None
    if draft.subject_kind is CustomRuleSubjectKind.WAF_ENDPOINT:
        if draft.waf_endpoint_id is None:
            return None
        if await session.get(WafEndpoint, draft.waf_endpoint_id) is None:
            return f"waf endpoint {draft.waf_endpoint_id} does not exist"
        return None
    if draft.subject_kind is CustomRuleSubjectKind.IPS_ENTITY:
        if draft.ips_entity_id is None:
            return None
        if await session.get(IpsEntity, draft.ips_entity_id) is None:
            return f"ips entity {draft.ips_entity_id} does not exist"
    return None


async def _name_taken(
    session: AsyncSession, name: str, current_id: uuid.UUID | None
) -> bool:
    found = await session.scalar(select(CustomRule.id).where(CustomRule.name == name))
    return found is not None and found != current_id


async def _flush_name(session: AsyncSession, name: str) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if _PW_VALUE_FK_CONSTRAINT in str(exc):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "the property-watch value no longer exists (its watch changed or was deleted "
                "while the rule was saved); reload and choose a value again",
            ) from exc
        if _NAME_CONSTRAINT not in str(exc):
            raise
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a custom rule named '{name}' already exists",
        ) from exc


async def _locked(session: AsyncSession, rule_id: uuid.UUID) -> CustomRule:
    rule = await session.scalar(
        select(CustomRule).where(CustomRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "custom rule not found")
    return rule


async def _load_rule(session: AsyncSession, rule_id: uuid.UUID) -> CustomRule:
    rule = await session.get(CustomRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "custom rule not found")
    return rule


async def _active_by_id(
    session: AsyncSession, rules: list[CustomRule]
) -> dict[uuid.UUID, CustomRuleBreach]:
    ids = [rule.active_breach_id for rule in rules if rule.active_breach_id is not None]
    if not ids:
        return {}
    rows = (
        await session.scalars(select(CustomRuleBreach).where(CustomRuleBreach.id.in_(ids)))
    ).all()
    return {row.id: row for row in rows}


async def _check_draft(
    session: AsyncSession,
    draft: CustomRuleDraft,
    settings: Settings,
    *,
    previously_enabled: bool,
) -> None:
    reasons = validate_rule_draft(
        draft,
        enabled_count=await _enabled_count(session),
        previously_enabled=previously_enabled,
        max_enabled=settings.custom_rules_max_enabled,
    )
    existence = await _subject_reason(session, draft)
    if existence is not None:
        reasons.append(existence)
    _reject(reasons)


def _metric_of(rule: CustomRule) -> CustomRuleMetric:
    return CustomRuleMetric(_as_str(rule.metric))


def _kind_of(rule: CustomRule) -> CustomRuleSubjectKind:
    return CustomRuleSubjectKind(_as_str(rule.subject_kind))


@router.get("", response_model=list[CustomRuleOut])
async def list_rules(
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CustomRuleOut]:
    rows = list(
        (
            await session.scalars(
                select(CustomRule)
                .order_by(CustomRule.name.asc(), CustomRule.id.asc())
                .limit(_bound(limit, settings))
                .offset(offset)
            )
        ).all()
    )
    active = await _active_by_id(session, rows)
    rendered: list[CustomRuleOut] = []
    for rule in rows:
        breach = active.get(rule.active_breach_id) if rule.active_breach_id else None
        rendered.append(_rule_out(rule, breach, settings))
    return rendered


@router.post("", response_model=CustomRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: CustomRuleCreate,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> CustomRuleOut:
    _guard(principal)
    draft = _draft_from_create(body)
    if body.enabled:
        await _lock_enabled_cap(session)
    await _check_draft(session, draft, settings, previously_enabled=False)
    if await _name_taken(session, body.name, None):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a custom rule named '{body.name}' already exists",
        )
    now = datetime.now(UTC)
    rule = CustomRule(
        name=body.name,
        description=body.description,
        subject_kind=body.subject_kind,
        log_source_id=body.log_source_id,
        source_class=body.source_class,
        waf_endpoint_id=body.waf_endpoint_id,
        ips_entity_id=body.ips_entity_id,
        pw_value_id=body.pw_value_id,
        metric=body.metric,
        operator=body.operator,
        threshold_value=body.threshold_value,
        sustain_buckets=body.sustain_buckets,
        recovery_buckets=body.recovery_buckets,
        severity=body.severity,
        enabled=body.enabled,
        consecutive_breaching=0,
        consecutive_under=0,
        created_by=principal.subject,
        updated_by=principal.subject,
        created_at=now,
        updated_at=now,
    )
    session.add(rule)
    await _flush_name(session, body.name)
    await record_audit(
        session,
        action="custom_rule.create",
        actor=principal.subject,
        object_type="custom_rule",
        object_id=str(rule.id),
        detail={"before": None, "after": _snapshot(rule)},
    )
    return _rule_out(rule, None, settings)


@router.post("/preview", response_model=PreviewOut)
async def preview_draft(
    body: CustomRulePreviewIn,
    session: SessionDep,
    settings: SettingsDep,
    hours: Annotated[int, Query(ge=PREVIEW_HOURS_MIN, le=PREVIEW_HOURS_MAX)] = 24,
) -> PreviewOut:
    """Replay a draft over stored buckets. Nothing is written and nothing is audited."""
    draft = _draft_from_create(body)
    # A preview does not take an enabled slot. The cap is a write concern.
    reasons = validate_rule_draft(
        draft,
        enabled_count=0,
        previously_enabled=True,
        max_enabled=settings.custom_rules_max_enabled,
    )
    existence = await _subject_reason(session, draft)
    if existence is not None:
        reasons.append(existence)
    _reject(reasons)
    result = await replay(
        session,
        metric=CustomRuleMetric(body.metric),
        operator=body.operator,
        threshold_value=body.threshold_value,
        sustain_buckets=body.sustain_buckets,
        recovery_buckets=body.recovery_buckets,
        severity=_as_str(body.severity),
        subject_kind=body.subject_kind,
        log_source_id=body.log_source_id,
        source_class=body.source_class,
        waf_endpoint_id=body.waf_endpoint_id,
        ips_entity_id=body.ips_entity_id,
        hours=hours,
        interval_seconds=settings.collection_interval_seconds,
        now=datetime.now(UTC),
        name="preview",
        pw_value_id=body.pw_value_id,
        pw_coverage_floor=settings.pw_min_coverage,
    )
    return _preview_out(result)


@router.get("/catalog", response_model=CustomRuleCatalogOut)
async def get_catalog(session: SessionDep, settings: SettingsDep) -> CustomRuleCatalogOut:
    """Metrics, pairing and switches for the rule editor.

    Registered before ``/{rule_id}`` so ``catalog`` is not parsed as a UUID.
    One count statement supplies ``enabled_count``; the rest is in-process.
    """
    cadence_rows = {row.family: row for row in await SearchCadenceService(session, settings).list()}
    families = dict.fromkeys(_KIND_FAMILY[kind.value] for kind in CustomRuleSubjectKind)
    cadences = [
        CadenceOut(
            family=family,
            cadence_seconds=cadence_rows[family.value].cadence_seconds,
            bucket_seconds=bucket_seconds(family, settings),
            settle_seconds=settings.search_cadence_settle_seconds,
        )
        for family in families
    ]
    return build_custom_rule_catalog(
        cadences=cadences,
        evaluation_enabled=settings.custom_rules_enabled,
        alerting_enabled=settings.custom_rules_alerting_enabled,
        max_enabled=settings.custom_rules_max_enabled,
        enabled_count=await _enabled_count(session),
    )


@router.get("/{rule_id}", response_model=CustomRuleOut)
async def get_rule(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> CustomRuleOut:
    rule = await _load_rule(session, rule_id)
    breach = None
    if rule.active_breach_id is not None:
        breach = await session.get(CustomRuleBreach, rule.active_breach_id)
    return _rule_out(rule, breach, settings)


@router.patch("/{rule_id}", response_model=CustomRuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    payload: CustomRuleUpdate,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> CustomRuleOut:
    _guard(principal)
    rule = await _locked(session, rule_id)
    if _utc(payload.expected_updated_at) != _utc(rule.updated_at):
        raise HTTPException(status.HTTP_409_CONFLICT, _conflict(rule))
    nulls = _null_reasons(payload)
    if nulls:
        _reject(nulls)
    draft = _draft_from_patch(rule, payload)
    if draft.enabled and not rule.enabled:
        await _lock_enabled_cap(session)
    await _check_draft(session, draft, settings, previously_enabled=bool(rule.enabled))
    before = _snapshot(rule)
    after = _merged(rule, payload)
    if before == after:
        breach = None
        if rule.active_breach_id is not None:
            breach = await session.get(CustomRuleBreach, rule.active_breach_id)
        return _rule_out(rule, breach, settings)
    new_name = str(after["name"])
    if new_name != rule.name and await _name_taken(session, new_name, rule.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a custom rule named '{new_name}' already exists",
        )
    changed = {key for key in after if after[key] != before[key]}
    _apply(rule, payload)
    rule.updated_by = principal.subject
    rule.updated_at = _bumped(_utc(rule.updated_at))
    await _flush_name(session, rule.name)
    if changed == {"enabled"}:
        action = "custom_rule.enable" if rule.enabled else "custom_rule.disable"
    else:
        action = "custom_rule.update"
    await record_audit(
        session,
        action=action,
        actor=principal.subject,
        object_type="custom_rule",
        object_id=str(rule.id),
        detail={"before": before, "after": _snapshot(rule)},
    )
    breach = None
    if rule.active_breach_id is not None:
        breach = await session.get(CustomRuleBreach, rule.active_breach_id)
    return _rule_out(rule, breach, settings)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> Response:
    """Resolve the open alert as this principal, then delete the rule."""
    _guard(principal)
    rule = await _locked(session, rule_id)
    before = _snapshot(rule)
    rule_key = str(rule.id)
    await resolve_deleted_rule_alert(
        session,
        rule.id,
        actor=principal.subject,
        enqueuer=NotificationDispatcher(session, notifiers={}, policy=default_policy()),
    )
    await session.delete(rule)
    await record_audit(
        session,
        action="custom_rule.delete",
        actor=principal.subject,
        object_type="custom_rule",
        object_id=rule_key,
        detail={"before": before, "after": None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{rule_id}/preview", response_model=PreviewOut)
async def preview_rule(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    hours: Annotated[int, Query(ge=PREVIEW_HOURS_MIN, le=PREVIEW_HOURS_MAX)] = 24,
) -> PreviewOut:
    """Replay the stored rule. A disabled rule still answers the historical question."""
    rule = await _load_rule(session, rule_id)
    result = await replay(
        session,
        metric=_metric_of(rule),
        operator=rule.operator,
        threshold_value=float(rule.threshold_value),
        sustain_buckets=int(rule.sustain_buckets),
        recovery_buckets=int(rule.recovery_buckets),
        severity=_as_str(rule.severity),
        subject_kind=_kind_of(rule),
        log_source_id=rule.log_source_id,
        source_class=rule.source_class,
        waf_endpoint_id=rule.waf_endpoint_id,
        ips_entity_id=rule.ips_entity_id,
        hours=hours,
        interval_seconds=settings.collection_interval_seconds,
        now=datetime.now(UTC),
        name=rule.name,
        pw_value_id=rule.pw_value_id,
        pw_coverage_floor=settings.pw_min_coverage,
    )
    return _preview_out(result)


@router.get("/{rule_id}/breaches", response_model=list[BreachOut])
async def list_breaches(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BreachOut]:
    await _load_rule(session, rule_id)
    rows = (
        await session.scalars(
            select(CustomRuleBreach)
            .where(CustomRuleBreach.rule_id == rule_id)
            .order_by(CustomRuleBreach.opened_at.desc(), CustomRuleBreach.id.asc())
            .limit(_bound(limit, settings))
            .offset(offset)
        )
    ).all()
    return [_breach_out(row) for row in rows]
