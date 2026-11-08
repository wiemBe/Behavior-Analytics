// The triage queue's URL state and the pure decisions around the "last visit" marker (SOC-4c).
//
// The URL is the view: scope, kind, family, severity, "new only", the comparison chip and the page
// offset all live in it, so a link reproduces what the sender saw. Nothing is trusted: an unknown
// value is dropped and the page says so through `ignored`.

import type { QueueFamily, QueueKind, QueuePage, QueueScope, QueueSeverity } from "@/lib/api";

type Raw = string | string[] | undefined;

export const PAGE_SIZE = 50;
/** The backend refuses `offset + limit` above this (`app/api/routes/queue.py`). */
export const QUEUE_CAP = 1000;

export const QUEUE_SCOPES: readonly QueueScope[] = ["mine", "unassigned", "all"];
export const QUEUE_KINDS: readonly QueueKind[] = [
  "anomaly",
  "alert",
  "offense",
  "source_review",
  "catchall_sender",
];
export const QUEUE_FAMILIES: readonly QueueFamily[] = ["log_source", "waf", "ips", "property_watch"];
export const QUEUE_SEVERITIES: readonly QueueSeverity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

const HOUR_MS = 3_600_000;
export const COMPARE_CHIPS = [
  { key: "8h", ms: 8 * HOUR_MS },
  { key: "24h", ms: 24 * HOUR_MS },
  { key: "7d", ms: 7 * 24 * HOUR_MS },
] as const;
export type CompareChip = (typeof COMPARE_CHIPS)[number]["key"];

export interface QueueQuery {
  scope: QueueScope;
  kinds: QueueKind[];
  families: QueueFamily[];
  severities: QueueSeverity[];
  newOnly: boolean;
  /** A chosen comparison instant, relative to now; "" compares with the stored marker. */
  compare: CompareChip | "";
  offset: number;
  /** What the URL held that this page does not know. */
  ignored: string[];
}

function all(value: Raw): string[] {
  return value === undefined ? [] : Array.isArray(value) ? value : [value];
}

function pick<T extends string>(raw: Raw, allowed: readonly T[], ignored: string[], name: string): T[] {
  const out: T[] = [];
  for (const item of all(raw)) {
    if ((allowed as readonly string[]).includes(item)) {
      if (!out.includes(item as T)) out.push(item as T);
    } else if (!ignored.includes(name)) {
      ignored.push(name);
    }
  }
  return out;
}

export function parseQueueQuery(params: Record<string, Raw>): QueueQuery {
  const ignored: string[] = [];
  const rawScope = all(params.scope)[0];
  let scope: QueueScope = "all";
  if (rawScope !== undefined) {
    if ((QUEUE_SCOPES as readonly string[]).includes(rawScope)) scope = rawScope as QueueScope;
    else ignored.push("scope");
  }
  const rawCompare = all(params.compare)[0];
  let compare: CompareChip | "" = "";
  if (rawCompare !== undefined) {
    if (COMPARE_CHIPS.some((c) => c.key === rawCompare)) compare = rawCompare as CompareChip;
    else ignored.push("compare");
  }
  const rawOffset = all(params.offset)[0];
  let offset = 0;
  if (rawOffset !== undefined) {
    const n = Number(rawOffset);
    if (Number.isInteger(n) && n >= 0) offset = Math.min(n, QUEUE_CAP - PAGE_SIZE);
    else ignored.push("offset");
  }
  return {
    scope,
    kinds: pick(params.kind, QUEUE_KINDS, ignored, "kind"),
    families: pick(params.family, QUEUE_FAMILIES, ignored, "family"),
    severities: pick(params.severity, QUEUE_SEVERITIES, ignored, "severity"),
    newOnly: all(params.new)[0] === "1",
    compare,
    offset: offset - (offset % PAGE_SIZE),
    ignored,
  };
}

/** The address of a view: only what differs from the defaults is written. */
export function queueHref(query: QueueQuery, patch: Partial<QueueQuery> = {}): string {
  const q = { ...query, ...patch };
  const qs = new URLSearchParams();
  if (q.scope !== "all") qs.set("scope", q.scope);
  for (const k of q.kinds) qs.append("kind", k);
  for (const f of q.families) qs.append("family", f);
  for (const s of q.severities) qs.append("severity", s);
  if (q.newOnly) qs.set("new", "1");
  if (q.compare) qs.set("compare", q.compare);
  if (q.offset > 0) qs.set("offset", String(q.offset));
  const text = qs.toString();
  return text ? `/queue?${text}` : "/queue";
}

/** Changing what is shown starts again at the first page. */
export function withFirstPage(patch: Partial<QueueQuery>): Partial<QueueQuery> {
  return { ...patch, offset: 0 };
}

/** The view is narrowed by something the URL chose, so it may hide rows the analyst has not seen. */
export function isNarrowed(query: QueueQuery): boolean {
  return (
    query.scope !== "all" ||
    query.kinds.length > 0 ||
    query.families.length > 0 ||
    query.severities.length > 0 ||
    query.newOnly ||
    query.offset > 0
  );
}

export function isFiltered(query: QueueQuery): boolean {
  return query.kinds.length > 0 || query.families.length > 0 || query.severities.length > 0 || query.newOnly;
}

/** The query string the API reads. `since` is an ISO instant, or undefined for the stored marker. */
export function queueApiQuery(query: QueueQuery, since: string | undefined): string {
  const qs = new URLSearchParams();
  qs.set("scope", query.scope);
  for (const k of query.kinds) qs.append("kind", k);
  for (const f of query.families) qs.append("family", f);
  for (const s of query.severities) qs.append("severity", s);
  if (query.newOnly) qs.set("new_only", "true");
  if (since) qs.set("since", since);
  qs.set("limit", String(PAGE_SIZE));
  qs.set("offset", String(query.offset));
  return qs.toString();
}

/** The comparison instant of a chip, as the API's `since`. */
export function compareSince(chip: CompareChip, nowMs: number): string {
  const ms = COMPARE_CHIPS.find((c) => c.key === chip)?.ms ?? 0;
  return new Date(nowMs - ms).toISOString();
}

/**
 * Why a load does not move the marker, or null when it may (D-59). The marker advances when the
 * page is opened, but never while a source failed to load, never under a chosen comparison, and
 * never from a view that hides rows (a filter, another scope, a later page): moving it from there
 * would clear the New label of rows the analyst was not shown.
 */
export type KeptReason = "failed" | "override" | "narrowed";

export function markerKept(
  page: QueuePage,
  query: QueueQuery,
): { reason: KeptReason; failed: QueuePage["sources"] } | null {
  if (query.compare) return { reason: "override", failed: [] };
  const failed = page.sources.filter((s) => s.state === "FAILED");
  if (failed.length > 0) return { reason: "failed", failed };
  if (isNarrowed(query)) return { reason: "narrowed", failed: [] };
  return null;
}
