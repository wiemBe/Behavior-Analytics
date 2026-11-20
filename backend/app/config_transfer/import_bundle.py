"""Plan fleet merges using only local configuration; the caller owns the transaction."""

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.config_transfer.refs import ResolutionCatalog
from app.config_transfer.report import ImportReport
from app.identity.reference import IdentityFieldMapping, IdentityReferenceError
from app.ips.profile import IpsFieldMapping, IpsFieldReference, IpsProfileError
from app.models.enums import (
    ClassificationRuleKind,
    IdentityProfileValidationStatus,
    IpsProfileValidationStatus,
    SearchFamily,
    SourceClass,
    WafProfileValidationStatus,
    default_search_cadence,
)
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafSourceProfile
from app.schemas.fleet import ClassificationRuleCreate, check_pattern
from app.schemas.ips import IpsProfileIn
from app.schemas.log_source import LogSourceUpdate
from app.schemas.search_cadence import SearchCadenceUpdate
from app.schemas.waf import WafProfileIn
from app.waf.profile import WafFieldMapping, WafProfileError

SOC_DEFAULTS: dict[str, Any] = {
    "criticality": "MEDIUM", "owner": None, "owner_email": None, "monitoring_enabled": True,
    "expected_interval_seconds": None, "business_hours_only": False, "business_hours_start": 8,
    "business_hours_end": 18, "business_days": [1, 2, 3, 4, 5], "timezone_name": "UTC",
    "custom_thresholds": {},
}
#: The two fleet sections and the three profile sections that name one log source.
SOURCE_SECTIONS = ("log_source_settings", "classification_overrides", "waf_source_profiles",
                   "ips_source_profiles", "identity_source_profiles")
#: Profile section -> (table, creation status). A created row is disabled and never validated.
PROFILES: dict[str, tuple[Any, str]] = {
    "waf_source_profiles": (WafSourceProfile, WafProfileValidationStatus.UNVALIDATED),
    "ips_source_profiles": (IpsSourceProfile, IpsProfileValidationStatus.UNVALIDATED),
    "identity_source_profiles": (IdentitySourceProfile,
                                 IdentityProfileValidationStatus.UNVALIDATED),
}
#: WAF logical field (the `WafMappingIn` alias) -> the stored and exported column.
WAF_COLUMNS = {
    "URL": "url_property", "METHOD": "method_property", "VIRTUAL_HOST": "virtual_host_property",
    "ACTION": "action_property", "RESPONSE_CODE": "response_code_property",
    "RULE_ID": "rule_id_property", "USER_AGENT": "user_agent_property",
}
IPS_FIELDS = frozenset({"SOURCE_IP", "DESTINATION_IP", "DESTINATION_PORT", "PROTOCOL",
                        "SIGNATURE", "ACTION", "SEVERITY"})


class RowError(ValueError):
    """A fixed, safe report reason."""


def _shape(row: dict[str, Any], fields: set[str]) -> None:
    if set(row) != fields:
        raise RowError("row fields do not match the section")


def _key(row: dict[str, Any]) -> str:
    ref = row.get("log_source")
    values = ([ref.get("name"), ref.get("type_name")] if isinstance(ref, dict) else
              [row.get("family")] if "family" in row else
              [row.get("scope", "built-in"), row.get("kind"), row.get("pattern")])
    return "|".join(value if isinstance(value, str) else "" for value in values)


def _merge(target: Any, values: dict[str, Any], default: bool) -> str:
    if all(getattr(target, key) == value for key, value in values.items()):
        return "unchanged"
    if not default:
        return "conflict"
    for key, value in values.items():
        setattr(target, key, value)
    return "created"


@dataclass
class _Ctx:
    """What one run loads once: the same constant queries serve every row of every section."""

    session: AsyncSession
    instance: QRadarInstance
    actor: str
    now: datetime
    by_id: dict[str, LogSource]
    classifications: dict[str, LogSourceClassification]
    rules: list[ClassificationRule]
    cadences: dict[Any, SearchCadenceSetting]
    profiles: dict[str, dict[str, Any]]
    reasons: dict[str, str]
    created_profile: str


#: A section's applier: (context, row, resolved local id or None) -> (outcome, reason or None).
Result = tuple[str, str | None]
Applier = Callable[[_Ctx, dict[str, Any], str | None], Result]


def _log_source_settings(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"log_source", *SOC_DEFAULTS})
    values = LogSourceUpdate.model_validate(
        {key: row[key] for key in SOC_DEFAULTS}).model_dump(exclude_unset=True)
    if any(values[key] is None for key in SOC_DEFAULTS
           if key not in {"owner", "owner_email", "expected_interval_seconds"}):
        raise RowError("non-null SOC field is null")
    source = ctx.by_id[str(local_id)]
    return _merge(source, values, all(
        getattr(source, key) == value for key, value in SOC_DEFAULTS.items())), None


def _classification_overrides(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"log_source", "override_class"})
    value = SourceClass(row["override_class"]) if row["override_class"] is not None else None
    classification = ctx.classifications.get(str(local_id))
    if classification is None:
        raise RowError("log source has not been classified")
    if value is None and classification.override_class is not None:
        # The import never clears an operator override; it does not write a
        # null over a set one, and it must not call that "already matches".
        return "conflict", "file has no override; import never clears a local override"
    outcome = "unchanged" if value is None else _merge(
        classification, {"override_class": value}, classification.override_class is None)
    if outcome == "created":
        classification.override_by, classification.override_at = ctx.actor[:255], ctx.now
    return outcome, None


def _classification_rules(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    kind = ClassificationRuleKind(row["kind"])
    if not isinstance(row["pattern"], str):
        raise RowError("classification pattern is not a string")
    check_pattern(kind, row["pattern"])
    if type(row["built_in"]) is not bool or type(row["enabled"]) is not bool:
        raise RowError("classification flags must be booleans")
    if row["built_in"]:
        _shape(row, {"built_in", "kind", "pattern", "enabled"})
        matches = [rule for rule in ctx.rules if rule.built_in and
                   (rule.kind, rule.pattern) == (kind, row["pattern"])]
        if not matches:
            raise RowError("built-in classification rule is not seeded")
        values = {"enabled": row["enabled"]}
    else:
        _shape(row, {"built_in", "scope", "kind", "pattern", "target_class",
                     "priority", "enabled", "description"})
        if row["scope"] not in {"global", "instance"}:
            raise RowError("classification scope is not global or instance")
        scope = ctx.instance.id if row["scope"] == "instance" else None
        values = ClassificationRuleCreate.model_validate(
            {key: value for key, value in row.items()
             if key not in {"built_in", "scope"}} | {"instance_id": scope}
        ).model_dump()
        matches = [rule for rule in ctx.rules if
                   (rule.instance_id, rule.kind, rule.pattern) == (scope, kind, row["pattern"])]
    if len(matches) > 1:
        raise RowError("classification natural key is ambiguous")
    if not matches:
        rule = ClassificationRule(**values, built_in=False, created_by=ctx.actor[:255])
        ctx.session.add(rule)
        ctx.rules.append(rule)
        return "created", None
    if matches[0].built_in != row["built_in"]:
        return "conflict", None
    return _merge(matches[0], values, row["built_in"] and matches[0].enabled is True), None


def _search_cadence(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"family", "cadence_seconds"})
    family = SearchFamily(row["family"])
    values = SearchCadenceUpdate.model_validate(
        {"cadence_seconds": row["cadence_seconds"]}).model_dump()
    cadence = ctx.cadences.get(family)
    if cadence is None:
        raise RowError("cadence family is not seeded")
    outcome = _merge(cadence, values, cadence.updated_by is None and
                     cadence.cadence_seconds == default_search_cadence(family))
    if outcome == "created":
        cadence.updated_by, cadence.updated_at = ctx.actor[:255], ctx.now
    return outcome, None


_EMPTY_MAP: dict[str, Any] = {"action_value_map": {}}


def _profile_row(row: dict[str, Any], fields: set[str]) -> None:
    """The file's exact fields, a text-or-null label and a boolean ``enabled_on_source``."""
    _shape(row, {"log_source", "enabled_on_source", "product_label", *fields})
    if type(row["enabled_on_source"]) is not bool:
        raise RowError("enabled_on_source must be a boolean")


def _waf_values(row: dict[str, Any]) -> dict[str, Any]:
    """Adapter, then the schema: the `*_property` columns nest as `WafMappingIn` aliases."""
    row = _EMPTY_MAP | row
    _profile_row(row, {*WAF_COLUMNS.values(), "action_value_map"})
    action_map = row["action_value_map"] or {"BLOCKED": [], "ALLOWED": []}
    payload = WafProfileIn.model_validate({
        "product_label": row["product_label"], "action_value_map": action_map,
        "mapping": {alias: row[column] for alias, column in WAF_COLUMNS.items()}})
    mapping = WafFieldMapping(
        **{column.removesuffix("_property"): getattr(payload.mapping, column.removesuffix(
            "_property")) for column in WAF_COLUMNS.values()},
        blocked_actions=tuple(payload.action_value_map.blocked),
        allowed_actions=tuple(payload.action_value_map.allowed))
    return {"product_label": payload.product_label, "action_value_map": mapping.action_value_map(),
            **{column: getattr(mapping, column.removesuffix("_property"))
               for column in WAF_COLUMNS.values()}}


def _ips_values(row: dict[str, Any]) -> dict[str, Any]:
    """A missing or empty action map expands to two empty lists so the schema parses it."""
    row = _EMPTY_MAP | row
    _profile_row(row, {"preset", "mapping", "action_value_map"})
    mapping = row["mapping"]
    if not isinstance(mapping, dict) or not set(mapping) <= IPS_FIELDS:
        raise RowError("IPS mapping keys are not logical fields")
    payload = IpsProfileIn.model_validate({
        "product_label": row["product_label"], "preset": row["preset"], "mapping": mapping,
        "action_value_map": row["action_value_map"] or {"BLOCKED": [], "ALLOWED": []}})
    refs = {field: IpsFieldReference.from_dict(value)
            for field, value in mapping.items() if value is not None}
    stored = IpsFieldMapping(
        source_ip=refs["SOURCE_IP"], destination_ip=refs["DESTINATION_IP"],
        destination_port=refs["DESTINATION_PORT"], signature=refs["SIGNATURE"],
        protocol=refs.get("PROTOCOL"), action=refs.get("ACTION"), severity=refs.get("SEVERITY"),
        blocked_actions=tuple(payload.action_value_map.blocked),
        allowed_actions=tuple(payload.action_value_map.allowed))
    return {"product_label": payload.product_label, "preset": payload.preset,
            "mapping": stored.mapping_dict(), "action_value_map": stored.action_value_map()}


def _identity_values(row: dict[str, Any]) -> dict[str, Any]:
    _profile_row(row, {"event_id_property", "username_property", "logon_type_property",
                       "enabled_event_ids"})
    label = row["product_label"]
    if label is not None and not (isinstance(label, str) and len(label) <= 128):
        raise RowError("product label is not text of at most 128 characters")
    if not isinstance(row["enabled_event_ids"], list):
        raise RowError("enabled_event_ids must be a list")
    mapping = IdentityFieldMapping.from_parts(
        row["event_id_property"], row["username_property"], row["logon_type_property"],
        row["enabled_event_ids"])
    return {"product_label": label, "event_id_property": mapping.event_id_property,
            "username_property": mapping.username_property,
            "logon_type_property": mapping.logon_type_property,
            "enabled_event_ids": list(mapping.enabled_event_ids)}


def _profile(section: str, build: Callable[[dict[str, Any]], dict[str, Any]]) -> Applier:
    """Merge one profile by its resolved log source: insert disabled, else equal or conflict.

    Design "Import semantics": a created profile is ``enabled=false`` and ``UNVALIDATED``, and
    `enabled`/validation are not compared, so a later enable is no conflict; any other
    difference from a stored profile conflicts and writes nothing.
    """
    model, status = PROFILES[section]

    def apply(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
        values = build(row)
        existing = ctx.profiles[section].get(str(local_id))
        if existing is None:
            profile = model(
                instance_id=ctx.instance.id, log_source_id=uuid.UUID(str(local_id)),
                enabled=False, profile_version=1, validation_status=status, validation_report={},
                updated_by=ctx.actor[:255], **values)
            ctx.session.add(profile)
            ctx.profiles[section][str(local_id)] = profile
            return "created", ctx.created_profile
        # The model default lists event ids in catalog order; every writer stores them sorted.
        stored = {key: getattr(existing, key) for key in values}
        if "enabled_event_ids" in stored:
            stored["enabled_event_ids"] = sorted(stored["enabled_event_ids"])
        if stored == values:
            return "unchanged", None
        return "conflict", "a profile for this log source already has a different mapping"

    return apply


APPLIERS: dict[str, Applier] = {
    "log_source_settings": _log_source_settings,
    "classification_overrides": _classification_overrides,
    "classification_rules": _classification_rules,
    "search_cadence": _search_cadence,
    "waf_source_profiles": _profile("waf_source_profiles", _waf_values),
    "ips_source_profiles": _profile("ips_source_profiles", _ips_values),
    "identity_source_profiles": _profile("identity_source_profiles", _identity_values),
}


def _failure(exc: Exception) -> str:
    """A fixed reason; a validator's own text can carry the input value, so only codes pass."""
    if type(exc) is RowError or isinstance(exc, IdentityReferenceError):
        return str(exc)
    if isinstance(exc, WafProfileError | IpsProfileError) and exc.ref is not None:
        return exc.ref.code
    return "row validation failed"


async def plan(session: AsyncSession, document: dict[str, Any], instance: QRadarInstance,
               report: ImportReport, actor: str, mode: str) -> None:
    """Make the same writes for dry-run and apply; no provider or secret column is read."""
    sources = list(await session.scalars(select(LogSource).where(
        LogSource.instance_id == instance.id).options(load_only(
            LogSource.id, LogSource.name, LogSource.type_name,
            *(getattr(LogSource, key) for key in SOC_DEFAULTS)))))
    catalog = ResolutionCatalog(log_sources=tuple(
        (str(row.id), row.name, row.type_name) for row in sources))
    # One query, no bind parameter per source: asyncpg caps a statement at 32,767 parameters.
    # Joining the instance's own sources yields exactly the rows the id list used to.
    classifications = {str(row.log_source_id): row for row in await session.scalars(
        select(LogSourceClassification).join(
            LogSource, LogSourceClassification.log_source_id == LogSource.id
        ).where(LogSource.instance_id == instance.id))}
    profiles: dict[str, dict[str, Any]] = {}
    for name, (model, _) in PROFILES.items():
        profile: Any
        profiles[name] = {}
        for profile in await session.scalars(select(model).where(model.instance_id == instance.id)):
            profiles[name][str(profile.log_source_id)] = profile
    ctx = _Ctx(
        session, instance, actor, datetime.now(UTC), {str(row.id): row for row in sources},
        classifications,
        list(await session.scalars(select(ClassificationRule).where(or_(
            ClassificationRule.instance_id == instance.id,
            ClassificationRule.instance_id.is_(None))))),
        {row.family: row for row in await session.scalars(select(SearchCadenceSetting))},
        profiles,
        {"created": "applied configuration" if mode == "apply" else "would apply configuration",
         "unchanged": "configuration already matches",
         "conflict": "stored configuration differs from the file and is not default"},
        "profile inserted disabled and UNVALIDATED" if mode == "apply" else
        "no profile for this log source; would insert disabled and UNVALIDATED")
    for section, rows in document["sections"].items():
        keys = Counter(_key(row) for row in rows)
        for row in rows:
            key, local_id = _key(row), None
            if section not in APPLIERS:
                report.add(section, "unresolved", key, "not applied in this release")
                continue
            try:
                if keys[key] > 1:
                    raise RowError("duplicate natural key in the file")
                if row.get("warnings"):
                    raise RowError("export reported an unresolved reference")
                if section in SOURCE_SECTIONS:
                    match = catalog.match_log_source(row.get("log_source"))
                    if match.exact_id is None:
                        raise RowError(match.reason)
                    local_id = str(match.exact_id)
                    _shape(row["log_source"], {"name", "type_name"})
                outcome, reason = APPLIERS[section](ctx, row, local_id)
                report.add(section, outcome, key, reason or ctx.reasons[outcome], local_id)
            except (ValueError, TypeError, KeyError) as exc:
                # Never stringify validators: their messages can include input values.
                report.add(section, "unresolved", key, _failure(exc), local_id)
    await session.flush()
