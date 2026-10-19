"""Role-based authorization.

Permissions are coarse capability strings on Role.permissions (see
app/models/identity.py). A user holds the union of their roles' permissions.
`admin:*` and `read:*` are wildcards. Checks are centralised here so routes
declare *what* they require, not *how* it is evaluated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, resolved from the auth layer.

    `username`, `auth_source`, `must_change_password`, `roles` and `user_id`
    are populated for a session. The development principals leave them empty.
    Defaults keep every existing `Principal(...)` call valid.
    """

    subject: str
    email: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    username: str | None = None
    auth_source: str | None = None
    must_change_password: bool = False
    roles: tuple[str, ...] = ()
    user_id: uuid.UUID | None = None

    def has(self, permission: str) -> bool:
        if permission in self.permissions:
            return True
        # Wildcard: "admin:*" grants "admin:anything"; "read:*" grants "read:*".
        prefix = permission.split(":", 1)[0]
        return f"{prefix}:*" in self.permissions or "admin:*" in self.permissions


# Capability constants used by routes/services.
PERM_SEARCH_EXECUTE = "search:execute"
PERM_SEARCH_WRITE = "search:write"
PERM_ALERT_ACK = "alert:ack"
PERM_ALERT_RESOLVE = "alert:resolve"
PERM_ADMIN = "admin:*"

# Phase 3 read capabilities. Offense records carry parsed usernames, source
# addresses and analyst assignments, and the rule/coverage views describe where
# detection is absent -- a map of where to attack. None of it may be served to
# an unauthenticated caller, so these routes declare a permission rather than
# relying on the endpoint being read-only.
#
# All three are satisfied by the `read:*` wildcard, so an existing read-only
# role keeps working; what changes is that *some* principal is now required.
PERM_OFFENSE_READ = "read:offenses"
PERM_RULE_READ = "read:rules"
PERM_COVERAGE_READ = "read:coverage"
PERM_PROVIDER_READ = "read:providers"
# Editing SOC-owned rule metadata (owner, MITRE mapping, expected firing rate)
# and recording a detection-coverage mapping. Both are writes, so `read:*` must
# not grant them: a read-only role can already see ownership and the detection
# map, and letting it rewrite either would change the evidence the coverage
# verdict rests on -- a viewer could quietly mark a real gap as covered.
PERM_RULE_WRITE = "rule:write"
PERM_COVERAGE_WRITE = "coverage:write"
# Phase A. Behavioral anomalies expose contributor evidence (source and
# destination addresses, usernames where the DSM supplies them), so the
# investigation surface is guarded exactly like offences are.
PERM_ANOMALY_READ = "read:anomalies"
# Phase B.1. WAF collection operations: profile validation state, watermarks,
# failures and admission counts. Administrative, so `read:*` does not grant it.
PERM_WAF_ADMIN = "admin:waf"
# Phase D profile administration is intentionally separate: WAF administrators
# do not implicitly control IPS collection semantics.
PERM_IPS_ADMIN = "admin:ips"
# Log-source inventory and the SOC overview. The inventory is a map of every
# monitored system and its owner; the overview aggregates it. Both were once
# mounted with no guard at all. `read:*` satisfies the two read capabilities.
PERM_LOG_SOURCE_READ = "read:log_sources"
PERM_OVERVIEW_READ = "read:overview"
# Editing SOC-owned log-source fields (monitoring_enabled, maintenance_mode ...).
# A write, so `read:*` does not grant it: switching monitoring off silently
# blinds the platform to that source. Inventory sync needs `admin:*` instead.
PERM_LOG_SOURCE_WRITE = "log_source:write"
# Searches and alerts: the read routes were unguarded while their mutating
# handlers checked a write permission. Now guarded at the router like the rest.
PERM_SEARCH_READ = "read:searches"
PERM_ALERT_READ = "read:alerts"
# Search-family cadence is visible to read-only roles, while changing it is an
# operational control reserved for administrators, matching the WAF pattern.
PERM_SEARCH_CADENCE_READ = "read:search_cadence"
PERM_SEARCH_CADENCE_ADMIN = "admin:search_cadence"
# Phase C. The fleet view maps every monitored source to a class, an owner-facing
# review state and its volume, so it is guarded at the router like the other
# inventory surfaces; `read:*` satisfies it. Classification rules decide which
# policy a source falls under (a rule that matches everything to OTHER would hide
# a firewall from firewall policy), so writing them is administrative and `read:*`
# does not grant it. Overrides and reviews reuse `log_source:write`.
PERM_FLEET_READ = "read:fleet"
PERM_CLASSIFICATION_ADMIN = "admin:classification"
# Phase C.2. The outage alert policy decides which silences page a human; that
# is operational control, so `read:*` does not grant it and a fleet viewer
# cannot silence a class's outages.
PERM_OUTAGE_ADMIN = "admin:outage"
# D-10. The country map shows where the monitored traffic comes from and goes to. That is
# aggregate counts per country, never an address, but it still describes the estate, so the
# route declares a permission; `read:*` satisfies it.
PERM_GEO_READ = "read:geo"
# UI redesign (UR.5.5). Saved dashboards hold only widget ids and geometry, so reading them
# is `read:*`. Saving a layout is a write: `read:*` does not grant it (a read-only role sees
# the template and cannot persist changes), like `log_source:write`. Sharing a dashboard with
# every reader, or taking it back, is administrative and needs `admin:dashboards`
# (`admin:*` satisfies it).
PERM_DASHBOARD_READ = "read:dashboards"
PERM_DASHBOARD_WRITE = "dashboard:write"
PERM_DASHBOARD_SHARE = "admin:dashboards"
# Phase F. A custom rule thresholds aggregates the platform already stored.
# Listing, reading and previewing one is a view of the estate, so `read:*`
# satisfies it. Authoring, enabling or deleting one is a write: `read:*` does
# not grant `custom_rule:write`, the same split as `dashboard:write`. `admin:*`
# does satisfy the write, because that wildcard grants every permission; the
# admin role therefore does not list the string.
PERM_CUSTOM_RULE_READ = "read:custom_rules"
PERM_CUSTOM_RULE_WRITE = "custom_rule:write"
# Phase G. A property watch counts the values of a QRadar property per bucket, and the
# values are stored in clear text (design §G.7.2, D-22), so reading them is `read:*`, as
# offenses carry usernames to any reader. Authoring, enabling, pinning, deleting and
# previewing one (a preview spends an Ariel search) is a write: `read:*` does not grant
# `property_watch:write`. `admin:*` does, so the admin role does not list the string.
PERM_PROPERTY_WATCH_READ = "read:property_watches"
PERM_PROPERTY_WATCH_WRITE = "property_watch:write"
# History backfill (HISTORY-BACKFILL §B.10.3). The admin page shows, per console and per
# source, what was read from QRadar's past and what could not be, so reading it is `read:*`.
# Recording the answer, starting or stopping a reading is an operational control that
# spends Ariel searches: `read:*` does not grant `admin:history_backfill`, `admin:*` does.
PERM_HISTORY_BACKFILL_READ = "read:history_backfill"
PERM_HISTORY_BACKFILL_ADMIN = "admin:history_backfill"
# Accounts and directory configuration. `admin:*` satisfies both. `read:*`
# does not: a viewer can see the estate and cannot create a sign-in or point
# the application at a directory.
PERM_USER_ADMIN = "admin:users"
PERM_AUTH_ADMIN = "admin:auth"


class PermissionDenied(Exception):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        super().__init__(f"missing required permission: {permission}")


def require_permission(principal: Principal, permission: str) -> None:
    if not principal.has(permission):
        raise PermissionDenied(permission)
