"""Read the config bundle's fleet sections from this product's database (CFG-1, D-78).

Slice 1c: a local ``SELECT`` per section, no provider call and no secret column; later slices
append the rest. Rows follow the file's natural key with NULLs first, so two exports of one
unchanged database differ only in ``exported_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import build_document
from app.core.version import read_version
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.search_cadence import SearchCadenceSetting

#: The SOC-owned log-source columns. Mirrored QRadar columns, maintenance and health stay out.
_SOC_FIELDS = (
    "criticality", "owner", "owner_email", "monitoring_enabled", "expected_interval_seconds",
    "business_hours_only", "business_hours_start", "business_hours_end", "business_days",
    "timezone_name", "custom_thresholds",
)

#: An operator rule's file fields, in the design's order; ``built_in`` and ``scope`` lead.
_OPERATOR_RULE_FIELDS = ("kind", "pattern", "target_class", "priority", "enabled", "description")


def _log_source_ref(row: LogSource) -> dict[str, Any]:
    """A log source is referenced by name and type, never by a QRadar id or a UUID."""
    return {"name": row.name, "type_name": row.type_name}


async def _log_source_settings(
    session: AsyncSession, instance: QRadarInstance
) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(LogSource).where(LogSource.instance_id == instance.id).order_by(
            LogSource.name, LogSource.type_name.nullsfirst()
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
        .order_by(LogSource.name, LogSource.type_name.nullsfirst())
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
            "search_cadence": await _search_cadence(session),
        },
    )
