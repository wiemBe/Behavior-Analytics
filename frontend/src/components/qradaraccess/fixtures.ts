// Builders for the QRadar access tests. Every entry is a plausible catalog row; a test overrides
// only what it is about.

import type {
  AccessCheckAnswer,
  AccessConsole,
  AccessEntry,
  QRadarAccess,
} from "@/lib/api";

export const CHECKED = "2026-11-03T09:15:00Z";

export function entry(over: Partial<AccessEntry> = {}): AccessEntry {
  return {
    id: "log_sources",
    title: "Log sources",
    method: "GET",
    path: "/api/config/event_sources/log_source_management/log_sources",
    feeds: "The log source inventory.",
    used_by: ["log source inventory collector", "Log sources page"],
    status: "ok",
    http_status: 200,
    checked_at: CHECKED,
    previous_status: null,
    syncs_started: [],
    required_capability: null,
    capability_verified: false,
    capability_hint: "Read-only Configuration, View Log Sources",
    sources: ["https://www.ibm.com/docs/en/qradar-common?topic=api-5000-config"],
    covers: [],
    ...over,
  };
}

export const ALL_OK: AccessEntry[] = [
  entry({ id: "system_about", title: "System information", path: "/api/system/about" }),
  entry({ id: "log_sources", title: "Log sources" }),
  entry({ id: "analytics_rules", title: "Analytics rules", path: "/api/analytics/rules" }),
];

export function consoleOf(over: Partial<AccessConsole> = {}): AccessConsole {
  return {
    instance_id: "11111111-1111-4111-8111-111111111111",
    instance_name: "qradar-lab",
    applicable: true,
    last_checked_at: CHECKED,
    check_running: false,
    last_check_started_at: "2026-11-03T09:14:20Z",
    last_check_finished_at: CHECKED,
    entries: ALL_OK,
    syncs_started: [],
    ...over,
  };
}

export function access(over: Partial<QRadarAccess> = {}): QRadarAccess {
  return { consoles: [consoleOf()], check_interval_seconds: 21600, ...over };
}

export function answer(over: Partial<AccessCheckAnswer> = {}): AccessCheckAnswer {
  return { ...access(), started: [], already_running: [], failed: [], ...over };
}

/** Three forbidden entries and one not found, the shape a read-only token leaves behind. */
export const REFUSED: AccessEntry[] = [
  entry({ id: "system_about", title: "System information", path: "/api/system/about" }),
  entry({
    id: "offenses",
    title: "Offenses",
    path: "/api/siem/offenses",
    feeds: "The offense snapshot.",
    used_by: ["offense snapshot collector"],
    status: "forbidden",
    http_status: 403,
    previous_status: "ok",
    capability_hint: "Offenses",
  }),
  entry({
    id: "custom_properties",
    title: "Custom event properties",
    path: "/api/config/event_sources/custom_properties/regex_properties",
    status: "forbidden",
    http_status: 403,
    capability_hint: "Log Activity, User Defined Event Properties",
    covers: [
      {
        method: "GET",
        path: "/api/config/event_sources/custom_properties/regex_properties/{id}",
        reason: "Reads the same collection by id.",
      },
    ],
  }),
  entry({
    id: "building_blocks",
    title: "Building blocks",
    path: "/api/analytics/building_blocks",
    status: "forbidden",
    http_status: 403,
    capability_hint: null,
  }),
  entry({
    id: "license_pool",
    title: "License pool",
    path: "/api/config/licensing/license_pool/license_allocations",
    status: "not_found",
    http_status: 404,
    capability_hint: "Admin",
  }),
  entry({
    id: "ariel_searches",
    title: "Ariel searches",
    path: "/api/ariel/searches",
    status: "unreachable",
    http_status: null,
  }),
  entry({ id: "log_sources", title: "Log sources" }),
];
