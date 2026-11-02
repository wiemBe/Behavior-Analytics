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

export function rangeHref(id: SummaryRangeId): string {
  return id === DEFAULT_RANGE ? "/admin/siem-load" : `/admin/siem-load?range=${id}`;
}
