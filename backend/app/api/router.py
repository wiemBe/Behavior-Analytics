"""Top-level API router aggregating all versioned route modules."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_principal, requires
from app.api.routes import (
    alerts,
    anomalies,
    auth,
    catchall,
    classification_rules,
    coverage,
    custom_rules,
    dashboards,
    fleet,
    geo,
    health,
    history_backfill,
    ips,
    log_sources,
    me,
    offenses,
    outage_policy,
    overview,
    property_watches,
    providers,
    rules,
    search_cadence,
    searches,
    users,
    version,
    waf,
    waf_catalog,
)
from app.security.rbac import (
    PERM_ALERT_READ,
    PERM_ANOMALY_READ,
    PERM_AUTH_ADMIN,
    PERM_COVERAGE_READ,
    PERM_CUSTOM_RULE_READ,
    PERM_DASHBOARD_READ,
    PERM_FLEET_READ,
    PERM_GEO_READ,
    PERM_HISTORY_BACKFILL_READ,
    PERM_IPS_ADMIN,
    PERM_LOG_SOURCE_READ,
    PERM_OFFENSE_READ,
    PERM_OUTAGE_ADMIN,
    PERM_OVERVIEW_READ,
    PERM_PROPERTY_WATCH_READ,
    PERM_PROVIDER_READ,
    PERM_RULE_READ,
    PERM_SEARCH_CADENCE_READ,
    PERM_SEARCH_READ,
    PERM_USER_ADMIN,
    PERM_WAF_ADMIN,
)

api_router = APIRouter()
api_router.include_router(health.router)
# Build identity: open like health, and it carries no data beyond version, SHA and head.
api_router.include_router(version.router)
# Login is unauthenticated. Logout and password change need a principal.
# Directory configuration needs admin:auth, on its own router.
api_router.include_router(auth.public_router)
api_router.include_router(auth.session_router)
api_router.include_router(auth.admin_router, dependencies=[Depends(requires(PERM_AUTH_ADMIN))])
api_router.include_router(users.router, dependencies=[Depends(requires(PERM_USER_ADMIN))])
# /me returns only the caller's own identity and grants; it requires
# authentication but no additional permission.
api_router.include_router(me.router, dependencies=[Depends(get_principal)])
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
# History backfill admin page: reads need `read:history_backfill`; the three writes add
# `admin:history_backfill` on the route.
api_router.include_router(
    history_backfill.router,
    dependencies=[Depends(requires(PERM_HISTORY_BACKFILL_READ))],
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
# --- Phase D ---
# IPS source mappings control future collection semantics and require their own
# administrative capability; neither read:* nor admin:waf satisfies it.
api_router.include_router(ips.router, dependencies=[Depends(requires(PERM_IPS_ADMIN))])
# --- Phase C ---
# The fleet view and classification rules. Both reads need `read:fleet` (satisfied
# by `read:*`); rule writes check `admin:classification` in the handler.
api_router.include_router(fleet.router, dependencies=[Depends(requires(PERM_FLEET_READ))])
api_router.include_router(
    classification_rules.router, dependencies=[Depends(requires(PERM_FLEET_READ))]
)
# Phase C.2b catch-all senders: read-guarded like /fleet; review writes check
# `log_source:write` in the handler.
api_router.include_router(catchall.router, dependencies=[Depends(requires(PERM_FLEET_READ))])
# D-10 geography: aggregate events per country for the home map; `read:geo` (`read:*`).
api_router.include_router(geo.router, dependencies=[Depends(requires(PERM_GEO_READ))])
# The outage alert policy is administrative: it changes what pages a human, so
# `admin:outage` guards every method, reads included; `read:*` does not satisfy it.
api_router.include_router(
    outage_policy.router, dependencies=[Depends(requires(PERM_OUTAGE_ADMIN))]
)
# UI redesign (UR.5.5) saved dashboards: read-guarded; every write handler checks
# `dashboard:write` itself, and ownership and `admin:dashboards` where they apply.
api_router.include_router(
    dashboards.router, dependencies=[Depends(requires(PERM_DASHBOARD_READ))]
)
# Phase F. Custom threshold rules over stored aggregates. Read-guarded;
# `read:*` satisfies `read:custom_rules`. Every write handler checks
# `custom_rule:write` itself. `read:*` does not grant that write.
api_router.include_router(
    custom_rules.router, dependencies=[Depends(requires(PERM_CUSTOM_RULE_READ))]
)
# Phase G. Property watches: the values of a QRadar property per bucket, clear text (D-22).
# Read-guarded at the mount; `read:*` satisfies `read:property_watches`. Every write handler,
# the preview included, checks `property_watch:write` itself. `read:*` does not grant it.
api_router.include_router(
    property_watches.router, dependencies=[Depends(requires(PERM_PROPERTY_WATCH_READ))]
)
