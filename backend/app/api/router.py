"""Top-level API router aggregating all versioned route modules."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import requires
from app.api.routes import (
    alerts,
    anomalies,
    classification_rules,
    coverage,
    fleet,
    health,
    log_sources,
    offenses,
    overview,
    providers,
    rules,
    search_cadence,
    searches,
    waf,
    waf_catalog,
)
from app.security.rbac import (
    PERM_ALERT_READ,
    PERM_ANOMALY_READ,
    PERM_COVERAGE_READ,
    PERM_FLEET_READ,
    PERM_LOG_SOURCE_READ,
    PERM_OFFENSE_READ,
    PERM_OVERVIEW_READ,
    PERM_PROVIDER_READ,
    PERM_RULE_READ,
    PERM_SEARCH_CADENCE_READ,
    PERM_SEARCH_READ,
    PERM_WAF_ADMIN,
)

api_router = APIRouter()
api_router.include_router(health.router)
# Guarded at the router, like everything below. /health is the only open surface:
# container probes carry no credentials. Handlers that mutate state keep their own
# write-permission checks on top of the router's read requirement.
api_router.include_router(
    overview.router, dependencies=[Depends(requires(PERM_OVERVIEW_READ))]
)
api_router.include_router(
    log_sources.router, dependencies=[Depends(requires(PERM_LOG_SOURCE_READ))]
)
api_router.include_router(
    searches.router, dependencies=[Depends(requires(PERM_SEARCH_READ))]
)
api_router.include_router(alerts.router, dependencies=[Depends(requires(PERM_ALERT_READ))])
api_router.include_router(
    search_cadence.router,
    dependencies=[Depends(requires(PERM_SEARCH_CADENCE_READ))],
)
# --- Phase 3 ---
# Guarded at the router. These endpoints expose offence records (parsed
# usernames, source addresses, analyst assignment) and a map of where detection
# coverage is absent; neither may reach an unauthenticated caller. The `read:*`
# wildcard satisfies all four, so existing read-only roles are unaffected.
api_router.include_router(
    offenses.router, dependencies=[Depends(requires(PERM_OFFENSE_READ))]
)
api_router.include_router(rules.router, dependencies=[Depends(requires(PERM_RULE_READ))])
api_router.include_router(
    coverage.router, dependencies=[Depends(requires(PERM_COVERAGE_READ))]
)
api_router.include_router(
    providers.router, dependencies=[Depends(requires(PERM_PROVIDER_READ))]
)
# --- Phase A ---
# The behavioural anomaly and investigation surface. Guarded at the router for
# the same reason as Phase 3: contributor evidence carries source addresses and,
# where the DSM supplies them, usernames. The `read:*` wildcard satisfies it, so
# existing read-only roles keep working.
api_router.include_router(
    anomalies.router, dependencies=[Depends(requires(PERM_ANOMALY_READ))]
)
api_router.include_router(
    anomalies.behavior_router, dependencies=[Depends(requires(PERM_ANOMALY_READ))]
)
# --- Phase B.1 ---
# WAF collection operations. Administrative rather than analytical, so it needs
# `admin:waf`; the `read:*` wildcard does not satisfy it.
api_router.include_router(waf.router, dependencies=[Depends(requires(PERM_WAF_ADMIN))])
# The mapping editor's property-name catalog: names only, read-only, same admin:waf guard.
api_router.include_router(waf_catalog.router, dependencies=[Depends(requires(PERM_WAF_ADMIN))])
# --- Phase C ---
# The fleet view and classification rules. Both reads need `read:fleet` (satisfied
# by `read:*`); rule writes check `admin:classification` in the handler.
api_router.include_router(fleet.router, dependencies=[Depends(requires(PERM_FLEET_READ))])
api_router.include_router(
    classification_rules.router, dependencies=[Depends(requires(PERM_FLEET_READ))]
)
