"""Plan fleet merges using only local configuration; the caller owns the transaction."""

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.config_transfer.refs import ResolutionCatalog
from app.config_transfer.report import ImportReport
from app.models.enums import (
    ClassificationRuleKind,
    SearchFamily,
    SourceClass,
    default_search_cadence,
)
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.search_cadence import SearchCadenceSetting
from app.schemas.fleet import ClassificationRuleCreate, check_pattern
from app.schemas.log_source import LogSourceUpdate
from app.schemas.search_cadence import SearchCadenceUpdate

SOC_DEFAULTS: dict[str, Any] = {
    "criticality": "MEDIUM", "owner": None, "owner_email": None, "monitoring_enabled": True,
    "expected_interval_seconds": None, "business_hours_only": False, "business_hours_start": 8,
    "business_hours_end": 18, "business_days": [1, 2, 3, 4, 5], "timezone_name": "UTC",
    "custom_thresholds": {},
}
FLEET = ("log_source_settings", "classification_overrides",
         "classification_rules", "search_cadence")


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


async def plan(session: AsyncSession, document: dict[str, Any], instance: QRadarInstance,
               report: ImportReport, actor: str, mode: str) -> None:
    """Make the same writes for dry-run and apply; no provider or secret column is read."""
    sources = list(await session.scalars(select(LogSource).where(
        LogSource.instance_id == instance.id).options(load_only(
            LogSource.id, LogSource.name, LogSource.type_name,
            *(getattr(LogSource, key) for key in SOC_DEFAULTS)))))
    by_id = {str(row.id): row for row in sources}
    catalog = ResolutionCatalog(log_sources=tuple(
        (str(row.id), row.name, row.type_name) for row in sources))
    # One query, no bind parameter per source: asyncpg caps a statement at 32,767 parameters.
    # Joining the instance's own sources yields exactly the rows the id list used to.
    classifications = {str(row.log_source_id): row for row in await session.scalars(
        select(LogSourceClassification).join(
            LogSource, LogSourceClassification.log_source_id == LogSource.id
        ).where(LogSource.instance_id == instance.id))}
    rules = list(await session.scalars(select(ClassificationRule).where(or_(
        ClassificationRule.instance_id == instance.id, ClassificationRule.instance_id.is_(None)))))
    cadences = {row.family: row for row in await session.scalars(select(SearchCadenceSetting))}
    now = datetime.now(UTC)
    reasons = {
        "created": "applied configuration" if mode == "apply" else "would apply configuration",
        "unchanged": "configuration already matches",
        "conflict": "stored configuration differs from the file and is not default",
    }
    for section, rows in document["sections"].items():
        keys = Counter(_key(row) for row in rows)
        for row in rows:
            key, local_id = _key(row), None
            row_reason: str | None = None
            if section not in FLEET:
                report.add(section, "unresolved", key, "not applied in this release")
                continue
            try:
                if keys[key] > 1:
                    raise RowError("duplicate natural key in the file")
                if row.get("warnings"):
                    raise RowError("export reported an unresolved reference")
                if section in FLEET[:2]:
                    match = catalog.match_log_source(row.get("log_source"))
                    if match.exact_id is None:
                        raise RowError(match.reason)
                    local_id = str(match.exact_id)
                    _shape(row["log_source"], {"name", "type_name"})
                if section == "log_source_settings":
                    _shape(row, {"log_source", *SOC_DEFAULTS})
                    values = LogSourceUpdate.model_validate(
                        {key: row[key] for key in SOC_DEFAULTS}).model_dump(exclude_unset=True)
                    if any(values[key] is None for key in SOC_DEFAULTS
                           if key not in {"owner", "owner_email", "expected_interval_seconds"}):
                        raise RowError("non-null SOC field is null")
                    source = by_id[str(local_id)]
                    outcome = _merge(source, values, all(
                        getattr(source, key) == value for key, value in SOC_DEFAULTS.items()))
                elif section == "classification_overrides":
                    _shape(row, {"log_source", "override_class"})
                    value = (SourceClass(row["override_class"])
                             if row["override_class"] is not None else None)
                    classification = classifications.get(str(local_id))
                    if classification is None:
                        raise RowError("log source has not been classified")
                    if value is None and classification.override_class is not None:
                        # The import never clears an operator override; it does not write a
                        # null over a set one, and it must not call that "already matches".
                        outcome = "conflict"
                        row_reason = "file has no override; import never clears a local override"
                    else:
                        outcome = ("unchanged" if value is None else _merge(
                            classification, {"override_class": value},
                            classification.override_class is None))
                        if outcome == "created":
                            classification.override_by, classification.override_at = (
                                actor[:255], now)
                elif section == "classification_rules":
                    kind = ClassificationRuleKind(row["kind"])
                    if not isinstance(row["pattern"], str):
                        raise RowError("classification pattern is not a string")
                    check_pattern(kind, row["pattern"])
                    if type(row["built_in"]) is not bool or type(row["enabled"]) is not bool:
                        raise RowError("classification flags must be booleans")
                    if row["built_in"]:
                        _shape(row, {"built_in", "kind", "pattern", "enabled"})
                        matches = [rule for rule in rules if rule.built_in and
                                   (rule.kind, rule.pattern) == (kind, row["pattern"])]
                        if not matches:
                            raise RowError("built-in classification rule is not seeded")
                        values = {"enabled": row["enabled"]}
                    else:
                        _shape(row, {"built_in", "scope", "kind", "pattern", "target_class",
                                     "priority", "enabled", "description"})
                        if row["scope"] not in {"global", "instance"}:
                            raise RowError("classification scope is not global or instance")
                        scope = instance.id if row["scope"] == "instance" else None
                        values = ClassificationRuleCreate.model_validate(
                            {key: value for key, value in row.items()
                             if key not in {"built_in", "scope"}} | {"instance_id": scope}
                        ).model_dump()
                        matches = [rule for rule in rules if
                                   (rule.instance_id, rule.kind, rule.pattern) ==
                                   (scope, kind, row["pattern"])]
                    if len(matches) > 1:
                        raise RowError("classification natural key is ambiguous")
                    if not matches:
                        rule = ClassificationRule(**values, built_in=False, created_by=actor[:255])
                        session.add(rule)
                        rules.append(rule)
                        outcome = "created"
                    elif matches[0].built_in != row["built_in"]:
                        outcome = "conflict"
                    else:
                        outcome = _merge(matches[0], values,
                                         row["built_in"] and matches[0].enabled is True)
                else:
                    _shape(row, {"family", "cadence_seconds"})
                    family = SearchFamily(row["family"])
                    values = SearchCadenceUpdate.model_validate(
                        {"cadence_seconds": row["cadence_seconds"]}).model_dump()
                    cadence = cadences.get(family)
                    if cadence is None:
                        raise RowError("cadence family is not seeded")
                    outcome = _merge(cadence, values, cadence.updated_by is None and
                                     cadence.cadence_seconds == default_search_cadence(family))
                    if outcome == "created":
                        cadence.updated_by, cadence.updated_at = actor[:255], now
                report.add(section, outcome, key, row_reason or reasons[outcome], local_id)
            except (ValueError, TypeError, KeyError) as exc:
                # Never stringify validators: their messages can include input values.
                reason = str(exc) if type(exc) is RowError else "row validation failed"
                report.add(section, "unresolved", key, reason, local_id)
    await session.flush()
