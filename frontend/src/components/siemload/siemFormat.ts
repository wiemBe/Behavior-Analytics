// The figures of the SIEM load page. A figure that was not measured is an em dash and never "0";
// a measured zero is "0". Durations carry their unit: milliseconds under a second, seconds under
// a minute, then the shared duration spelling.

import { formatDuration } from "@/lib/time";

const MISSING = "—";

/** A duration in milliseconds: "820 ms", "4.2 s", "2 min 5 s". Null is unmeasured. */
export function formatMs(ms: number | null | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return MISSING;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return formatDuration(Math.round(ms / 1000));
}

/** The summary windows the range control offers, in order, with the hours each reads. */
export const SUMMARY_RANGES = [
  { id: "1h", hours: 1 },
  { id: "24h", hours: 24 },
  { id: "7d", hours: 168 },
  { id: "30d", hours: 720 },
] as const;

export type SummaryRangeId = (typeof SUMMARY_RANGES)[number]["id"];

export const DEFAULT_RANGE: SummaryRangeId = "24h";

/** The range a URL asks for; anything that is not one of the four is the default. */
export function parseRange(raw: string | undefined): SummaryRangeId {
  const found = SUMMARY_RANGES.find((range) => range.id === raw);
  return found ? found.id : DEFAULT_RANGE;
}

export function rangeHours(id: SummaryRangeId): number {
  return (SUMMARY_RANGES.find((range) => range.id === id) ?? SUMMARY_RANGES[1]).hours;
}

/** The windows the QRadar trends offer (the history API answers these two). */
export const TREND_RANGES = [
  { id: "24h", hours: 24 },
  { id: "7d", hours: 168 },
] as const;

export type TrendRangeId = (typeof TREND_RANGES)[number]["id"];

export const DEFAULT_TREND: TrendRangeId = "24h";

/** The trend window a URL asks for; anything that is not one of the two is the default. */
export function parseTrend(raw: string | undefined): TrendRangeId {
  const found = TREND_RANGES.find((range) => range.id === raw);
  return found ? found.id : DEFAULT_TREND;
}

export function trendHours(id: TrendRangeId): 24 | 168 {
  return id === "7d" ? 168 : 24;
}

/** The page's URL for a summary window and a trend window; a default is left out. */
export function rangeHref(id: SummaryRangeId, trend: TrendRangeId = DEFAULT_TREND): string {
  const params = [
    id === DEFAULT_RANGE ? null : `range=${id}`,
    trend === DEFAULT_TREND ? null : `trend=${trend}`,
  ].filter((part): part is string => part !== null);
  return params.length ? `/admin/siem-load?${params.join("&")}` : "/admin/siem-load";
}

const BINARY_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;

/**
 * A size in bytes with its unit: "0 B", "512 B", "12.4 GiB". Null is unmeasured; a measured zero
 * is "0 B". Binary units, because QRadar reports bytes of storage.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return MISSING;
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BINARY_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)}\u00A0${BINARY_UNITS[unit]}`;
}

/** A percentage figure (0 to 100) with one decimal and the sign: "88.0 %". Null is unmeasured. */
export function formatPercentFigure(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  return `${value.toFixed(1)}\u00A0%`;
}
