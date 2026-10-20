// The permission strings a widget may require (UR.5.1). The list mirrors the `PERM_*`
// constants of `backend/app/security/rbac.py`; a test reads that file and fails when the two
// differ, so a permission cannot be added on one side only.

export const KNOWN_PERMISSIONS = [
  "search:execute",
  "search:write",
  "alert:ack",
  "alert:resolve",
  "admin:*",
  "read:offenses",
  "read:rules",
  "read:coverage",
  "read:providers",
  "rule:write",
  "coverage:write",
  "read:anomalies",
  "admin:waf",
  "admin:ips",
  "read:log_sources",
  "read:overview",
  "log_source:write",
  "read:searches",
  "read:alerts",
  "read:search_cadence",
  "admin:search_cadence",
  "read:history_backfill",
  "admin:history_backfill",
  "read:fleet",
  "admin:classification",
  "admin:outage",
  "read:geo",
  "read:dashboards",
  "dashboard:write",
  "admin:dashboards",
  // Phase F. Listing and previewing a custom rule is a view of the estate, so `read:*`
  // satisfies it. Authoring one is a write: `read:*` does not grant it. `admin:*` does.
  "read:custom_rules",
  "custom_rule:write",
  // Account administration: creating and changing a sign-in. `admin:*` satisfies it; `read:*`
  // does not.
  "admin:users",
  // Directory configuration: pointing the application at a directory. `admin:*` satisfies it;
  // `read:*` does not.
  "admin:auth",
  // Phase G. Reading a property watch (its values are stored in clear text) is `read:*`;
  // authoring, enabling, pinning, deleting and previewing one is a write `read:*` does not grant.
  "read:property_watches",
  "property_watch:write",
  // History backfill spends Ariel searches: `read:*` does not grant the admin one, `admin:*` does.
  "read:history_backfill",
  "admin:history_backfill",
] as const;

export type KnownPermission = (typeof KNOWN_PERMISSIONS)[number];
