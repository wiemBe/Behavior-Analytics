// The query, links and figures of `/behavior/noise`.
//
// Everything the screen shows is in the URL: the window, the order and the page. An unknown value
// falls back to the default instead of being forwarded, so a crafted link is never reflected into a
// request or into the page.
//
// URL keys: window (7 | 28, default 28), order (incidents | alerts | noise_share, default
// incidents), offset (a multiple of the page size, default 0).

import type { NoiseOrder, NoiseSourceRow, NoiseWindow } from "@/lib/api";

export const NOISE_WINDOWS: readonly NoiseWindow[] = [7, 28];
export const NOISE_ORDERS: readonly NoiseOrder[] = ["incidents", "alerts", "noise_share"];
export const NOISE_PAGE_SIZE = 50;
export const DEFAULT_NOISE_WINDOW: NoiseWindow = 28;
export const DEFAULT_NOISE_ORDER: NoiseOrder = "incidents";

/** The backend withholds the share until this many closed items stand behind it (SOC-17a). */
export const MIN_NOISE_DENOMINATOR = 5;

export interface NoiseQuery {
  window: NoiseWindow;
  order: NoiseOrder;
  offset: number;
}

type Raw = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function parseNoiseQuery(params: Raw): NoiseQuery {
  const rawWindow = Number(first(params.window));
  const window = NOISE_WINDOWS.find((w) => w === rawWindow) ?? DEFAULT_NOISE_WINDOW;
  const rawOrder = first(params.order);
  const order = NOISE_ORDERS.find((o) => o === rawOrder) ?? DEFAULT_NOISE_ORDER;
  const rawOffset = Number(first(params.offset) ?? 0);
  const offset = Number.isFinite(rawOffset) && rawOffset > 0 ? Math.floor(rawOffset) : 0;
  return { window, order, offset };
}

/** The query string of a state, with every default left out so a link stays short. */
export function noiseHref(query: NoiseQuery, change: Partial<NoiseQuery> = {}): string {
  const q = { ...query, ...change };
  const qs = new URLSearchParams();
  if (q.window !== DEFAULT_NOISE_WINDOW) qs.set("window", String(q.window));
  if (q.order !== DEFAULT_NOISE_ORDER) qs.set("order", q.order);
  if (q.offset > 0) qs.set("offset", String(q.offset));
  const text = qs.toString();
  return text ? `/behavior/noise?${text}` : "/behavior/noise";
}

/** The query params `Pagination` carries on its own links: all but the offset. */
export function noisePaginationParams(query: NoiseQuery): Record<string, string | undefined> {
  return {
    window: query.window !== DEFAULT_NOISE_WINDOW ? String(query.window) : undefined,
    order: query.order !== DEFAULT_NOISE_ORDER ? query.order : undefined,
  };
}

/** The source's own behavior page. */
export function sourceHref(row: Pick<NoiseSourceRow, "log_source_id">): string {
  return `/behavior/sources/${encodeURIComponent(row.log_source_id)}`;
}

/** The alert list filtered to the source (SOC-7b key `log_source_id`). */
export function sourceAlertsHref(row: Pick<NoiseSourceRow, "log_source_id">): string {
  return `/alerts?log_source_id=${encodeURIComponent(row.log_source_id)}`;
}

/**
 * Where a row's detail goes. SOC-17b adds `/behavior/noise/[id]`; until that route exists this
 * returns null and the row links nowhere, because a link to a 404 spends the analyst's trust.
 * 17b: return `/behavior/noise/${encodeURIComponent(row.log_source_id)}`.
 */
export function noiseDetailHref(row: Pick<NoiseSourceRow, "log_source_id">): string | null {
  void row;
  return null;
}

/** A source that is not monitored has no counts at all: they are null, not zero. */
export function isNotMonitored(row: Pick<NoiseSourceRow, "monitoring_enabled" | "judgement">): boolean {
  return !row.monitoring_enabled || row.judgement === "NOT_COLLECTED";
}

/** A source is shown as judged only when the backend says so and the source is monitored. */
export function isJudged(row: NoiseSourceRow): boolean {
  return row.judged && row.judgement === "JUDGED" && !isNotMonitored(row);
}
