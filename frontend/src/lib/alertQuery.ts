// The alert list's view in the URL (SOC-7b): every filter, the time range, the sort and the
// page, so a link reproduces the view it was copied from.
//
// URL keys: status, severity (repeats), family, log_source_id (repeats), q, range, since,
// until, sort, order, offset. Nothing in the query string is trusted. Each value is checked
// against an allow-list (or a UUID / length bound) and a value that fails is dropped and named
// in `ignored`, so the page can say "Ignored an unknown severity value" instead of crashing or
// sending a request the backend would answer with a 422.

import type { AlertFilterParams, AlertListParams } from "./api";
import {
  parseRange,
  rangeQuery,
  type RangeIssue,
  type RangeState,
} from "./timeRange";

export const ALERT_STATUSES = ["OPEN", "ACKNOWLEDGED", "RESOLVED"] as const;
export const ALERT_SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;
/** The closed family set of `app/alerts/query.py` (SOC-7a §4): there is no per-type filter. */
export const ALERT_FAMILIES = [
  "log_source",
  "waf",
  "ips",
  "property_watch",
  "identity",
  "catchall_sender",
  "custom_rule",
  "scheduled_search",
  "qradar_instance",
] as const;
export const ALERT_SORTS = ["created_at", "severity", "updated_at"] as const;
export const ALERT_ORDERS = ["asc", "desc"] as const;

/** The backend's bound on `q` (`MAX_Q_LENGTH`). */
export const Q_MAX = 100;
export const PAGE_SIZE = 100;
/** The most log sources one link may name, so a crafted URL cannot build an unbounded request. */
export const LOG_SOURCE_MAX = 25;

export type AlertStatusValue = (typeof ALERT_STATUSES)[number];
export type AlertSeverityValue = (typeof ALERT_SEVERITIES)[number];
export type AlertFamilyValue = (typeof ALERT_FAMILIES)[number];
export type AlertSortValue = (typeof ALERT_SORTS)[number];
export type AlertOrderValue = (typeof ALERT_ORDERS)[number];

/** What a dropped URL value was, so the page words the note. */
export type IgnoredValue =
  | "status"
  | "severity"
  | "family"
  | "logSource"
  | "q"
  | "sort"
  | "order"
  | "offset"
  | RangeIssue;

export interface AlertQuery {
  status: AlertStatusValue | "";
  severity: AlertSeverityValue[];
  family: AlertFamilyValue | "";
  logSourceIds: string[];
  /** Trimmed, at most `Q_MAX` characters; "" when blank. */
  q: string;
  sort: AlertSortValue;
  order: AlertOrderValue;
  offset: number;
  range: RangeState;
  ignored: IgnoredValue[];
}

export const DEFAULT_SORT: AlertSortValue = "created_at";
export const DEFAULT_ORDER: AlertOrderValue = "desc";

type Raw = string | string[] | undefined;
export type AlertSearchParams = Record<string, Raw>;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function all(value: Raw): string[] {
  if (value === undefined) return [];
  return (Array.isArray(value) ? value : [value]).map((v) => v.trim()).filter((v) => v !== "");
}

function pickOne<T extends string>(
  value: Raw,
  allowed: readonly T[],
  bad: IgnoredValue,
  ignored: IgnoredValue[],
): T | "" {
  const values = all(value);
  if (values.length === 0) return "";
  const hit = allowed.find((a) => a === values[0]);
  if (!hit || values.length > 1) ignored.push(bad);
  return hit ?? "";
}

export function parseAlertQuery(params: AlertSearchParams, nowMs: number): AlertQuery {
  const ignored: IgnoredValue[] = [];

  const status = pickOne(params.status, ALERT_STATUSES, "status", ignored);
  const family = pickOne(params.family, ALERT_FAMILIES, "family", ignored);

  const sevValues = all(params.severity);
  const severity = ALERT_SEVERITIES.filter((s) => sevValues.includes(s));
  if (sevValues.some((v) => !(ALERT_SEVERITIES as readonly string[]).includes(v))) {
    ignored.push("severity");
  }

  const sourceValues = all(params.log_source_id);
  const goodSources = sourceValues.filter((v) => UUID_RE.test(v)).map((v) => v.toLowerCase());
  let logSourceIds = [...new Set(goodSources)];
  if (goodSources.length !== sourceValues.length || logSourceIds.length > LOG_SOURCE_MAX) {
    ignored.push("logSource");
  }
  logSourceIds = logSourceIds.slice(0, LOG_SOURCE_MAX);

  // A blank search sends nothing. One over the backend's bound is dropped rather than cut: a
  // truncated search would match a different set than the one the link names.
  const rawQ = all(params.q)[0] ?? "";
  const q = rawQ.length > Q_MAX ? "" : rawQ;
  if (rawQ.length > Q_MAX) ignored.push("q");

  const sort = pickOne(params.sort, ALERT_SORTS, "sort", ignored) || DEFAULT_SORT;
  const order = pickOne(params.order, ALERT_ORDERS, "order", ignored) || DEFAULT_ORDER;

  const rawOffset = all(params.offset)[0];
  const parsedOffset = Number(rawOffset ?? 0);
  const offset =
    Number.isFinite(parsedOffset) && parsedOffset > 0 ? Math.floor(parsedOffset) : 0;
  if (rawOffset !== undefined && !Number.isFinite(parsedOffset)) ignored.push("offset");

  const range = parseRange(params, nowMs);
  ignored.push(...range.issues);

  return { status, severity, family, logSourceIds, q, sort, order, offset, range, ignored };
}

/** The URL parameters of a view, canonical: defaults and empty values are left out. */
export function alertPairs(query: AlertQuery, withOffset = true): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  if (query.status) pairs.push(["status", query.status]);
  for (const s of ALERT_SEVERITIES) if (query.severity.includes(s)) pairs.push(["severity", s]);
  if (query.family) pairs.push(["family", query.family]);
  for (const id of query.logSourceIds) pairs.push(["log_source_id", id]);
  if (query.q) pairs.push(["q", query.q]);
  for (const [k, v] of Object.entries(rangeQuery(query.range))) pairs.push([k, v]);
  if (query.sort !== DEFAULT_SORT) pairs.push(["sort", query.sort]);
  if (query.order !== DEFAULT_ORDER) pairs.push(["order", query.order]);
  if (withOffset && query.offset > 0) pairs.push(["offset", String(query.offset)]);
  return pairs;
}

/** The view's path and query string. A change of filter starts again at page one. */
export function alertHref(query: AlertQuery, patch: Partial<AlertQuery> = {}): string {
  const next = { ...query, ...patch };
  if (!("offset" in patch)) next.offset = 0;
  const qs = new URLSearchParams(alertPairs(next)).toString();
  return qs ? `/alerts?${qs}` : "/alerts";
}

/** Something narrows the list: the status, a severity, a family, a source, a search or a range. */
export function isAlertFiltered(query: AlertQuery): boolean {
  return Boolean(
    query.status ||
      query.severity.length ||
      query.family ||
      query.logSourceIds.length ||
      query.q ||
      query.range.since ||
      query.range.until,
  );
}

/** Only the status narrows the list: the one filter the page had before SOC-7b. */
export function isStatusOnly(query: AlertQuery): boolean {
  return Boolean(
    query.status &&
      !query.severity.length &&
      !query.family &&
      !query.logSourceIds.length &&
      !query.q &&
      !query.range.since &&
      !query.range.until,
  );
}

/** The same filters for the list and the facets. Each facet drops its own filter server-side;
 *  `total` applies them all, so it equals the list's total. */
export function alertFilterParams(query: AlertQuery): AlertFilterParams {
  return {
    status: query.status || undefined,
    severity: query.severity.length ? query.severity : undefined,
    log_source_id: query.logSourceIds.length ? query.logSourceIds : undefined,
    family: query.family || undefined,
    q: query.q || undefined,
    since: query.range.since || undefined,
    until: query.range.until || undefined,
  };
}

export function alertListParams(query: AlertQuery): AlertListParams {
  return {
    ...alertFilterParams(query),
    // The defaults are the backend's own, so they are left unsent.
    sort: query.sort === DEFAULT_SORT ? undefined : query.sort,
    order: query.order === DEFAULT_ORDER ? undefined : query.order,
    limit: PAGE_SIZE,
    offset: query.offset,
  };
}

/** The list request without its page: what `GET /alerts.csv` takes, so the file is the view. */
export function alertExportParams(query: AlertQuery): AlertListParams {
  return {
    ...alertFilterParams(query),
    sort: query.sort === DEFAULT_SORT ? undefined : query.sort,
    order: query.order === DEFAULT_ORDER ? undefined : query.order,
  };
}

/** A sortable column's next sort: the default order the first time, the opposite after. */
export function sortPatch(
  query: AlertQuery,
  column: AlertSortValue,
): Pick<AlertQuery, "sort" | "order"> {
  if (query.sort === column) return { sort: column, order: query.order === "asc" ? "desc" : "asc" };
  return { sort: column, order: "desc" };
}
