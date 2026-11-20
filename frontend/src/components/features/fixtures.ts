import type { AccessEntry, FeatureOverview, FeatureRow, FeatureSwitchView } from "@/lib/api";

export const NOW = new Date("2026-11-05T19:05:00Z");

export function sw(enabled: boolean, over: Partial<FeatureSwitchView> = {}): FeatureSwitchView {
  return {
    enabled,
    source: "default",
    updated_at: null,
    updated_by: null,
    requires: null,
    inert: false,
    ...over,
  };
}

export function stored(enabled: boolean, by = "a.rivera", over: Partial<FeatureSwitchView> = {}) {
  return sw(enabled, { source: "db", updated_by: by, updated_at: "2026-11-05T17:05:00Z", ...over });
}

export function entry(over: Partial<AccessEntry> = {}): AccessEntry {
  return {
    id: "ariel_searches",
    title: "Log Activity",
    method: "GET",
    path: "/",
    feeds: "",
    used_by: [],
    status: "ok",
    http_status: 200,
    checked_at: null,
    previous_status: null,
    syncs_started: [],
    required_capability: null,
    capability_verified: false,
    capability_hint: null,
    sources: [],
    covers: [],
    ...over,
  };
}

export function row(family: string, over: Partial<FeatureRow> = {}): FeatureRow {
  return {
    family,
    switches: {},
    prerequisites: null,
    estimate: {
      upper_bound_searches_per_hour: 120,
      reason: null,
      count: 1,
      count_unit: "enabled_consoles",
      enabled_consoles: 1,
    },
    measured_last_hour: 40,
    measured_reason: null,
    cadence_seconds: 900,
    last_run: { at: "2026-11-05T18:55:00Z", outcome: "ok", searches: 4, buckets: 2 },
    last_run_reason: null,
    access: [{ instance_id: "i1", entries: [entry()] }],
    ...over,
  };
}

const NEVER = { last_run: null, last_run_reason: "NOT_COLLECTED" } as const;

/** A small console: one family of each control shape, plus the honest-absence cases. */
export function overview(over: Partial<FeatureOverview> = {}): FeatureOverview {
  return {
    runtime_switches_active: true,
    worker_switches_active: true,
    reason: null,
    settings_problems: [],
    families: [
      row("waf", {
        switches: {
          waf_collection_enabled: stored(true),
          waf_alerting_enabled: sw(false, { requires: "waf_collection_enabled" }),
          waf_outcome_collection_enabled: sw(true, { requires: "waf_collection_enabled" }),
          waf_client_collection_enabled: sw(false, { requires: "waf_collection_enabled" }),
        },
        prerequisites: { total: 16, enabled: 14, validated: 13 },
        estimate: {
          upper_bound_searches_per_hour: 560,
          reason: null,
          count: 14,
          count_unit: "enabled_profiles",
          enabled_consoles: 1,
        },
        measured_last_hour: 531,
        access: [{ instance_id: "i1", entries: [entry({ id: "cp", title: "Custom event properties", status: "forbidden" })] }],
      }),
      row("ips", {
        switches: {
          ips_collection_enabled: stored(true, "j.okafor"),
          ips_alerting_enabled: stored(true, "j.okafor", { requires: "ips_collection_enabled" }),
        },
        last_run: { at: "2026-11-05T16:05:00Z", outcome: "ok", searches: 18, buckets: 3 },
      }),
      row("identity", {
        switches: {
          identity_collection_enabled: sw(true, { source: "env" }),
          identity_alerting_enabled: sw(false, { source: "env", requires: "identity_collection_enabled" }),
        },
        ...NEVER,
        measured_last_hour: null,
        measured_reason: "NOT_COLLECTED",
        access: [{ instance_id: "i1", entries: [entry({ status: null })] }],
      }),
      row("rule_match", {
        switches: {
          rule_match_collection_enabled: sw(false, { source: "env" }),
          rule_alerting_enabled: stored(true, "a.rivera", {
            requires: "rule_match_collection_enabled",
            inert: true,
          }),
        },
      }),
      row("catchall", {
        switches: {
          catchall_collection_enabled: sw(false),
          catchall_new_sender_alerts_enabled: sw(false, { requires: "catchall_collection_enabled" }),
        },
        cadence_seconds: null,
        estimate: {
          upper_bound_searches_per_hour: null,
          reason: "NOT_COLLECTED",
          count: 1,
          count_unit: "enabled_consoles",
          enabled_consoles: 1,
        },
        ...NEVER,
      }),
      row("custom_rules", {
        switches: { custom_rules_enabled: sw(true), custom_rules_alerting_enabled: sw(true, { requires: "custom_rules_enabled" }) },
        prerequisites: { total: 5, enabled: 5 },
        cadence_seconds: null,
        estimate: {
          upper_bound_searches_per_hour: null,
          reason: "NOT_COLLECTED",
          count: 5,
          count_unit: "enabled_rules",
          enabled_consoles: 1,
        },
        access: [{ instance_id: "i1", entries: [] }],
        ...NEVER,
      }),
      row("volume", { switches: {} }),
    ],
    ...over,
  };
}
