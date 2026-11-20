// The pure parts of Admin → Features: which control a family gets, what a state change sends,
// where a switch's value came from, what the token reaches and which settings problem belongs to
// which row. Nothing here reads a catalog or the network.

import { isRefused } from "@/components/qradaraccess/access";
import type { FeatureRow, FeatureSwitchView } from "@/lib/api";

export type FamilyState = "off" | "shadow" | "alerting";

/** `state`: collection plus alerting as Off / Shadow / Alerting. `toggles`: plain switches. */
export type Layout =
  | { kind: "state"; collection: string; alerting: string; extras: string[] }
  | { kind: "toggles"; keys: string[] };

const state = (collection: string, alerting: string, extras: string[] = []): Layout => ({
  kind: "state",
  collection,
  alerting,
  extras,
});

export const LAYOUT: Readonly<Record<string, Layout>> = {
  waf: state("waf_collection_enabled", "waf_alerting_enabled", [
    "waf_outcome_collection_enabled",
    "waf_client_collection_enabled",
  ]),
  ips: state("ips_collection_enabled", "ips_alerting_enabled"),
  identity: state("identity_collection_enabled", "identity_alerting_enabled"),
  rule_match: state("rule_match_collection_enabled", "rule_alerting_enabled"),
  property_watch: state("pw_collection_enabled", "pw_alerting_enabled"),
  custom_rules: { kind: "toggles", keys: ["custom_rules_enabled", "custom_rules_alerting_enabled"] },
  catchall: {
    kind: "toggles",
    keys: ["catchall_collection_enabled", "catchall_new_sender_alerts_enabled"],
  },
  geo: { kind: "toggles", keys: ["geo_collection_enabled"] },
  qradar_health: { kind: "toggles", keys: ["qradar_health_collection_enabled"] },
  explanations: { kind: "toggles", keys: ["explanation_enabled"] },
  inventory: { kind: "toggles", keys: ["inventory_new_source_alerts_enabled"] },
  volume: { kind: "toggles", keys: [] },
};

/** The page that owns each family's data. */
export const DATA_PAGE: Readonly<Record<string, string>> = {
  waf: "/behavior/waf",
  ips: "/behavior/ips",
  identity: "/behavior/identity",
  rule_match: "/rules",
  property_watch: "/behavior/watches",
  custom_rules: "/custom-rules",
  catchall: "/fleet/catchall",
  geo: "/geography",
  qradar_health: "/admin/siem-load",
  explanations: "/anomalies",
  inventory: "/log-sources",
  volume: "/behavior/sources",
};

/** Families whose load is not an Ariel cadence: the API reports them NOT_COLLECTED by design. */
const NO_ESTIMATE = new Set(["custom_rules", "explanations", "inventory"]);

/** A family the API returns that this build does not know gets its own switches as toggles. */
export function layoutOf(row: FeatureRow): Layout {
  return LAYOUT[row.family] ?? { kind: "toggles", keys: Object.keys(row.switches) };
}

export function stateOf(row: FeatureRow, layout: Extract<Layout, { kind: "state" }>): FamilyState {
  if (!row.switches[layout.collection]?.enabled) return "off";
  return row.switches[layout.alerting]?.enabled ? "alerting" : "shadow";
}

export interface Step {
  key: string;
  enabled: boolean;
}

/** The writes a state change takes. Alerting from Off turns collection on first: the API refuses the reverse. */
export function stepsFor(
  from: FamilyState,
  to: FamilyState,
  layout: Extract<Layout, { kind: "state" }>,
): Step[] {
  if (from === to) return [];
  if (to === "off") return [{ key: layout.collection, enabled: false }];
  if (to === "shadow") {
    return from === "alerting"
      ? [{ key: layout.alerting, enabled: false }]
      : [{ key: layout.collection, enabled: true }];
  }
  return from === "off"
    ? [
        { key: layout.collection, enabled: true },
        { key: layout.alerting, enabled: true },
      ]
    : [{ key: layout.alerting, enabled: true }];
}

/** Leaving a state that collects or alerts needs a second click. */
export function needsConfirm(from: FamilyState, to: FamilyState): boolean {
  return to === "off" || (from === "alerting" && to === "shadow");
}

/** Why a toggle cannot be switched on: the switch it needs is off. Null when nothing blocks it. */
export function blockedBy(row: FeatureRow, key: string): string | null {
  const needs = row.switches[key]?.requires;
  return needs && row.switches[needs]?.enabled === false ? needs : null;
}

export interface SourceLine {
  source: "env" | "default" | "db";
  by: string | null;
  at: string | null;
}

/** The origin of a family's switches: the newest stored change, else each distinct `.env` or default origin. */
export function sourceLines(row: FeatureRow): SourceLine[] {
  const views: FeatureSwitchView[] = Object.values(row.switches);
  const lines: SourceLine[] = [];
  const stored = views
    .filter((v) => v.source === "db")
    .sort((a, b) => Date.parse(b.updated_at ?? "") - Date.parse(a.updated_at ?? ""))[0];
  if (stored) lines.push({ source: "db", by: stored.updated_by, at: stored.updated_at });
  // A stored change stands for the family: the rest follow `.env` or the default, which the guide says.
  if (stored) return lines;
  for (const source of ["env", "default"] as const) {
    if (views.some((v) => v.source === source)) lines.push({ source, by: null, at: null });
  }
  return lines;
}

/** The keys a reset touches, alerting before collection: the API refuses the other order. */
export function storedKeys(row: FeatureRow): string[] {
  return Object.keys(row.switches)
    .filter((key) => row.switches[key]?.source === "db")
    .reverse();
}

export type Load =
  | { kind: "bound"; perHour: number; count: number; unit: string; measured: number | null }
  | { kind: "insufficient" }
  | { kind: "none"; unit: string }
  | { kind: "notEstimated" };

/** The Ariel load cell: a bound with its count, or the reason there is none. Never a zero. */
export function loadOf(row: FeatureRow): Load {
  const { estimate } = row;
  if (NO_ESTIMATE.has(row.family) && estimate.upper_bound_searches_per_hour === null) {
    return { kind: "notEstimated" };
  }
  if (estimate.upper_bound_searches_per_hour !== null) {
    return {
      kind: "bound",
      perHour: estimate.upper_bound_searches_per_hour,
      count: estimate.count,
      unit: estimate.count_unit,
      measured: row.measured_last_hour,
    };
  }
  if (estimate.enabled_consoles === 0) return { kind: "none", unit: "enabled_consoles" };
  if (row.cadence_seconds === null) return { kind: "insufficient" };
  return { kind: "none", unit: estimate.count_unit };
}

/** Whether any of the family's collection switches is on. */
export function isCollecting(row: FeatureRow, layout: Layout): boolean {
  return layout.kind === "state"
    ? row.switches[layout.collection]?.enabled === true
    : layout.keys.some((k) => row.switches[k]?.enabled === true);
}

/** A collecting family whose last run is older than two cadences. */
export function isStale(row: FeatureRow, layout: Layout, now: Date): boolean {
  if (!row.last_run || row.cadence_seconds === null) return false;
  const age = (now.getTime() - Date.parse(row.last_run.at)) / 1000;
  return isCollecting(row, layout) && Number.isFinite(age) && age > 2 * row.cadence_seconds;
}

export type Needs =
  | { kind: "adminOnly" }
  | { kind: "nothing" }
  | { kind: "noConsole" }
  | { kind: "reachable" }
  | { kind: "refused"; titles: string[] }
  | { kind: "notAnswered" }
  | { kind: "notChecked" };

/** What the stored access check says about the APIs a family reads. Advisory: it never blocks a switch. */
export function needsOf(row: FeatureRow): Needs {
  if (row.access === null) return { kind: "adminOnly" };
  if (row.access.length === 0) return { kind: "noConsole" };
  const entries = row.access.flatMap((c) => c.entries);
  if (entries.length === 0) return { kind: "nothing" };
  const refused = entries.filter(isRefused).map((e) => e.title);
  if (refused.length > 0) return { kind: "refused", titles: [...new Set(refused)] };
  if (entries.some((e) => e.status === "unreachable" || e.status === "error")) {
    return { kind: "notAnswered" };
  }
  if (entries.some((e) => e.status === null)) return { kind: "notChecked" };
  return { kind: "reachable" };
}

const PROBLEM_FAMILY: readonly (readonly [string, string])[] = [
  ["WAF_", "waf"],
  ["IPS_", "ips"],
  ["RULE_MATCH_", "rule_match"],
  ["PW_", "property_watch"],
];

/** Settings problems by the family their setting name starts with; the rest stay page-level. */
export function groupProblems(problems: readonly string[]): {
  byFamily: Record<string, string[]>;
  other: string[];
} {
  const byFamily: Record<string, string[]> = {};
  const other: string[] = [];
  for (const text of problems) {
    const hit = PROBLEM_FAMILY.find(([prefix]) => text.startsWith(prefix));
    if (hit) (byFamily[hit[1]] ??= []).push(text);
    else other.push(text);
  }
  return { byFamily, other };
}

/** The server's own words for a refused write: `detail.message` when it sent an object, else the text. */
export function refusalMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail !== "") return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message !== "") return message;
  }
  return fallback;
}
