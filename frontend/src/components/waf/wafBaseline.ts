// The seasonal baseline under a chart: one cell per (ISO weekday, UTC hour).
//
// The backend keys cells exactly as Phase A does (`app/anomaly/seasonal.py`):
// ISO weekday 1-7 with Monday = 1, and the UTC hour of the bucket start. The
// detector's own range is the cell's p05..p95 and its expected value the cell's
// median (`app/waf/behavior.py`), so a cell reproduces the range the incident
// was judged against. A cell that is missing or not reliable yields null: no
// band for that bucket, never a zero-width or zero-height one.

import type { WafBaselineCell } from "@/lib/api";

export interface SeasonalValue {
  median: number;
  /** p05, or null when the cell has no percentile. */
  low: number | null;
  /** p95, or null when the cell has no percentile. */
  high: number | null;
}

export type BaselineLookup = (t: number) => SeasonalValue | null;

/** ISO weekday (Monday = 1 .. Sunday = 7) and UTC hour of an instant. */
export function seasonalKey(t: number): [number, number] {
  const d = new Date(t);
  const day = d.getUTCDay();
  return [day === 0 ? 7 : day, d.getUTCHours()];
}

export function buildBaselineLookup(
  cells: WafBaselineCell[],
  metric: string,
  normalizerVersion: number | null,
): BaselineLookup {
  const index = new Map<string, WafBaselineCell>();
  for (const c of cells) {
    if (c.metric_name !== metric) continue;
    if (normalizerVersion != null && c.normalizer_version !== normalizerVersion) continue;
    index.set(`${c.weekday}-${c.hour}`, c);
  }
  return (t) => {
    const [weekday, hour] = seasonalKey(t);
    const cell = index.get(`${weekday}-${hour}`);
    // Unreliable means too few samples to drive a verdict: not an expectation.
    if (!cell || !cell.is_reliable) return null;
    const both = cell.p05 != null && cell.p95 != null;
    return { median: cell.median, low: both ? cell.p05 : null, high: both ? cell.p95 : null };
  };
}

/**
 * What a chart needs to look cells up: plain data, so a server component can hand
 * it to the client chart (a function cannot cross that boundary).
 */
export interface BaselineSource {
  cells: WafBaselineCell[];
  metric: string;
  normalizerVersion: number | null;
}
