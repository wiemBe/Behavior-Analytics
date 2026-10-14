// The seasonal band under an IPS chart: one cell per (ISO weekday, UTC hour).
//
// Keyed exactly as the WAF and Phase A bands are (`seasonalKey`): ISO weekday 1-7
// with Monday = 1, and the UTC hour of the bucket start. The band is the cell's
// p05..p95 and the expectation its median, except for the share metrics of the
// source level, whose expectation is the pooled sum ratio the detector divides
// (`numerator_sum / denominator_sum`). A cell that is missing or not reliable
// yields null: no band for that bucket, never a zero-width or zero-height one.

import type { IpsBaselineCell } from "@/lib/api";
import { seasonalKey } from "@/components/waf/wafBaseline";

/**
 * What a chart needs to look cells up: plain data, so a server component can hand
 * it to the client chart (a function cannot cross that boundary).
 */
export interface IpsBaselineSource {
  cells: IpsBaselineCell[];
  metric: string;
  /** True for a share metric: its expectation is the pooled sum ratio. */
  pooled: boolean;
}

export interface IpsSeasonal {
  /** The expectation, or null when a share cell carries no pooled sums. */
  median: number | null;
  /** p05, or null when the cell has no percentile. */
  low: number | null;
  /** p95, or null when the cell has no percentile. */
  high: number | null;
}

export type IpsBaselineLookup = (t: number) => IpsSeasonal | null;

export function buildIpsBaselineLookup(source: IpsBaselineSource): IpsBaselineLookup {
  const index = new Map<string, IpsBaselineCell>();
  for (const c of source.cells) {
    if (c.metric_name !== source.metric) continue;
    index.set(`${c.weekday}-${c.hour}`, c);
  }
  return (t) => {
    const [weekday, hour] = seasonalKey(t);
    const cell = index.get(`${weekday}-${hour}`);
    // Unreliable means too few samples to drive a verdict: not an expectation.
    if (!cell || !cell.is_reliable) return null;
    const both = cell.p05 != null && cell.p95 != null;
    const pooled =
      source.pooled &&
      cell.numerator_sum != null &&
      cell.denominator_sum != null &&
      cell.denominator_sum > 0
        ? cell.numerator_sum / cell.denominator_sum
        : null;
    // A share cell with no pooled sums has no honest expectation to draw.
    return {
      median: source.pooled ? pooled : cell.median,
      low: both ? cell.p05 : null,
      high: both ? cell.p95 : null,
    };
  };
}

/** Reliable cells of the metric, for the "no reliable cell" state. */
export function reliableCellCount(cells: IpsBaselineCell[], metric: string): number {
  return cells.filter((c) => c.metric_name === metric && c.is_reliable).length;
}
