"""Read the config bundle's sections from this product's database (CFG-1, D-78).

Slice 1c: a local ``SELECT`` per section, no provider call and no secret column; later slices
append the rest. Rows follow the file's natural key with NULLs first, so two exports of one
unchanged database differ only in ``exported_at``.

Slice 1c2: the three profile sections carry the mapping, ``enabled_on_source`` and nothing from
the validation state -- an imported row is never ``VALID`` and is never enabled (AGENTS §2 rule 8).

Slice 1d: policies, open suppressions, webhook destinations, notification rules and mail. Every
reference is written as a local name object, and a destination's HMAC secret and encrypted header
block are dropped, leaving the fixed ``secrets_omitted`` list (AGENTS §2 rule 5).

Slice 1d2: the two sections 1d left out -- outage alert policies and e-mail recipients -- and the
unresolved-reference rule. A ref that no longer resolves on this console keeps the shape the row
spells (a null ref, or a null-named object) and the row gains a ``warnings`` entry, so a dangling
id is never written silently; the entry names the field, never the id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import build_document, format_timestamp
from app.core.version import read_version
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression
from app.models.email import EmailRecipient, EmailTemplate
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

#: The SOC-owned log-source columns. Mirrored QRadar columns, maintenance and health stay out.
_SOC_FIELDS = (
    "criticality", "owner", "owner_email", "monitoring_enabled", "expected_interval_seconds",
    "business_hours_only", "business_hours_start", "business_hours_end", "business_days",
    "timezone_name", "custom_thresholds",
)

#: An operator rule's file fields, in the design's order; ``built_in`` and ``scope`` lead.
_OPERATOR_RULE_FIELDS = ("kind", "pattern", "target_class", "priority", "enabled", "description")

#: A profile's mapping columns, in the design's order. Validation and ``enabled`` stay out.
_WAF_PROFILE_FIELDS = (
    "product_label", "url_property", "method_property", "virtual_host_property",
    "action_property", "response_code_property", "rule_id_property", "user_agent_property",
    "action_value_map",
)
_IPS_PROFILE_FIELDS = ("product_label", "preset", "mapping", "action_value_map")
_IDENTITY_PROFILE_FIELDS = (
    "product_label", "event_id_property", "username_property", "logon_type_property",
    "enabled_event_ids",
)

#: A log source's natural key is not unique, so the tie-breaker keeps two exports identical.
#: It is a sort key only; neither value enters the file, and the NULL ordering is unchanged.
_LOG_SOURCE_ORDER = (
    LogSource.name, LogSource.type_name.nullsfirst(), LogSource.qradar_id, LogSource.id,
)


def _log_source_ref(row: LogSource) -> dict[str, Any]:
    """A log source is referenced by name and type, never by a QRadar id or a UUID."""
    return {"name": row.name, "type_name": row.type_name}


def _warn(row: dict[str, Any], kind: str) -> None:
    """Record an unresolved reference on its row, so a null name is never silent.

    The design's unresolved outcomes belong to import; it has no export-time rule for a ref
    that no longer resolves (a deleted watch, rule or log source). The row is therefore kept
    with the ref as the row spells it, and this fixed English entry is appended, naming the
    field rather than the dangling id.
    """
    row.setdefault("warnings", []).append(f"{kind} ref does not resolve")


async def _log_source_settings(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(LogSource).where(LogSource.instance_id == instance.id).order_by(
            *_LOG_SOURCE_ORDER
        )
    )
    return [
        {"log_source": _log_source_ref(row), **{name: getattr(row, name) for name in _SOC_FIELDS}}
        for row in rows.all()
    ]


async def _classification_overrides(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    """Only a set override: a null one is classifier state, and import never clears one."""
    pairs = await session.execute(
        select(LogSourceClassification.override_class, LogSource)
        .join(LogSource, LogSource.id == LogSourceClassification.log_source_id)
        .where(LogSource.instance_id == instance.id,
               LogSourceClassification.override_class.is_not(None))
        .order_by(*_LOG_SOURCE_ORDER)
    )
    return [
        {"log_source": _log_source_ref(source), "override_class": override}
        for override, source in pairs.all()
    ]


async def _classification_rules(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    """Operator rows in full; a built-in one only when its seed default ``enabled`` changed.

    ``ensure_seed_rules`` owns the other columns of a built-in row.
    """
    scope = or_(
        ClassificationRule.instance_id == instance.id, ClassificationRule.instance_id.is_(None)
    )
    rules = await session.scalars(select(ClassificationRule).where(scope))
    rows: list[dict[str, Any]] = []
    for rule in rules.all():
        if not rule.built_in:
            rows.append({"built_in": False,
                         "scope": "global" if rule.instance_id is None else "instance",
                         **{name: getattr(rule, name) for name in _OPERATOR_RULE_FIELDS}})
        elif not rule.enabled:
            rows.append({"built_in": True, "kind": rule.kind, "pattern": rule.pattern,
                         "enabled": False})
    rows.sort(key=lambda row: (row["kind"], row["pattern"], row.get("scope", ""), row["built_in"]))
    return rows


async def _search_cadence(session: AsyncSession) -> list[dict[str, Any]]:
    """The operator's cadence only: the last-run cursors and ``updated_by`` are runtime state."""
    rows = await session.scalars(select(SearchCadenceSetting).order_by(SearchCadenceSetting.family))
    return [{"family": row.family, "cadence_seconds": row.cadence_seconds} for row in rows.all()]


async def _waf_source_profiles(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    """The property mapping per WAF source: no validation column, and ``enabled`` is the source's.

    The order is the log source's natural key, the same one import resolves the row by.
    """
    pairs = await session.execute(
        select(WafSourceProfile, LogSource)
        .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
        .where(WafSourceProfile.instance_id == instance.id)
        .order_by(*_LOG_SOURCE_ORDER)
    )
    return [
        {"log_source": _log_source_ref(source), "enabled_on_source": profile.enabled,
         **{name: getattr(profile, name) for name in _WAF_PROFILE_FIELDS}}
        for profile, source in pairs.all()
    ]


async def _ips_source_profiles(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    """The stored ``mapping`` object as-is: it already holds the design's ``{"kind","name"}``."""
    pairs = await session.execute(
        select(IpsSourceProfile, LogSource)
        .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
        .where(IpsSourceProfile.instance_id == instance.id)
        .order_by(*_LOG_SOURCE_ORDER)
    )
    return [
        {"log_source": _log_source_ref(source), "enabled_on_source": profile.enabled,
         **{name: getattr(profile, name) for name in _IPS_PROFILE_FIELDS}}
        for profile, source in pairs.all()
    ]


async def _identity_source_profiles(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    """The EVENT_ID, USERNAME and logon-type mapping, and the catalog event ids it collects."""
    pairs = await session.execute(
        select(IdentitySourceProfile, LogSource)
        .join(LogSource, LogSource.id == IdentitySourceProfile.log_source_id)
        .where(IdentitySourceProfile.instance_id == instance.id)
        .order_by(*_LOG_SOURCE_ORDER)
    )
    return [
        {"log_source": _log_source_ref(source), "enabled_on_source": profile.enabled,
         **{name: getattr(profile, name) for name in _IDENTITY_PROFILE_FIELDS}}
        for profile, source in pairs.all()
    ]


async def _alert_policies(
    session: AsyncSession, instance: QRadarInstance, refs: dict[str, Any]
) -> list[dict[str, Any]]:
    """A policy's ``scope_value`` is a class, a log source or a watch, depending on family."""
    scope = or_(AlertPolicy.instance_id == instance.id, AlertPolicy.instance_id.is_(None))
    policies = (await session.scalars(select(AlertPolicy).where(scope))).all()
    ordered = sorted(policies, key=lambda r: (
        0 if r.instance_id is None else 1, str(r.scope_kind), str(r.family),
        r.detector_type or "", r.scope_value,
    ))
    rows: list[dict[str, Any]] = []
    for policy in ordered:
        watch = policy.family == "property_watch"
        source = policy.scope_kind == "SOURCE" and not watch
        row: dict[str, Any] = {
            "scope": "global" if policy.instance_id is None else "instance",
            "scope_kind": policy.scope_kind, "family": policy.family,
            "log_source": refs["sources"].get(policy.scope_value) if source else None,
            "source_class": policy.scope_value
            if policy.scope_kind == "CLASS" and not watch else None,
            "watch": {"name": refs["watches"].get(policy.scope_value)} if watch else None,
            "detector_type": policy.detector_type, "mode": policy.mode,
        }
        if watch:
            if policy.scope_value not in refs["watches"]:
                _warn(row, "watch")
        elif source and policy.scope_value not in refs["sources"]:
            _warn(row, "log source")
        rows.append(row)
    return rows


async def _outage_alert_policies(
    session: AsyncSession, instance: QRadarInstance, refs: dict[str, Any]
) -> list[dict[str, Any]]:
    """A ``NO_EVENTS`` policy: a class string, or one log source by name and type.

    Outage has no watch scope (design, "Identity of referenced objects"), so a SOURCE row
    resolves only against this console's log sources.
    """
    scope = or_(
        OutageAlertPolicy.instance_id == instance.id,
        OutageAlertPolicy.instance_id.is_(None),
    )
    policies = (await session.scalars(select(OutageAlertPolicy).where(scope))).all()
    ordered = sorted(policies, key=lambda r: (
        0 if r.instance_id is None else 1, str(r.scope_kind), r.scope_value, str(r.mode),
    ))
    rows: list[dict[str, Any]] = []
    for policy in ordered:
        source = policy.scope_kind == "SOURCE"
        row: dict[str, Any] = {
            "scope": "global" if policy.instance_id is None else "instance",
            "scope_kind": policy.scope_kind,
            "source_class": None if source else policy.scope_value,
            "log_source": refs["sources"].get(policy.scope_value) if source else None,
            "mode": policy.mode,
            "silence_grace_buckets": policy.silence_grace_buckets,
        }
        if source and policy.scope_value not in refs["sources"]:
            _warn(row, "log source")
        rows.append(row)
    return rows


async def _alert_suppressions(
    session: AsyncSession, refs: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    """Only open, unexpired windows; an ended or past one is the old console's history."""
    rows = (await session.scalars(
        select(AlertSuppression).where(
            AlertSuppression.ended_at.is_(None), AlertSuppression.ends_at > now
        )
    )).all()
    ordered = sorted(rows, key=lambda r: (
        str(r.family), str(r.subject_id), r.detector_type or "",
    ))
    result: list[dict[str, Any]] = []
    for row in ordered:
        subject: dict[str, Any] | None
        key = str(row.subject_id)
        if row.family == "property_watch":
            subject = {"name": refs["watches"].get(key)}
            missing = None if key in refs["watches"] else "watch"
        elif row.family == "rule":
            subject = refs["rules"].get(key, {"name": None})
            missing = None if key in refs["rules"] else "rule"
        else:
            subject = refs["sources"].get(key)
            missing = None if key in refs["sources"] else "log source"
        entry: dict[str, Any] = {
            "family": row.family, "detector_type": row.detector_type,
            "log_source": subject if row.family in ("log_source", "waf", "ips", "identity")
            else None,
            "watch": subject if row.family == "property_watch" else None,
            "rule": subject if row.family == "rule" else None,
            "kind": row.kind, "reason": row.reason, "owner": row.owner,
            "starts_at": format_timestamp(row.starts_at),
            "ends_at": format_timestamp(row.ends_at), "reference": row.reference,
        }
        if missing is not None:
            _warn(entry, missing)
        result.append(entry)
    return result


async def _notification_destinations(session: AsyncSession) -> list[dict[str, Any]]:
    """The URL is configuration; the HMAC secret and encrypted header block are never read."""
    rows = (await session.execute(
        select(NotificationDestination.name, NotificationDestination.enabled,
               NotificationDestination.url, NotificationDestination.allow_http,
               NotificationDestination.allow_private)
        .order_by(NotificationDestination.name)
    )).all()
    return [{
        "name": name, "enabled_on_source": enabled, "url": url, "allow_http": allow_http,
        "allow_private": allow_private, "secrets_omitted": ["hmac_secret", "headers"],
    } for name, enabled, url, allow_http, allow_private in rows]


async def _notification_rules(
    session: AsyncSession, refs: dict[str, Any]
) -> list[dict[str, Any]]:
    """Destinations and groups are written as names, never as ids."""
    rules = (await session.scalars(select(NotificationRule).order_by(NotificationRule.name))).all()
    groups = refs["groups"]
    group_ref = {
        qid: {"name": name,
              "parent_name": groups[parent][0] if parent is not None and parent in groups else None}
        for qid, (name, parent) in groups.items()
    }
    # A group whose parent id points at a group this console no longer holds would otherwise
    # read as a top-level group and match a different row on import.
    orphan_parents = {
        qid for qid, (_name, parent) in groups.items()
        if parent is not None and parent not in groups
    }
    rows: list[dict[str, Any]] = []
    for rule in rules:
        missing: set[str] = set()
        destinations: list[dict[str, Any]] = []
        for did in rule.destination_ids:
            name = refs["destinations"].get(str(did))
            destinations.append({"name": name})
            if name is None:
                missing.add("destination")
        destinations.sort(key=lambda ref: ref["name"] or "")
        group_list: list[dict[str, Any]] = []
        for gid in rule.group_ids or []:
            ref = group_ref.get(gid, {"name": None, "parent_name": None})
            group_list.append(ref)
            if ref["name"] is None:
                missing.add("group")
            elif gid in orphan_parents:
                missing.add("group parent")
        group_list.sort(key=lambda ref: ref["name"] or "")
        row: dict[str, Any] = {
            "name": rule.name, "enabled": rule.enabled, "priority": rule.priority,
            "min_severity": rule.min_severity, "families": rule.families, "owners": rule.owners,
            "source_classes": rule.source_classes, "groups": group_list,
            "window_start_minute": rule.window_start_minute,
            "window_end_minute": rule.window_end_minute, "destinations": destinations,
            "stop_default": rule.stop_default,
        }
        for kind in sorted(missing):
            _warn(row, kind)
        rows.append(row)
    return rows


async def _email_templates(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.scalars(
        select(EmailTemplate).order_by(EmailTemplate.alert_type.nullsfirst())
    )).all()
    return [{
        "alert_type": row.alert_type, "subject": row.subject, "html_body": row.html_body,
        "text_body": row.text_body, "enabled": row.enabled,
    } for row in rows]


async def _email_recipients(session: AsyncSession) -> list[dict[str, Any]]:
    """One mailbox and how it is routed: an address is configuration, not a credential.

    ``updated_by`` and the timestamps are the actor's, not the routing rule's.
    """
    rows = (await session.scalars(
        select(EmailRecipient).order_by(EmailRecipient.address)
    )).all()
    return [{
        "address": row.address, "min_severity": row.min_severity,
        "alert_types": row.alert_types, "enabled": row.enabled,
    } for row in rows]


async def _ldap_config(session: AsyncSession) -> list[dict[str, Any]]:
    """The singleton seed row: no password column, and ``version`` is import state."""
    row = await session.get(LdapConfig, 1)
    if row is None:
        return []
    return [{
        "enabled": row.enabled, "urls": row.urls, "start_tls": row.start_tls,
        "ca_pem": row.ca_pem, "bind_template": row.bind_template,
        "timeout_seconds": row.timeout_seconds, "user_search_base": row.user_search_base,
    }]


async def _ref_maps(session: AsyncSession, instance: QRadarInstance) -> dict[str, Any]:
    """Local names a policy, suppression or rule ref resolves to on this console."""
    sources = (await session.scalars(
        select(LogSource).where(LogSource.instance_id == instance.id))).all()
    watches = (await session.scalars(
        select(PropertyWatch).where(PropertyWatch.instance_id == instance.id))).all()
    rules = (await session.scalars(
        select(AnalyticsRule).where(AnalyticsRule.instance_id == instance.id))).all()
    destinations = (await session.scalars(select(NotificationDestination))).all()
    groups = (await session.scalars(
        select(LogSourceGroup).where(LogSourceGroup.instance_id == instance.id))).all()
    return {
        "sources": {str(r.id): _log_source_ref(r) for r in sources},
        "watches": {str(r.id): r.name for r in watches},
        "rules": {str(r.id): {"name": r.name, "rule_type": r.rule_type,
                              "is_building_block": r.is_building_block} for r in rules},
        "destinations": {str(r.id): r.name for r in destinations},
        "groups": {r.qradar_id: (r.name, r.parent_qradar_id) for r in groups},
    }


async def export_document(
    session: AsyncSession, instance: QRadarInstance, source_label: str, exported_at: datetime
) -> dict[str, Any]:
    """The whole secret-free document for one console, its empty sections included."""
    refs = await _ref_maps(session, instance)
    return build_document(
        product_version=read_version(),
        exported_at=exported_at,
        source_label=source_label,
        sections={
            "log_source_settings": await _log_source_settings(session, instance),
            "classification_overrides": await _classification_overrides(session, instance),
            "classification_rules": await _classification_rules(session, instance),
            "waf_source_profiles": await _waf_source_profiles(session, instance),
            "ips_source_profiles": await _ips_source_profiles(session, instance),
            "identity_source_profiles": await _identity_source_profiles(session, instance),
            "alert_policies": await _alert_policies(session, instance, refs),
            "outage_alert_policies": await _outage_alert_policies(session, instance, refs),
            "alert_suppressions": await _alert_suppressions(session, refs, datetime.now(UTC)),
            "notification_destinations": await _notification_destinations(session),
            "notification_rules": await _notification_rules(session, refs),
            "email_templates": await _email_templates(session),
            "email_recipients": await _email_recipients(session),
            "ldap_config": await _ldap_config(session),
            "search_cadence": await _search_cadence(session),
        },
    )
