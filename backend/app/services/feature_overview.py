"""Stored feature evidence only: no provider, probes, or per-profile queries."""
from dataclasses import asdict
from datetime import UTC, datetime
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, switch_dependent_problems
from app.models.custom_rule import CustomRule
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.property_watch import PropertyWatch
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafSourceProfile
from app.security.rbac import Principal
from app.services.ariel_load_current import read_ariel_runs_by_family
from app.services.feature_switches import ALERTING_REQUIRES, effective_settings, resolve
from app.services.qradar_access import read_access

# Family, cadence/telemetry caller, switches. Modifiers belong to the WAF row.
FAMILIES = (
    ("waf", "WAF_COLLECTION", ("waf_collection_enabled", "waf_alerting_enabled",
                               "waf_outcome_collection_enabled", "waf_client_collection_enabled")),
    ("ips", "IPS_COLLECTION", ("ips_collection_enabled", "ips_alerting_enabled")),
    ("identity", "IDENTITY_COLLECTION",
     ("identity_collection_enabled", "identity_alerting_enabled")),
    ("rule_match", "RULE_MATCH", ("rule_match_collection_enabled", "rule_alerting_enabled")),
    ("property_watch", "PROPERTY_WATCH", ("pw_collection_enabled", "pw_alerting_enabled")),
    ("custom_rules", None, ("custom_rules_enabled", "custom_rules_alerting_enabled")),
    ("catchall", "CATCHALL_SENDERS", ("catchall_collection_enabled",
                                      "catchall_new_sender_alerts_enabled")),
    ("geo", "GEO_COLLECTION", ("geo_collection_enabled",)),
    ("qradar_health", "QRADAR_HEALTH", ("qradar_health_collection_enabled",)),
    ("explanations", "ANOMALY_EXPLANATION", ("explanation_enabled",)),
    ("inventory", None, ("inventory_new_source_alerts_enabled",)),
    ("volume", "LOG_SOURCE_VOLUME", ()),
)
PROFILE_ACCESS = ("ariel_searches", "log_sources", "custom_properties", "property_expressions")
ACCESS: dict[str, tuple[str, ...]] = dict.fromkeys(
    ("waf", "ips", "identity", "property_watch"), PROFILE_ACCESS)
ACCESS.update(rule_match=("ariel_searches", "analytics_rules"), geo=("ariel_searches",),
              catchall=("ariel_searches",), volume=("ariel_searches",),
              qradar_health=("ariel_searches", "system_about", "managed_servers",
                             "deployment_hosts", "license_pool", "ariel_search_list"),
              explanations=("ariel_searches",), custom_rules=(), inventory=())


async def prerequisites(session: AsyncSession) -> dict[str, dict[str, int]]:
    result = {}
    for name, model in (("waf", WafSourceProfile), ("ips", IpsSourceProfile),
                        ("identity", IdentitySourceProfile), ("property_watch", PropertyWatch),
                        ("custom_rules", CustomRule)):
        model_type: Any = model
        columns = [func.count(), func.count().filter(model_type.enabled.is_(True))]
        if name in ("waf", "ips", "identity"):
            columns.append(func.count().filter(
                (model_type.validation_status == "VALID")
                & (model_type.validated_profile_version == model_type.profile_version)))
        row = (await session.execute(select(*columns))).one()
        result[name] = dict(zip(("total", "enabled", "validated"), row, strict=False))
    return result


def estimate(name: str, cadence: Any, counts: dict, consoles: int, base: Settings) -> dict:
    count = counts.get(name, {}).get("enabled", consoles)
    unit = "enabled_profiles" if name in ("waf", "ips", "identity") else {
        "property_watch": "enabled_watches", "custom_rules": "enabled_rules",
    }.get(name, "enabled_consoles")
    cap = base.search_cadence_max_searches_per_tick
    per_tick = cap * consoles
    if name == "waf":
        per_tick = 2 * cap * count  # A volume request uses two Ariel searches.
    if name == "property_watch":
        per_tick = base.pw_max_searches_per_tick * consoles
    if name == "qradar_health":
        per_tick = 2 * consoles
    if name == "rule_match" and cadence:
        windows = ceil(cadence.cadence_seconds / base.rule_match_window_seconds)
        per_tick = max(cap, 2 * (windows + base.rule_match_max_windows_per_run - 1) - 1) * consoles
    reason = None
    if (not count or not consoles or cadence is None
            or name in ("explanations", "custom_rules", "inventory")):
        reason = "NOT_COLLECTED"
    return {"upper_bound_searches_per_hour": None if reason else
            ceil(3600 / cadence.cadence_seconds) * per_tick,
            "reason": reason, "count": count, "count_unit": unit, "enabled_consoles": consoles,
            "scope": "scheduled_collection_excluding_history_and_probes",
            "basis": "per_profile_tick_budget" if name == "waf" else "per_console_tick_budget"}


async def overview(session: AsyncSession, base: Settings, principal: Principal) -> dict:
    resolved = await resolve(session, base)
    if resolved.reason:
        await session.rollback()
    counts: dict = {}
    cadences: dict = {}
    measured: dict = {}
    consoles: list = []
    access: list = []
    can_access = principal.has("admin:*")
    if not resolved.reason:
        counts = await prerequisites(session)
        consoles = list((await session.scalars(select(QRadarInstance).where(
            QRadarInstance.enabled.is_(True)))).all())
        stored = (await session.scalars(select(SearchCadenceSetting))).all()
        cadences = {r.family: r for r in stored}
        measured = await read_ariel_runs_by_family(session, as_of=datetime.now(UTC))
        if can_access:
            access = await read_access(session, consoles)
    rows = []
    for name, caller, keys in FAMILIES:
        cadence = cadences.get("PHASE_A_VOLUME" if name == "volume" else caller)
        switches = {key: {**asdict(resolved.values[key]),
                    "requires": ALERTING_REQUIRES.get(key),
                    "inert": bool(ALERTING_REQUIRES.get(key) and not
                                  resolved.values[ALERTING_REQUIRES[key]].enabled)} for key in keys}
        rows.append({"family": name, "switches": switches,
                     "prerequisites": counts.get(name),
                     "estimate": estimate(name, cadence, counts, len(consoles), base),
                     "measured_last_hour": measured.get(caller),
                     "measured_reason": None if caller in measured else "NOT_COLLECTED",
                     "cadence_seconds": cadence.cadence_seconds if cadence else None,
                     "last_run": {"at": cadence.last_run_at, "outcome": cadence.last_run_outcome,
                                  "searches": cadence.last_run_searches,
                                  "buckets": cadence.last_run_buckets} if
                                  cadence and cadence.last_run_at else None,
                     "last_run_reason": (None if cadence and cadence.last_run_at
                                         else "NOT_COLLECTED"),
                     "access": [{"instance_id": console.instance_id,
                                 "entries": [e.model_dump() for e in console.entries
                                             if e.id in ACCESS[name]]} for console in access]
                                if can_access and not resolved.reason else None})
    return {"runtime_switches_active": False, "reason": resolved.reason, "families": rows,
            "settings_problems": switch_dependent_problems(effective_settings(base, resolved))}
