// What the alert policy page says, and the arithmetic behind it. Everything here is pure.
//
// The rule the page lives by (SOC-17 design §4): every source alerts unless a row says otherwise.
// A row names a scope (one source or a whole class), a family, a detector (or every detector of
// the family) and a mode. The most specific row wins; the table is a list of exceptions.

import {
  ApiError,
  actionErrorMessage,
  type AlertPolicyFamily,
  type AlertPolicyMode,
  type AlertPolicyRow,
} from "@/lib/api";
import type { TextTranslator } from "@/i18n/textTranslator";

export const FAMILIES: readonly AlertPolicyFamily[] = ["log_source", "waf", "ips", "property_watch"];
export const MODES: readonly AlertPolicyMode[] = ["ALERT", "RECORD_ONLY", "DIGEST"];

/** The Phase A detectors, as the backend's `AnomalyType` lists them (the API validates the choice). */
const LOG_SOURCE_TYPES: readonly string[] = [
  "NO_EVENTS",
  "VOLUME_DROP",
  "VOLUME_SPIKE",
  "PARSING_DEGRADATION",
  "UNKNOWN_EVENT_SPIKE",
  "TIMESTAMP_DELAY",
  "CARDINALITY_DROP",
  "COLLECTION_ERROR",
  "REPEATED_PAYLOAD",
];

/** The WAF detectors, as `WafAnomalyType` lists them. */
const WAF_DETECTOR_TYPES: readonly string[] = [
  "WAF_REQUEST_RATE_SPIKE",
  "WAF_CLIENT_CONCENTRATION_SPIKE",
  "WAF_BLOCK_RATIO_SPIKE",
  "WAF_ERROR_RATIO_SPIKE",
  "WAF_CLIENT_ERROR_RATIO_SPIKE",
  "WAF_NEW_ENDPOINT_ACCESS",
  "WAF_CLIENT_ENDPOINT_FANOUT_SPIKE",
];

/** The IPS detectors, as `IpsAnomalyType` lists them. */
const IPS_DETECTOR_TYPES: readonly string[] = [
  "IPS_PORT_RATE_SPIKE",
  "IPS_DESTINATION_RATE_SPIKE",
  "IPS_SIGNATURE_BURST",
  "IPS_NEW_DESTINATION_PORT",
  "IPS_SOURCE_CONCENTRATION_SPIKE",
  "IPS_PORT_FANOUT_SPIKE",
  "IPS_BLOCK_RATIO_SHIFT",
];

/** The property-watch detectors, as `PwAnomalyType` lists them. */
const PW_DETECTOR_TYPES: readonly string[] = [
  "PW_VALUE_SPIKE",
  "PW_VALUE_DROP",
  "PW_VALUE_DISAPPEARED",
  "PW_NEW_VALUE",
  "PW_NEW_COMBINATION",
];

/** The detectors a family can have a policy for. Never a list typed into the markup. */
export function detectorsOf(family: AlertPolicyFamily): readonly string[] {
  switch (family) {
    case "log_source":
      return LOG_SOURCE_TYPES;
    case "waf":
      return WAF_DETECTOR_TYPES;
    case "ips":
      return IPS_DETECTOR_TYPES;
    case "property_watch":
      return PW_DETECTOR_TYPES;
  }
}

/** The `enums` group that names a family's detectors. */
export function detectorGroup(family: AlertPolicyFamily): "anomalyType" | "propertyWatchAnomalyType" {
  return family === "property_watch" ? "propertyWatchAnomalyType" : "anomalyType";
}

/** A property watch has no class scope; every other family has both. */
export function allowsClass(family: AlertPolicyFamily): boolean {
  return family !== "property_watch";
}

/** The key of a row's identity: the same five fields the API upserts on. */
export function identityKey(
  row: Pick<
    AlertPolicyRow,
    "instance_id" | "scope_kind" | "scope_value" | "family" | "detector_type"
  >,
): string {
  return [row.instance_id ?? "", row.scope_kind, row.scope_value, row.family, row.detector_type ?? ""].join("|");
}

/**
 * A NO_EVENTS silence is first judged by the outage policy. A row that can reach it (every detector
 * of the log-source family, or NO_EVENTS itself) says so in place.
 */
export function touchesOutage(row: Pick<AlertPolicyRow, "family" | "detector_type">): boolean {
  return row.family === "log_source" && (row.detector_type === null || row.detector_type === "NO_EVENTS");
}

/** True when the row's source or watch has left the inventory. */
export function isGone(row: Pick<AlertPolicyRow, "scope_kind" | "source_name">): boolean {
  return row.scope_kind === "SOURCE" && row.source_name === null;
}

/**
 * Display order: SOURCE rows before CLASS rows, then by name (a class by its value), then family,
 * detector (all detectors first) and console (all consoles first). A copy; the input is not touched.
 */
export function sortRows(rows: readonly AlertPolicyRow[]): AlertPolicyRow[] {
  const name = (r: AlertPolicyRow) => (r.source_name ?? r.scope_value).toLowerCase();
  return [...rows].sort(
    (a, b) =>
      (a.scope_kind === b.scope_kind ? 0 : a.scope_kind === "SOURCE" ? -1 : 1) ||
      name(a).localeCompare(name(b)) ||
      a.scope_value.localeCompare(b.scope_value) ||
      FAMILIES.indexOf(a.family) - FAMILIES.indexOf(b.family) ||
      (a.detector_type ?? "").localeCompare(b.detector_type ?? "") ||
      (a.instance_name ?? "").localeCompare(b.instance_name ?? "") ||
      (a.instance_id ?? "").localeCompare(b.instance_id ?? ""),
  );
}

/** The consoles the rows name, for the editor's console choice. A global row names none. */
export function knownConsoles(rows: readonly AlertPolicyRow[]): { id: string; name: string | null }[] {
  const seen = new Map<string, string | null>();
  for (const r of rows) {
    if (r.instance_id !== null && !seen.has(r.instance_id)) seen.set(r.instance_id, r.instance_name);
  }
  return [...seen].map(([id, name]) => ({ id, name }));
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

// --- failures ------------------------------------------------------------------

/** The form field a 422 sentence belongs at. */
export type PolicyField = "scope" | "detector" | "console";

/** Which field the server's validation sentence names; null when it names none. */
export function fieldOf(message: string): PolicyField | null {
  if (/^unknown detector/.test(message)) return "detector";
  if (/^unknown instance/.test(message)) return "console";
  if (/^(scope_value|unknown source class|property watch has no class)/.test(message)) return "scope";
  return null;
}

export interface PolicyFailure {
  kind: "forbidden" | "error";
  text: string;
  /** Set when the message belongs at a field of the editor. */
  field?: PolicyField | null;
  /** True for a 404 on a delete: the row is already gone, so the list is stale. */
  gone: boolean;
}

/** What a failed save says. A 422 shows the server's sentence, which names the field. */
export function saveFailure(err: unknown, say: TextTranslator): PolicyFailure {
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", text: say("alertPolicy.editor.forbidden"), gone: false };
  }
  if (err instanceof ApiError && err.status === 422 && err.message) {
    return { kind: "error", text: err.message, field: fieldOf(err.message), gone: false };
  }
  return {
    kind: "error",
    text: say("alertPolicy.editor.notSaved", { reason: actionErrorMessage(err, say) }),
    gone: false,
  };
}

export function removeFailure(err: unknown, say: TextTranslator): PolicyFailure {
  if (err instanceof ApiError && err.status === 404) {
    return { kind: "error", text: say("alertPolicy.flash.gone"), gone: true };
  }
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", text: say("alertPolicy.remove.forbidden"), gone: false };
  }
  return {
    kind: "error",
    text: say("alertPolicy.remove.notRemoved", { reason: actionErrorMessage(err, say) }),
    gone: false,
  };
}

/** The permission check the page uses: the exact grant, or the administrator wildcard. */
export function holds(permissions: readonly string[], grant: string): boolean {
  return permissions.includes(grant) || permissions.includes("admin:*");
}
