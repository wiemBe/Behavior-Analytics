"""Plan configuration merges using only local data; the caller owns the transaction."""

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.alerts.notifiers.destination_sender import encode_headers
from app.alerts.notifiers.url_guard import URLGuardError, guard_url
from app.config_transfer.document import TIMESTAMP_FORMAT
from app.config_transfer.refs import ResolutionCatalog
from app.config_transfer.report import ImportReport
from app.identity.reference import IdentityFieldMapping, IdentityReferenceError
from app.ips.profile import IpsFieldMapping, IpsFieldReference, IpsProfileError
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression
from app.models.email import (
    EMAIL_ALERT_TYPES,
    EMAIL_RECIPIENT_ALERT_TYPES,
    EmailRecipient,
    EmailTemplate,
)
from app.models.enums import (
    AnomalyFamily,
    AnomalyType,
    ClassificationRuleKind,
    IdentityAnomalyType,
    IdentityProfileValidationStatus,
    IpsAnomalyType,
    IpsProfileValidationStatus,
    OutagePolicyScope,
    PwAnomalyType,
    RuleAnomalyType,
    SearchFamily,
    SourceClass,
    WafAnomalyType,
    WafProfileValidationStatus,
    default_search_cadence,
)
from app.models.fleet import ClassificationRule, LogSourceClassification, LogSourceGroup
from app.models.identity import LdapConfig
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from app.models.outage import OutageAlertPolicy
from app.models.property_watch import PropertyWatch
from app.models.rule import AnalyticsRule
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafSourceProfile
from app.schemas.alert_policy import AlertPolicyUpsert
from app.schemas.email import RecipientIn
from app.schemas.fleet import ClassificationRuleCreate, check_pattern
from app.schemas.ips import IpsProfileIn
from app.schemas.log_source import LogSourceUpdate
from app.schemas.notification_destination import DestinationCreate
from app.schemas.notification_rule import RuleCreate
from app.schemas.outage import OutagePolicyUpsert
from app.schemas.search_cadence import SearchCadenceUpdate
from app.schemas.suppression import SuppressionCreate
from app.schemas.waf import WafProfileIn
from app.services.email_delivery import valid_mailbox
from app.services.email_templates import sanitize_email_html, unknown_placeholders
from app.services.ldap_config import validate_directory_coded
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
#: The detector set of each anomaly family, the same gate the create route applies.
DETECTORS: dict[AnomalyFamily, frozenset[str]] = {
    AnomalyFamily.LOG_SOURCE: frozenset(item.value for item in AnomalyType),
    AnomalyFamily.WAF: frozenset(item.value for item in WafAnomalyType),
    AnomalyFamily.IPS: frozenset(item.value for item in IpsAnomalyType),
    AnomalyFamily.PROPERTY_WATCH: frozenset(item.value for item in PwAnomalyType),
    AnomalyFamily.RULE: frozenset(item.value for item in RuleAnomalyType),
    AnomalyFamily.IDENTITY: frozenset(item.value for item in IdentityAnomalyType),
}
#: The seven comparable LDAP fields: `version` is import state and never travels.
LDAP_FIELDS = frozenset({"enabled", "urls", "start_tls", "ca_pem", "bind_template",
                         "timeout_seconds", "user_search_base"})


class RowError(ValueError):
    """A fixed, safe report reason."""


def _shape(row: dict[str, Any], fields: set[str]) -> None:
    if set(row) - {"warnings"} != fields:
        raise RowError("row fields do not match the section")


def _ref_name(value: Any) -> Any:
    """The name inside a reference object, for a report key only (never persisted)."""
    return value.get("name") if isinstance(value, dict) else value


def _key(section: str, row: dict[str, Any]) -> str:
    if section == "alert_policies":
        scope = row.get("log_source") or row.get("watch") or {"name": row.get("source_class")}
        values = [row.get("family"), row.get("scope_kind"), _ref_name(scope),
                  row.get("detector_type")]
    elif section == "outage_alert_policies":
        values = [row.get("scope_kind"),
                  _ref_name(row.get("log_source") or {"name": row.get("source_class")})]
    elif section == "alert_suppressions":
        subject = row.get("log_source") or row.get("watch") or row.get("rule") or {}
        values = [row.get("family"), _ref_name(subject), row.get("detector_type")]
    elif section in ("notification_destinations", "notification_rules"):
        values = [row.get("name")]
    elif section == "email_recipients":
        values = [row.get("address")]
    elif section == "email_templates":
        values = [row.get("alert_type") or "default"]
    elif section == "ldap_config":
        values = ["1"]
    else:
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
    catalog: ResolutionCatalog
    existing: dict[str, dict[Any, Any]]
    ldap: LdapConfig | None
    #: Name -> UUID of every destination this plan matched or created, for the rules that follow.
    destination_ids: dict[str, uuid.UUID]


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


def _only(match: Any) -> str | int:
    if match.exact_id is None:  # the fixed reason names no value
        raise RowError(match.reason)
    return match.exact_id


def _stamp(value: Any) -> datetime:
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except (TypeError, ValueError) as exc:
        raise RowError("timestamp is not a UTC string") from exc


def _alert_policies(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"scope", "scope_kind", "family", "log_source", "source_class", "watch",
                 "detector_type", "mode"})
    if row["watch"] is not None:
        scope_value = _only(ctx.catalog.match_watch(row["watch"]))
    elif row["log_source"] is not None:
        scope_value = _only(ctx.catalog.match_log_source(row["log_source"]))
    elif row["source_class"] is not None:
        scope_value = SourceClass(row["source_class"]).value
    else:
        raise RowError("alert policy has no scope value")
    if row["scope"] not in ("global", "instance"):
        raise RowError("scope is not global or instance")
    scope = ctx.instance.id if row["scope"] == "instance" else None
    values = AlertPolicyUpsert.model_validate({
        "instance_id": scope, "scope_kind": row["scope_kind"], "scope_value": str(scope_value),
        "family": row["family"], "detector_type": row["detector_type"], "mode": row["mode"],
    }).model_dump(mode="json")
    key = (str(scope) if scope else "", values["scope_kind"], str(scope_value),
           values["family"], values["detector_type"])
    existing = ctx.existing["alert_policies"].get(key)
    if existing is None:
        ctx.session.add(AlertPolicy(**values, updated_by=ctx.actor[:255]))
        return "created", None
    return (("unchanged" if existing.mode == values["mode"] else "conflict"), None)


def _outage_alert_policies(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"scope", "scope_kind", "source_class", "log_source", "mode",
                 "silence_grace_buckets"})
    if row["scope_kind"] not in tuple(item.value for item in OutagePolicyScope):
        raise RowError("outage scope_kind is not CLASS or SOURCE")
    if ((row["scope_kind"] == "SOURCE") != (row["log_source"] is not None) or
            (row["scope_kind"] == "CLASS") != (row["source_class"] is not None)):
        raise RowError("outage scope does not match scope_kind")
    if row["log_source"] is not None:
        scope_value = _only(ctx.catalog.match_log_source(row["log_source"]))
    elif row["source_class"] is not None:
        scope_value = SourceClass(row["source_class"]).value
    else:
        raise RowError("outage policy has no scope value")
    if row["scope"] not in ("global", "instance"):
        raise RowError("scope is not global or instance")
    scope = ctx.instance.id if row["scope"] == "instance" else None
    values = OutagePolicyUpsert.model_validate({
        "instance_id": scope, "mode": row["mode"],
        "silence_grace_buckets": row["silence_grace_buckets"],
    }).model_dump(mode="json")
    key = (str(scope) if scope else "", row["scope_kind"], str(scope_value))
    payload = (values["mode"], values["silence_grace_buckets"])
    existing = ctx.existing["outage_alert_policies"].get(key)
    if existing is None:
        ctx.session.add(OutageAlertPolicy(
            scope_kind=row["scope_kind"], scope_value=str(scope_value),
            **values, updated_by=ctx.actor[:255]))
        return "created", None
    return (("unchanged" if (existing.mode, existing.silence_grace_buckets) == payload
             else "conflict"), None)


def _alert_suppressions(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"family", "log_source", "watch", "rule", "detector_type", "kind", "reason",
                 "owner", "starts_at", "ends_at", "reference"})
    family = AnomalyFamily(row["family"])
    if row["log_source"] is not None:
        subject_id = _only(ctx.catalog.match_log_source(row["log_source"]))
    elif row["watch"] is not None:
        subject_id = _only(ctx.catalog.match_watch(row["watch"]))
    elif row["rule"] is not None:
        subject_id = _only(ctx.catalog.match_analytics_rule(row["rule"]))
    else:
        raise RowError("suppression has no subject")
    detector = row["detector_type"]
    if detector is not None and detector not in DETECTORS[family]:
        raise RowError("unknown detector for suppression family")
    check = SuppressionCreate.model_validate({  # only its days window is discarded
        "days": 1, "reason": row["reason"], "owner": row["owner"],
        "kind": row["kind"], "reference": row["reference"]})
    kind, reason, owner, reference = check.kind, check.reason, check.owner, check.reference
    starts, ends = _stamp(row["starts_at"]), _stamp(row["ends_at"])
    if ends <= starts or ends > starts + timedelta(days=30):
        raise RowError("suppression window is not within 30 days")
    if ends <= ctx.now:
        raise RowError("window expired before import")
    key = (family.value, str(subject_id), detector)
    value = (kind.value, reason, owner, starts, ends,
             reference if isinstance(reference, str) else None)
    existing = ctx.existing["alert_suppressions"].get(key)
    if existing is None:
        ctx.session.add(AlertSuppression(
            family=family.value, subject_id=uuid.UUID(str(subject_id)), detector_type=detector,
            kind=kind.value, reason=reason, owner=owner, starts_at=starts, ends_at=ends,
            reference=value[5], created_by=ctx.actor[:255]))
        return "created", None
    stored = (existing.kind, existing.reason, existing.owner, existing.starts_at,
              existing.ends_at, existing.reference)
    return (("unchanged" if stored == value else "conflict"), None)


def _notification_destinations(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"name", "enabled_on_source", "url", "allow_http", "allow_private",
                 "secrets_omitted"})
    payload = DestinationCreate.model_validate({
        "name": row["name"], "url": row["url"], "allow_http": row["allow_http"],
        "allow_private": row["allow_private"], "enabled": False,
    }).model_dump(mode="json")
    existing = ctx.existing["notification_destinations"].get(payload["name"])
    if existing is None:
        # guard_url consults the resolver for a hostname; only a row that will be
        # stored needs that check. An equal or conflicting row is not written.
        try:
            guard_url(payload["url"], allow_http=payload["allow_http"],
                      allow_private=payload["allow_private"])
        except URLGuardError as exc:
            raise RowError("destination URL failed the egress guard") from exc
        destination = NotificationDestination(
            id=uuid.uuid4(), name=payload["name"], url=payload["url"], enabled=False,
            allow_http=payload["allow_http"], allow_private=payload["allow_private"],
            hmac_secret=None, headers=encode_headers({}), created_by=ctx.actor[:255],
            updated_by=ctx.actor[:255])
        ctx.session.add(destination)
        ctx.destination_ids[payload["name"]] = destination.id
        return "created", None
    ctx.destination_ids[payload["name"]] = existing.id
    # The URL is configuration (export writes it); hmac_secret and headers are not read.
    values = (payload["allow_http"], payload["allow_private"], payload["url"])
    stored = (existing.allow_http, existing.allow_private, existing.url)
    return (("unchanged" if stored == values else "conflict"), None)


def _notification_rules(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"name", "enabled", "priority", "min_severity", "families", "owners",
                 "source_classes", "groups", "window_start_minute", "window_end_minute",
                 "destinations", "stop_default"})
    destinations: list[uuid.UUID] = []
    for ref in row["destinations"]:
        name = ref.get("name") if isinstance(ref, dict) else None
        if name not in ctx.destination_ids:
            raise RowError("notification rule destination does not resolve")
        destinations.append(ctx.destination_ids[name])
    groups: list[int] = []
    for ref in row["groups"]:
        group_id = _only(ctx.catalog.match_log_source_group(ref))
        if not isinstance(group_id, int):
            raise RowError("notification rule group does not resolve")
        groups.append(group_id)
    payload = RuleCreate.model_validate({
        "name": row["name"], "enabled": row["enabled"], "priority": row["priority"],
        "min_severity": row["min_severity"], "families": row["families"],
        "owners": row["owners"], "source_classes": row["source_classes"],
        "group_ids": groups, "window_start_minute": row["window_start_minute"],
        "window_end_minute": row["window_end_minute"],
        "destination_ids": destinations, "stop_default": row["stop_default"],
    }).model_dump(mode="json")
    compared = {key: payload[key] for key in (
        "enabled", "priority", "min_severity", "families", "owners", "source_classes",
        "group_ids", "window_start_minute", "window_end_minute", "destination_ids",
        "stop_default")}
    existing = ctx.existing["notification_rules"].get(payload["name"])
    if existing is None:
        ctx.session.add(NotificationRule(**payload, created_by=ctx.actor[:255],
                                         updated_by=ctx.actor[:255]))
        return "created", None
    stored = {key: getattr(existing, key) for key in compared}
    stored["destination_ids"] = [str(item) for item in stored["destination_ids"]]
    return (("unchanged" if stored == compared else "conflict"), None)


def _email_templates(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"alert_type", "subject", "html_body", "text_body", "enabled"})
    alert_type = row["alert_type"]
    if alert_type is not None and alert_type not in EMAIL_ALERT_TYPES:
        raise RowError("email alert_type is not a known template type")
    subject = " ".join(row["subject"].split()) if isinstance(row["subject"], str) else ""
    text = row["text_body"] or None
    if unknown_placeholders(subject, row["html_body"] or "", text or ""):
        raise RowError("email template has an unknown placeholder")
    html = sanitize_email_html(row["html_body"] or "")
    if not html.strip():
        raise RowError("email HTML body is empty after sanitizing")
    if not subject:
        raise RowError("email subject is empty")
    value = (subject, html, text, bool(row["enabled"]))
    existing = ctx.existing["email_templates"].get(alert_type)
    if existing is None:
        ctx.session.add(EmailTemplate(
            alert_type=alert_type, subject=subject, html_body=html, text_body=text,
            enabled=bool(row["enabled"]), updated_by=ctx.actor[:255]))
        return "created", None
    stored = (existing.subject, existing.html_body, existing.text_body, existing.enabled)
    return (("unchanged" if stored == value else "conflict"), None)


def _email_recipients(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, {"address", "min_severity", "alert_types", "enabled"})
    payload = RecipientIn.model_validate({
        "address": row["address"], "min_severity": row["min_severity"],
        "alert_types": row["alert_types"], "enabled": row["enabled"],
    }).model_dump(mode="json")
    address = payload["address"].strip().lower()
    if not valid_mailbox(address):
        raise RowError("email recipient address is not a mailbox")
    types = payload["alert_types"]
    if types is not None:
        if any(item not in EMAIL_RECIPIENT_ALERT_TYPES for item in types):
            raise RowError("email recipient alert_type is not known")
        types = sorted(set(types))
    value = (address, payload["min_severity"], types, payload["enabled"])
    existing = ctx.existing["email_recipients"].get(address)
    if existing is None:
        ctx.session.add(EmailRecipient(
            address=address, min_severity=payload["min_severity"], alert_types=types,
            enabled=payload["enabled"], updated_by=ctx.actor[:255]))
        return "created", None
    stored_types = sorted(existing.alert_types) if existing.alert_types else None
    stored = (existing.address, existing.min_severity, stored_types, existing.enabled)
    return (("unchanged" if stored == value else "conflict"), None)


def _ldap_config(ctx: _Ctx, row: dict[str, Any], local_id: str | None) -> Result:
    _shape(row, set(LDAP_FIELDS))
    spec, reasons = validate_directory_coded(**{key: row[key] for key in LDAP_FIELDS})
    if spec is None:
        raise RowError(reasons[0].code if reasons else "LDAP directory configuration is invalid")
    if ctx.ldap is None:
        raise RowError("LDAP singleton is not seeded")
    values = {key: (list(spec.urls) if key == "urls" else getattr(spec, key))
              for key in LDAP_FIELDS}
    stored = {key: getattr(ctx.ldap, key) for key in LDAP_FIELDS}
    if stored == values:
        return "unchanged", None
    seed = {"enabled": False, "urls": [], "start_tls": False, "ca_pem": None,
            "bind_template": "{username}", "timeout_seconds": 5, "user_search_base": None}
    if ctx.ldap.updated_by is None and stored == seed:
        for key, value in values.items():
            setattr(ctx.ldap, key, value)
        ctx.ldap.version += 1
        ctx.ldap.updated_by, ctx.ldap.updated_at = ctx.actor[:255], ctx.now
        return "created", None
    return "conflict", None


APPLIERS: dict[str, Applier] = {
    "log_source_settings": _log_source_settings,
    "classification_overrides": _classification_overrides,
    "classification_rules": _classification_rules,
    "search_cadence": _search_cadence,
    "waf_source_profiles": _profile("waf_source_profiles", _waf_values),
    "ips_source_profiles": _profile("ips_source_profiles", _ips_values),
    "identity_source_profiles": _profile("identity_source_profiles", _identity_values),
    "alert_policies": _alert_policies,
    "outage_alert_policies": _outage_alert_policies,
    "alert_suppressions": _alert_suppressions,
    "notification_destinations": _notification_destinations,
    "notification_rules": _notification_rules,
    "email_templates": _email_templates,
    "email_recipients": _email_recipients,
    "ldap_config": _ldap_config,
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
    watches = list(await session.scalars(select(PropertyWatch).where(
        PropertyWatch.instance_id == instance.id)))
    groups = list(await session.scalars(select(LogSourceGroup).where(
        LogSourceGroup.instance_id == instance.id)))
    analytics_rules = list(await session.scalars(select(AnalyticsRule).where(
        AnalyticsRule.instance_id == instance.id)))
    # The URL is configuration (export writes it in plaintext) and is compared on merge.
    # The HMAC secret and headers stay EncryptedString and are never read (AGENTS §2 rule 5).
    destinations = list(await session.scalars(select(NotificationDestination).options(
        load_only(NotificationDestination.id, NotificationDestination.name,
                  NotificationDestination.allow_http, NotificationDestination.allow_private,
                  NotificationDestination.url))))
    catalog = ResolutionCatalog(log_sources=tuple(
        (str(row.id), row.name, row.type_name) for row in sources),
        log_source_groups=tuple((row.qradar_id, row.name, row.parent_qradar_id) for row in groups),
        analytics_rules=tuple((str(row.id), row.name, row.rule_type, row.is_building_block)
                              for row in analytics_rules),
        watches=tuple((str(row.id), row.name) for row in watches),
        destinations=tuple((str(row.id), row.name) for row in destinations),
    )
    def scope_key(value: uuid.UUID | None) -> str:
        return str(value) if value is not None else ""

    async def load(model: Any, key: Callable[[Any], Any], *where: Any) -> dict[Any, Any]:
        return {key(row): row for row in await session.scalars(select(model).where(*where))}  # type: ignore[var-annotated]

    policy_scope = or_(AlertPolicy.instance_id == instance.id, AlertPolicy.instance_id.is_(None))
    outage_scope = or_(OutageAlertPolicy.instance_id == instance.id,
                       OutageAlertPolicy.instance_id.is_(None))
    existing: dict[str, dict[Any, Any]] = {
        "alert_policies": await load(AlertPolicy, lambda r: (scope_key(r.instance_id),
            r.scope_kind, r.scope_value, r.family, r.detector_type), policy_scope),
        "outage_alert_policies": await load(OutageAlertPolicy,
            lambda r: (scope_key(r.instance_id), r.scope_kind, r.scope_value), outage_scope),
        "alert_suppressions": await load(AlertSuppression,
            lambda r: (r.family, str(r.subject_id), r.detector_type),
            AlertSuppression.ended_at.is_(None)),
        "notification_destinations": {row.name: row for row in destinations},
        "notification_rules": await load(NotificationRule, lambda r: r.name),
        "email_templates": await load(EmailTemplate, lambda r: r.alert_type),
        "email_recipients": await load(EmailRecipient, lambda r: r.address),
    }
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
        "no profile for this log source; would insert disabled and UNVALIDATED",
        catalog, existing, await session.get(LdapConfig, 1), {})
    for section, rows in document["sections"].items():
        keys = Counter(_key(section, row) for row in rows)
        for row in rows:
            key, local_id = _key(section, row), None
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
