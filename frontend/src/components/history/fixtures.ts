// Builders for the history-backfill tests. The shapes are the HB-8 answers, not invented ones.

import type {
  HistoryConsole,
  HistoryConsoleEstimate,
  HistoryDecision,
  HistoryFamily,
  HistoryFamilyEstimate,
  HistoryScope,
} from "@/lib/api";

export const LAB_ID = "ddd41c44-ed49-44e3-9588-fb844583526c";
export const SECOND_ID = "eee41c44-ed49-44e3-9588-fb844583526d";

export function decision(over: Partial<HistoryDecision> = {}): HistoryDecision {
  return {
    answer: "YES",
    days: 28,
    decided_at: "2026-10-19T12:00:00Z",
    decided_by: "installer",
    source: "INSTALLER",
    updated_at: null,
    updated_by: null,
    start_requested_at: null,
    start_requested_by: null,
    stop_requested_at: null,
    stop_requested_by: null,
    ...over,
  };
}

export function scope(over: Partial<HistoryScope> = {}): HistoryScope {
  return {
    scope_key: "",
    state: "READING",
    days_requested: 28,
    days_target: 28,
    target_start: null,
    floor: "2026-10-01T00:00:00Z",
    floor_proved: false,
    through: "2026-10-01T04:45:00Z",
    anchor: null,
    started_at: "2026-10-19T12:00:00Z",
    finished_at: null,
    reason: null,
    last_error_class: null,
    last_error_at: null,
    searches: 12,
    buckets_read: 58,
    days_read: 9,
    gaps: null,
    sources_cut: null,
    sources_silent: null,
    sources_cut_count: null,
    sources_silent_count: null,
    progress_unreadable: false,
    later_yes: "possible",
    ...over,
  };
}

export function family(over: Partial<HistoryFamily> = {}): HistoryFamily {
  return {
    family: "PHASE_A",
    collection_enabled: true,
    collection_reason: "source-volume collection is the platform's base collection",
    window: {
      days_requested: 28,
      days: 28,
      available: true,
      limited_by: ["history_backfill_days"],
      reason: null,
    },
    scopes: [],
    sources: null,
    later_yes: "possible",
    ...over,
  };
}

export function consoleOf(over: Partial<HistoryConsole> = {}): HistoryConsole {
  return {
    instance_id: LAB_ID,
    name: "lab-console",
    decision: null,
    mode: "UNDECIDED",
    families: [
      family(),
      family({
        family: "WAF",
        collection_reason: "WAF_COLLECTION_ENABLED is on",
        sources: [
          { scope_key: "1", name: "web-1", profile_enabled: true, later_yes: "possible" },
        ],
        later_yes: null,
      }),
      family({
        family: "IPS",
        collection_enabled: false,
        collection_reason: "IPS_COLLECTION_ENABLED is off",
        sources: [],
        later_yes: null,
      }),
      family({ family: "CATCHALL", collection_reason: "CATCHALL_COLLECTION_ENABLED is on" }),
      family({
        family: "RULE_MATCH",
        collection_enabled: false,
        collection_reason: "RULE_MATCH_COLLECTION_ENABLED is off",
      }),
    ],
    message: null,
    ...over,
  };
}

export function estimateFamily(over: Partial<HistoryFamilyEstimate> = {}): HistoryFamilyEstimate {
  return {
    family: "PHASE_A",
    searches: 5,
    ticks: 1,
    hours: 0.1,
    days_requested: 28,
    days_fitted: 28,
    reduced: false,
    refused: false,
    reason: "the 28-day window fits in an estimated 0.1 hours",
    assumptions: [
      "5-minute buckets, 10 history searches per tick",
      "QRadar time per search not measured on this console",
    ],
    ...over,
  };
}

export function estimateOf(over: Partial<HistoryConsoleEstimate> = {}): HistoryConsoleEstimate {
  return {
    instance_id: LAB_ID,
    name: "lab-console",
    kind: "estimate",
    families: [
      estimateFamily(),
      estimateFamily({
        family: "WAF",
        searches: 2100,
        ticks: 216,
        hours: 72,
        days_fitted: 20,
        reduced: true,
        reason: "20 of 28 days fit in 72 hours",
      }),
      estimateFamily({
        family: "IPS",
        searches: null,
        ticks: null,
        hours: null,
        days_fitted: null,
        reason: "no estimate: collection is switched off (IPS_COLLECTION_ENABLED is off)",
        assumptions: [],
      }),
      estimateFamily({ family: "CATCHALL", searches: 21, hours: 0.2 }),
      estimateFamily({
        family: "RULE_MATCH",
        searches: null,
        ticks: null,
        hours: null,
        days_fitted: null,
        reason: "no estimate: collection is switched off (RULE_MATCH_COLLECTION_ENABLED is off)",
        assumptions: [],
      }),
    ],
    ...over,
  };
}
