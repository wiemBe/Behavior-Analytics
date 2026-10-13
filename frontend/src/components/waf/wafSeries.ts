// Turning stored WAF endpoint buckets into a plottable series, honestly.
//
// The Phase A rules apply unchanged (`lib/timeseries.ts`): a PARTIAL or MISSING
// bucket is a null, never its reported value, and an interval with no stored row
// is a gap, never a zero. Two WAF-specific rules on top:
//
//   * The API says a bucket has no row, or that its count is null, by absence or
//     `null`. Neither is "no requests": whether absence means zero depends on the
//     source's bucket record, which is not on the endpoint's row. So both stay
//     null on the line.
//   * A LOWER_BOUND count is plotted (it is a real observation of "at least n")
//     but flagged, so the tooltip and the key say it is a floor.
//
// The point type extends Phase A's `SeriesPoint` so `isolatedPoints` and
// `kindMeaning` are reused rather than copied.

import type { WafMetricBucket } from "@/lib/api";
import { isolatedPoints, kindMeaning, type PointKind, type SeriesPoint } from "@/lib/timeseries";

export { isolatedPoints, kindMeaning };

export interface WafPoint extends SeriesPoint {
  /** Endpoint coverage as a fraction in [0,1], or null when not recorded. */
  coverage: number | null;
  /** True when the plotted count is "at least n". */
  lowerBound: boolean;
}

/** A step wider than the bucket by this factor is declared a gap (Phase A's rule). */
const GAP_FACTOR = 1.5;

function sorted(buckets: WafMetricBucket[]): WafMetricBucket[] {
  return [...buckets].sort((a, b) => Date.parse(a.bucket_start) - Date.parse(b.bucket_start));
}

/** What a stored bucket actually is, once a null count is taken into account. */
function kindOf(b: WafMetricBucket): PointKind {
  if (b.completeness !== "COMPLETE") return b.completeness;
  // Marked complete but carrying no count: nothing was measured.
  return b.request_count == null ? "MISSING" : "COMPLETE";
}

export function buildWafSeries(buckets: WafMetricBucket[]): WafPoint[] {
  const rows = sorted(buckets);
  const points: WafPoint[] = [];
  for (let i = 0; i < rows.length; i += 1) {
    const b = rows[i];
    const t = Date.parse(b.bucket_start);
    const kind = kindOf(b);
    points.push({
      t,
      observed: kind === "COMPLETE" ? b.request_count : null,
      kind,
      reported: b.request_count,
      coverage: b.endpoint_coverage_ratio,
      lowerBound: b.request_count_measurement === "LOWER_BOUND",
    });

    const next = rows[i + 1];
    if (!next) continue;
    const width = b.bucket_seconds * 1000;
    if (width > 0 && Date.parse(next.bucket_start) - t > width * GAP_FACTOR) {
      points.push({
        t: t + width,
        observed: null,
        kind: "GAP",
        reported: null,
        coverage: null,
        lowerBound: false,
      });
    }
  }
  return points;
}

/** Intervals not fully observed, for shading behind the line. */
export function wafUnobserved(
  buckets: WafMetricBucket[],
): Array<{ start: number; end: number; kind: PointKind }> {
  const rows = sorted(buckets);
  const out: Array<{ start: number; end: number; kind: PointKind }> = [];
  for (let i = 0; i < rows.length; i += 1) {
    const b = rows[i];
    const t = Date.parse(b.bucket_start);
    const width = b.bucket_seconds * 1000;
    const kind = kindOf(b);
    if (kind !== "COMPLETE") out.push({ start: t, end: t + width, kind });
    const next = rows[i + 1];
    if (!next) continue;
    const nextT = Date.parse(next.bucket_start);
    if (width > 0 && nextT - t > width * GAP_FACTOR) {
      out.push({ start: t + width, end: nextT, kind: "GAP" });
    }
  }
  return out;
}
