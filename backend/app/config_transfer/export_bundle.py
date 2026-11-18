"""Read the config bundle's sections from this product's database (CFG-1, D-78).

Slice 1c: a local ``SELECT`` per section, no provider call and no secret column; later slices
append the rest. Rows follow the file's natural key with NULLs first, so two exports of one
unchanged database differ only in ``exported_at``.

Slice 1c2: the three profile sections carry the mapping, ``enabled_on_source`` and nothing from
the validation state -- an imported row is never ``VALID`` and is never enabled (AGENTS §2 rule 8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import build_document
from app.core.version import read_version
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
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


async def export_document(
    session: AsyncSession, instance: QRadarInstance, source_label: str, exported_at: datetime
) -> dict[str, Any]:
    """The whole secret-free document for one console, its empty sections included."""
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
            "search_cadence": await _search_cadence(session),
        },
    )
