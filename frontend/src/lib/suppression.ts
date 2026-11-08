// What the quiet-period UI says, and the arithmetic behind it (SOC-17d). Everything here is pure.
//
// A quiet period holds an alert back for 1, 7 or 30 days. The incident is still recorded. The
// server derives the condition (family, subject, detector) from the alert or anomaly; the UI only
// chooses the duration, reason, owner, scope and kind, and never sends a detector string.

import {
  ApiError,
  actionErrorMessage,
  type Alert,
  type Suppression,
  type SuppressionDays,
  type SuppressionFamily,
} from "@/lib/api";
import { missingPermission } from "@/lib/refusal";
import type { TextTranslator } from "@/i18n/textTranslator";

export const SUPPRESS_PERMISSION = "alert:suppress";

/** The only durations the backend accepts. There is no "forever": that is a RECORD_ONLY policy. */
export const DAYS: readonly SuppressionDays[] = [1, 7, 30];
export const REASON_MIN = 3;
export const REASON_MAX = 1000;
export const OWNER_MAX = 255;
export const REFERENCE_MAX = 255;
const DAY_MS = 86_400_000;

/** The families whose subject is a log source: only these may be quieted as a whole source. */
const SOURCE_ANCHORED: readonly SuppressionFamily[] = ["log_source", "waf", "ips"];

/** `alert.source_type` of the four anomaly producers, by family (the backend's `_ALERT_FAMILY`). */
const ALERT_FAMILY: Readonly<Record<string, SuppressionFamily>> = {
  log_source: "log_source",
  waf_endpoint: "waf",
  ips_entity: "ips",
  ips_source: "ips",
  pw_value: "property_watch",
};

/** The `enums` group that names a family's detectors. */
export function detectorGroup(family: SuppressionFamily): "anomalyType" | "propertyWatchAnomalyType" {
  return family === "property_watch" ? "propertyWatchAnomalyType" : "anomalyType";
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function allowsWholeSource(family: SuppressionFamily): boolean {
  return SOURCE_ANCHORED.includes(family);
}

/** The family an alert's condition belongs to, or null for an alert with no detector condition. */
export function alertFamily(alert: Pick<Alert, "source_type">): SuppressionFamily | null {
  return Object.hasOwn(ALERT_FAMILY, alert.source_type) ? ALERT_FAMILY[alert.source_type] : null;
}

/**
 * What the detail pages hand the quiet-period block: the family and the anomaly the server reads
 * the condition from, plus how to send the request. Null when the thing has no condition to quiet.
 */
export type SuppressTarget =
  | {
      via: "alert";
      alertId: string;
      family: SuppressionFamily;
      anomalyId: string;
      /** The detector the alert's own title names, when it carries one. */
      alertDetector: string | null;
    }
  | { via: "anomaly"; family: SuppressionFamily; anomalyId: string };

/** The detector value an anomaly alert's title params carry (`anomaly_type`, or `detector`). */
export function alertDetectorOf(alert: Pick<Alert, "title_params">): string | null {
  const params = alert.title_params;
  for (const key of ["anomaly_type", "detector"]) {
    const value = params?.[key];
    if (typeof value === "string" && value !== "") return value;
  }
  return null;
}

export function alertTarget(
  alert: Pick<Alert, "id" | "source_type" | "source_anomaly_ids" | "title_params">,
): SuppressTarget | null {
  const family = alertFamily(alert);
  const anomalyId = alert.source_anomaly_ids[0];
  if (family === null || !anomalyId) return null;
  return { via: "alert", alertId: alert.id, family, anomalyId, alertDetector: alertDetectorOf(alert) };
}

/** The exact grant, or the administrator wildcard. */
export function canSuppress(permissions: readonly string[]): boolean {
  return permissions.includes(SUPPRESS_PERMISSION) || permissions.includes("admin:*");
}

/** The whole days a window spans, rounded; at least 1. */
export function spanDays(row: Pick<Suppression, "starts_at" | "ends_at">): number | null {
  if (!row.starts_at || !row.ends_at) return null;
  const ms = Date.parse(row.ends_at) - Date.parse(row.starts_at);
  return Number.isFinite(ms) && ms > 0 ? Math.max(1, Math.round(ms / DAY_MS)) : null;
}

export type Until =
  | { unit: "past" }
  | { unit: "underMinute" }
  | { unit: "minutes" | "hours" | "days"; count: number };

/** How far an end lies ahead of `now`. Past or equal is "past"; an unreadable instant is null. */
export function untilEnd(endsAt: string | null | undefined, now: number): Until | null {
  if (!endsAt) return null;
  const end = Date.parse(endsAt);
  if (!Number.isFinite(end) || !Number.isFinite(now)) return null;
  const seconds = Math.floor((end - now) / 1000);
  if (seconds <= 0) return { unit: "past" };
  if (seconds < 60) return { unit: "underMinute" };
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return { unit: "minutes", count: minutes };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { unit: "hours", count: hours };
  return { unit: "days", count: Math.floor(hours / 24) };
}

/** The permission a 403 names: by code, or by the sentence "missing required permission: x". */
export function permissionOf(err: ApiError): string | null {
  const coded = missingPermission(err);
  if (coded) return coded;
  const named = /missing required permission:\s*([a-z_]+:[a-z_*]+)/.exec(err.message);
  return named ? named[1] : null;
}

/** The form fields a failed request can belong to. */
export type SuppressField = "reason" | "owner" | "reference" | "days";

export interface SuppressFailure {
  kind: "forbidden" | "error";
  text: string;
  /** Set when the message belongs next to a field of the form. */
  field: SuppressField | null;
  /** True for a 404: the alert, anomaly or quiet period is gone, so the page is stale. */
  gone: boolean;
}

const FIELDS: readonly SuppressField[] = ["reason", "owner", "reference", "days"];

/** The field a schema 422 names (its `loc` ends in the field), or null. */
export function fieldOf(err: ApiError): SuppressField | null {
  if (!Array.isArray(err.detail)) return null;
  for (const item of err.detail) {
    const loc = (item as { loc?: unknown } | null)?.loc;
    if (!Array.isArray(loc)) continue;
    const last = String(loc[loc.length - 1] ?? "");
    if ((FIELDS as readonly string[]).includes(last)) return last as SuppressField;
  }
  return null;
}

/** The sentence of a 422: a schema 422's messages (the client keeps only the status text), else the server's. */
export function sentenceOf(err: ApiError): string {
  if (Array.isArray(err.detail)) {
    const parts = err.detail
      .map((d) => (d && typeof (d as { msg?: unknown }).msg === "string" ? (d as { msg: string }).msg : null))
      .filter((m): m is string => m !== null)
      .map((m) => m.replace(/^Value error, /, ""));
    if (parts.length) return parts.join("; ");
  }
  return err.message;
}

/** What a failed quiet/end request says. A 422 keeps the server's sentence, next to its field. */
export function suppressFailure(err: unknown, say: TextTranslator): SuppressFailure {
  if (err instanceof ApiError) {
    if (err.status === 404) {
      return { kind: "error", text: say("suppression.failure.gone"), field: null, gone: true };
    }
    if (err.status === 403) {
      const permission = permissionOf(err) ?? SUPPRESS_PERMISSION;
      return {
        kind: "forbidden",
        text: say("suppression.failure.forbidden", { permission }),
        field: null,
        gone: false,
      };
    }
    if (err.status === 422 && sentenceOf(err)) {
      return { kind: "error", text: sentenceOf(err), field: fieldOf(err), gone: false };
    }
  }
  return {
    kind: "error",
    text: say("suppression.failure.notSaved", { reason: actionErrorMessage(err, say) }),
    field: null,
    gone: false,
  };
}

/** What a failed read says: the permission when the server named one. */
export type ReadFailure =
  | { kind: "forbidden"; permission: string }
  | { kind: "error"; text: string };

export function readFailure(err: unknown, say: TextTranslator, fallback: string): ReadFailure {
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", permission: permissionOf(err) ?? fallback };
  }
  return { kind: "error", text: actionErrorMessage(err, say) };
}
