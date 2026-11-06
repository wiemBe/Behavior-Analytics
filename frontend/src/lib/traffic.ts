// The query, links and figures of `/behavior/traffic`.
//
// Everything the screen shows is in the URL: the view, the window, the sort, the page and its
// size and the source. An unknown value falls back to the default instead of being forwarded, so
// a crafted link is never reflected into a request or into the page.

import type { FormatLocale } from "@/lib/format";
import { formatCount } from "@/lib/format";

export type TrafficView = "waf" | "ips";
export const TRAFFIC_VIEWS: readonly TrafficView[] = ["waf", "ips"];

export const TRAFFIC_WINDOWS = ["5m", "1h", "24h", "7d", "28d"] as const;
export type TrafficWindowKey = (typeof TRAFFIC_WINDOWS)[number];

export const TRAFFIC_PAGE_SIZES = [10, 25, 50, 100] as const;
/** "All": one request of the route's largest page, without paging. In the page URL it is
 * `limit=all`; the API gets 1000. */
export const ALL_ROWS = 1000;

export const WAF_SORTS = ["rate", "peak", "host", "endpoint"] as const;
export const IPS_SORTS = ["rate", "peak", "ip"] as const;
export type TrafficSort = (typeof WAF_SORTS)[number] | (typeof IPS_SORTS)[number];
export type TrafficOrder = "asc" | "desc";

export const DEFAULT_WINDOW: TrafficWindowKey = "1h";
export const DEFAULT_SORT: TrafficSort = "rate";
export const DEFAULT_LIMIT = 10;

export interface TrafficQuery {
  view: TrafficView;
  window: TrafficWindowKey;
  sort: TrafficSort;
  order: TrafficOrder;
  limit: number;
  offset: number;
  /** A log source id, or "" for the first source the view lists. */
  source: string;
}

type Raw = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function sortsOf(view: TrafficView): readonly TrafficSort[] {
  return view === "waf" ? WAF_SORTS : IPS_SORTS;
}

/** The order a column starts in: figures high to low, names A to Z. */
export function defaultOrder(sort: TrafficSort): TrafficOrder {
  return sort === "rate" || sort === "peak" ? "desc" : "asc";
}

export function parseTrafficQuery(params: Raw): TrafficQuery {
  const view: TrafficView = first(params.view) === "ips" ? "ips" : "waf";
  const rawWindow = first(params.window);
  const window = TRAFFIC_WINDOWS.find((w) => w === rawWindow) ?? DEFAULT_WINDOW;
  const rawSort = first(params.sort);
  const sort = sortsOf(view).find((s) => s === rawSort) ?? DEFAULT_SORT;
  const rawOrder = first(params.order);
  const order: TrafficOrder =
    rawOrder === "asc" || rawOrder === "desc" ? rawOrder : defaultOrder(sort);
  const rawLimitText = first(params.limit);
  const rawLimit = rawLimitText === "all" ? ALL_ROWS : Number(rawLimitText);
  const limit =
    rawLimit === ALL_ROWS
      ? ALL_ROWS
      : (TRAFFIC_PAGE_SIZES.find((n) => n === rawLimit) ?? DEFAULT_LIMIT);
  const rawOffset = Number(first(params.offset) ?? 0);
  // All has no pages.
  const offset =
    limit !== ALL_ROWS && Number.isFinite(rawOffset) && rawOffset > 0 ? Math.floor(rawOffset) : 0;
  const rawSource = first(params.source) ?? "";
  const source = /^[A-Za-z0-9-]{1,64}$/.test(rawSource) ? rawSource : "";
  return { view, window, sort, order, limit, offset, source };
}

/** The query string of a state, with every default left out so a link stays short. */
export function trafficHref(query: TrafficQuery, change: Partial<TrafficQuery> = {}): string {
  const q = { ...query, ...change };
  const qs = new URLSearchParams();
  if (q.view !== "waf") qs.set("view", q.view);
  if (q.source) qs.set("source", q.source);
  if (q.window !== DEFAULT_WINDOW) qs.set("window", q.window);
  if (q.sort !== DEFAULT_SORT) qs.set("sort", q.sort);
  if (q.order !== defaultOrder(q.sort)) qs.set("order", q.order);
  if (q.limit !== DEFAULT_LIMIT) qs.set("limit", q.limit === ALL_ROWS ? "all" : String(q.limit));
  if (q.offset > 0) qs.set("offset", String(q.offset));
  const text = qs.toString();
  return text ? `/behavior/traffic?${text}` : "/behavior/traffic";
}

/** The link a column header follows: the same column flips its order, a new one starts at its
 * own default, and the page goes back to the first. */
export function sortHref(query: TrafficQuery, column: TrafficSort): string {
  const order: TrafficOrder =
    query.sort === column ? (query.order === "asc" ? "desc" : "asc") : defaultOrder(column);
  return trafficHref(query, { sort: column, order, offset: 0 });
}

export function ariaSort(
  query: TrafficQuery,
  column: TrafficSort,
): "ascending" | "descending" | "none" {
  if (query.sort !== column) return "none";
  return query.order === "asc" ? "ascending" : "descending";
}

/** The query params `Pagination` carries on its own links: all but the offset. */
export function paginationParams(query: TrafficQuery): Record<string, string | undefined> {
  return {
    view: query.view !== "waf" ? query.view : undefined,
    source: query.source || undefined,
    window: query.window !== DEFAULT_WINDOW ? query.window : undefined,
    sort: query.sort !== DEFAULT_SORT ? query.sort : undefined,
    order: query.order !== defaultOrder(query.sort) ? query.order : undefined,
    limit:
      query.limit === DEFAULT_LIMIT ? undefined : query.limit === ALL_ROWS ? "all" : String(query.limit),
  };
}

/** A rate in events per second with three significant-looking digits: "41.2", "0.083", "1,204". */
export function formatRate(value: number | null | undefined, locale: FormatLocale = "en"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : abs >= 0.01 ? 3 : 4;
  const text = new Intl.NumberFormat(locale === "tr" ? "tr-TR" : "en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
  return abs < 0.0001 ? "<0.0001" : text;
}

/** The share of the source total that no listed entity carries, as a percentage with one
 * decimal; null when either figure is unknown or the total is zero. */
export function unattributedShare(
  unattributed: number | null,
  total: number | null,
): number | null {
  if (unattributed === null || total === null || total <= 0) return null;
  return unattributed / total;
}

export { formatCount };
