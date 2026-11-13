// Presentation rules for the identity family (SOC-10f). Pure functions.
//
// Honesty rules, each pinned by a test:
//   * a count that was not measured is the dash, never 0; a measured 0 stays "0";
//   * a sum over buckets is exact only when every bucket in the window was measured whole: a
//     truncated or uncollected bucket makes it "at least", and a window with no measured bucket
//     has no sum at all;
//   * a username is only what a stored row holds, and is attacker-controlled text: callers render
//     it as text.

import type {
  IdentityBucketSummary,
  IdentityCollection,
  IdentityProfile,
  IdentitySeriesPoint,
  IdentitySource,
  AnomalySummary,
} from "@/lib/api";
import { formatMetric } from "@/lib/behavior";
import { wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { formatCount, type FormatLocale } from "@/lib/format";

export const MISSING = "—";

/** The grant for the profile writes. Kept here, not in the client panel: a server page that imports
 *  a value from a client module receives a reference, not the string. */
export const ADMIN_PERMISSION = "admin:identity";

/** Fixed Active Directory catalogue (backend `IDENTITY_EVENT_CATALOG`), in the design order. */
export const IDENTITY_EVENTS = [4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102] as const;

/** 4625 and 4740 store a username only at this many events in a bucket. */
export const VOLUME_EVENTS: readonly number[] = [4625, 4740];
export const VOLUME_NAME_FLOOR = 3;
/** Usernames kept per source, event ID and bucket. */
export const TOP_N = 20;

export const IDENTITY_TYPES = [
  "IDENTITY_AUDIT_LOG_CLEARED",
  "IDENTITY_PRIVILEGED_GROUP_ADD",
  "IDENTITY_NEW_PRIVILEGED_ACCOUNT",
  "IDENTITY_FAILED_LOGON_SPIKE",
  "IDENTITY_LOCKOUT_SPIKE",
  "IDENTITY_ACCOUNT_CREATED",
  "IDENTITY_ACCOUNT_DELETED",
] as const;

export function isIdentityType(type: string | null | undefined): boolean {
  return type != null && (IDENTITY_TYPES as readonly string[]).includes(type);
}

/** An identity detector's name; a type this build does not know shows as sent. */
export function identityTypeLabel(type: string, i18n: BehaviorI18n): string {
  return isIdentityType(type) ? wordOrValue(i18n, "enums.identityAnomalyType", type) : type;
}

export function isIdentityRow(a: Pick<AnomalySummary, "subject_kind" | "identity">): boolean {
  return a.subject_kind === "IDENTITY_SOURCE" && a.identity != null;
}

/** A catalogue event ID as the message catalog keys it. */
export type EventKey = `${(typeof IDENTITY_EVENTS)[number]}`;

export function eventKey(eventId: number): EventKey {
  return String(eventId) as EventKey;
}

export function isVolumeEvent(eventId: number): boolean {
  return VOLUME_EVENTS.includes(eventId);
}

/** The catalogue event the catalogue message set names, or null for an ID outside the catalogue. */
export function isCatalogueEvent(eventId: number): boolean {
  return (IDENTITY_EVENTS as readonly number[]).includes(eventId);
}

/** Events per bucket: whole numbers as counts, a fractional expectation to one decimal. */
export function formatEvents(value: number | null | undefined, locale: FormatLocale = "en"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  return Number.isInteger(value) ? formatCount(value, locale) : formatMetric(value, 1, locale);
}

// --- one bucket's honesty state ------------------------------------------------------------

/** What a bucket (or a source's newest bucket) says about the figures in it. */
export type CollectionState = "COMPLETE" | "PARTIAL" | "TRUNCATED" | "NOT_COLLECTED";

/**
 * The state of one stored bucket summary. A source with no stored bucket was never collected.
 * `PARTIAL`: the counts are whole and the name search was cut, so the names are not the whole top.
 * An unknown measurement word is not trusted as whole.
 */
export function bucketState(bucket: IdentityBucketSummary | null): CollectionState {
  if (bucket === null) return "NOT_COLLECTED";
  if (bucket.measurement === "NOT_COLLECTED") return "NOT_COLLECTED";
  if (bucket.measurement === "TRUNCATED") return "TRUNCATED";
  if (bucket.measurement !== "EXACT") return "NOT_COLLECTED";
  return bucket.admission_truncated ? "PARTIAL" : "COMPLETE";
}

/** Why a source is not being collected at all (its profile), or null when it is eligible. */
export type NotCollectedWhy = "disabled" | "unvalidated" | "stale" | null;

export function notCollectedWhy(profile: IdentityProfile): NotCollectedWhy {
  if (!profile.enabled) return "disabled";
  if (profile.validation_status !== "VALID") return "unvalidated";
  if (profile.validated_profile_version !== profile.profile_version) return "stale";
  return null;
}

/** Whether the collector has failed on its last runs (a null count is "no watermark yet"). */
export function isFailing(c: IdentityCollection): boolean {
  return c.consecutive_failures !== null && c.consecutive_failures > 0;
}

/** Sources that need attention first: failing, then not collected, truncated, partial, complete. */
const ATTENTION: Record<CollectionState, number> = {
  NOT_COLLECTED: 1,
  TRUNCATED: 2,
  PARTIAL: 3,
  COMPLETE: 4,
};

export function attentionRank(source: IdentitySource): number {
  if (isFailing(source.collection)) return 0;
  if (notCollectedWhy(source.profile) !== null) return 1;
  return ATTENTION[bucketState(source.last_bucket)];
}

export function sortByAttention(sources: IdentitySource[]): IdentitySource[] {
  return [...sources].sort(
    (a, b) =>
      attentionRank(a) - attentionRank(b) ||
      a.profile.log_source_name.localeCompare(b.profile.log_source_name),
  );
}

// --- counts over a window -------------------------------------------------------------------

export interface EventTotal {
  eventId: number;
  /** The sum of the buckets measured whole; null when no bucket was measured at all. */
  total: number | null;
  /** True when a bucket in the window was truncated or not collected: the sum is "at least". */
  atLeast: boolean;
  /** Buckets in the window that carry no whole count (truncated, not collected or missing). */
  unmeasured: number;
  /** Buckets the window spans. */
  slots: number;
}

/** The measurement of a series point, never trusting an unknown word as whole. */
export function pointMeasurement(p: IdentitySeriesPoint): "EXACT" | "TRUNCATED" | "NOT_COLLECTED" {
  if (p.measurement === "EXACT" && p.event_count !== null) return "EXACT";
  if (p.measurement === "TRUNCATED") return "TRUNCATED";
  return "NOT_COLLECTED";
}

/** How many buckets of `bucketSeconds` a window spans (at least one). */
export function slotsIn(startMs: number, endMs: number, bucketSeconds: number): number {
  if (!(bucketSeconds > 0) || !(endMs > startMs)) return 0;
  return Math.max(1, Math.round((endMs - startMs) / (bucketSeconds * 1000)));
}

/**
 * Per-event totals over the stored series of one source. A bucket with no stored point is
 * unmeasured, not a zero: `slots - measured` buckets are unmeasured, so a window the collector
 * did not cover can never read as a quiet one. A truncated point adds its floor and flags it.
 */
export function eventTotals(
  series: IdentitySeriesPoint[],
  eventIds: readonly number[],
  slots: number,
): EventTotal[] {
  return eventIds.map((eventId) => {
    const points = series.filter((p) => p.event_id === eventId);
    let sum = 0;
    let whole = 0;
    let floor = false;
    for (const p of points) {
      const kind = pointMeasurement(p);
      if (kind === "EXACT") {
        sum += p.event_count as number;
        whole += 1;
      } else if (kind === "TRUNCATED" && p.event_count !== null) {
        sum += p.event_count;
        floor = true;
      }
    }
    const unmeasured = Math.max(0, slots - whole);
    const anyFloor = floor || unmeasured > 0;
    // Nothing measured whole and no floor to report: there is no figure, not a zero.
    const total = whole === 0 && !floor ? null : sum;
    return { eventId, total, atLeast: total !== null && anyFloor, unmeasured, slots };
  });
}

// --- the stored names over a window ---------------------------------------------------------

export interface NameRow {
  username: string;
  /** The sum of the stored counts: events in the buckets where this name was kept. */
  stored: number;
  buckets: number;
  /** The stored text was cut at the stored length in at least one bucket. */
  cut: boolean;
}

/**
 * The stored names of one event ID over a window, by stored events then name. A name that fell
 * below the bucket's top-N or floor is absent from that bucket, so `stored` is a lower bound of
 * the events behind the name, never a total.
 */
export function namesOf(series: IdentitySeriesPoint[], eventId: number): NameRow[] {
  const byName = new Map<string, NameRow>();
  for (const p of series) {
    if (p.event_id !== eventId) continue;
    for (const n of p.usernames) {
      const row = byName.get(n.username) ?? { username: n.username, stored: 0, buckets: 0, cut: false };
      row.stored += n.count;
      row.buckets += 1;
      row.cut = row.cut || n.truncated;
      byName.set(n.username, row);
    }
  }
  return [...byName.values()].sort(
    (a, b) => b.stored - a.stored || (a.username < b.username ? -1 : a.username > b.username ? 1 : 0),
  );
}

/** Whether any bucket of the event ID in the window had its name list cut or not collected. */
export function namesIncomplete(series: IdentitySeriesPoint[], eventId: number): boolean {
  return series.some(
    (p) =>
      p.event_id === eventId &&
      (p.names_measurement === "TRUNCATED" ||
        p.names_measurement === "NOT_COLLECTED" ||
        p.admission_truncated === true),
  );
}

// --- the window ---------------------------------------------------------------------------

export const RANGES = ["1h", "6h", "24h", "7d"] as const;
export type IdentityRange = (typeof RANGES)[number];
export const DEFAULT_RANGE: IdentityRange = "24h";
const RANGE_MS: Record<IdentityRange, number> = {
  "1h": 3_600_000,
  "6h": 6 * 3_600_000,
  "24h": 24 * 3_600_000,
  "7d": 7 * 24 * 3_600_000,
};

export function parseRange(value: string | string[] | undefined): IdentityRange {
  const first = Array.isArray(value) ? value[0] : value;
  return (RANGES as readonly string[]).includes(first ?? "") ? (first as IdentityRange) : DEFAULT_RANGE;
}

/** The window ending at `nowMs`, aligned to the bucket grid so each slot is a whole bucket. */
export function windowOf(
  range: IdentityRange,
  nowMs: number,
  bucketSeconds = 300,
): { start: string; end: string; startMs: number; endMs: number } {
  const grid = bucketSeconds * 1000;
  const endMs = Math.floor(nowMs / grid) * grid;
  const startMs = endMs - RANGE_MS[range];
  return { start: new Date(startMs).toISOString(), end: new Date(endMs).toISOString(), startMs, endMs };
}

/** The address of an identity page: only what differs from the defaults is written. */
export function identityHref(
  path: string,
  parts: { range?: IdentityRange; event?: number | null },
): string {
  const pairs: string[] = [];
  if (parts.range && parts.range !== DEFAULT_RANGE) pairs.push(`range=${parts.range}`);
  if (parts.event != null) pairs.push(`event=${parts.event}`);
  return pairs.length ? `${path}?${pairs.join("&")}` : path;
}

/** An event ID read from a URL: a catalogue ID or null. */
export function parseEvent(value: string | string[] | undefined): number | null {
  const first = Array.isArray(value) ? value[0] : value;
  const n = Number(first);
  return Number.isInteger(n) && isCatalogueEvent(n) ? n : null;
}

// --- open incidents per source ---------------------------------------------------------------

export const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;

export interface IncidentCounts {
  total: number;
  bySeverity: Array<{ severity: string; count: number }>;
}

/** The active incidents of each source, grouped by severity in the fixed severity order. */
export function incidentsBySource(items: AnomalySummary[]): Map<string, IncidentCounts> {
  const out = new Map<string, Map<string, number>>();
  for (const a of items) {
    if (!a.log_source_id) continue;
    const inner = out.get(a.log_source_id) ?? new Map<string, number>();
    inner.set(a.severity, (inner.get(a.severity) ?? 0) + 1);
    out.set(a.log_source_id, inner);
  }
  const result = new Map<string, IncidentCounts>();
  for (const [id, inner] of out) {
    const known = SEVERITY_ORDER.filter((s) => inner.has(s)).map((s) => ({
      severity: s as string,
      count: inner.get(s) as number,
    }));
    const other = [...inner.keys()]
      .filter((s) => !(SEVERITY_ORDER as readonly string[]).includes(s))
      .map((s) => ({ severity: s, count: inner.get(s) as number }));
    const bySeverity = [...known, ...other];
    result.set(id, { total: bySeverity.reduce((n, x) => n + x.count, 0), bySeverity });
  }
  return result;
}
