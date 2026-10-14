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

/**
 * A proportion detector's own quantity: which stored counts divide into it, which
 * breakdown status says they were measured, and which baseline cells describe it.
 * Mirrors `RATIO_METRICS` in `app/waf/baseline.py` and the detectors in
 * `app/waf/behavior.py`.
 */
export interface RatioSpec {
  baselineMetric: string;
  measurement: "action_measurement" | "response_code_measurement" | "client_measurement";
  numerator: "blocked_count" | "server_error_count" | "client_error_count" | "top1_client_requests";
  denominator: "action_events" | "response_code_events" | "client_events";
  /** The proportion in words, for the axis and the tooltip. */
  label: string;
}

export const RATIO_SPECS: Record<string, RatioSpec> = {
  WAF_BLOCK_RATIO_SPIKE: {
    baselineMetric: "block_ratio",
    measurement: "action_measurement",
    numerator: "blocked_count",
    denominator: "action_events",
    label: "Blocked share of requests",
  },
  WAF_ERROR_RATIO_SPIKE: {
    baselineMetric: "server_error_ratio",
    measurement: "response_code_measurement",
    numerator: "server_error_count",
    denominator: "response_code_events",
    label: "HTTP 5xx share of responses",
  },
  WAF_CLIENT_ERROR_RATIO_SPIKE: {
    baselineMetric: "client_error_ratio",
    measurement: "response_code_measurement",
    numerator: "client_error_count",
    denominator: "response_code_events",
    label: "HTTP 4xx share of responses",
  },
  WAF_CLIENT_CONCENTRATION_SPIKE: {
    baselineMetric: "top1_client_share",
    measurement: "client_measurement",
    numerator: "top1_client_requests",
    denominator: "client_events",
    label: "Largest source's share of requests",
  },
};

export function ratioSpecFor(type: string): RatioSpec | null {
  return RATIO_SPECS[type] ?? null;
}

/**
 * What a bucket says about a proportion.
 *
 * Only a bucket collected whole, whose breakdown was measured EXACT, with a
 * non-zero denominator, has a proportion. A breakdown that was not collected,
 * failed or hit its cap (LOWER_BOUND), a partial bucket, a null count and a zero
 * denominator are all gaps: the share of nothing is not 0 %.
 */
export function ratioOf(
  b: WafMetricBucket,
  spec: RatioSpec,
): { kind: PointKind; value: number | null } {
  if (b.completeness !== "COMPLETE") return { kind: b.completeness, value: null };
  if (b[spec.measurement] !== "EXACT") {
    return { kind: b[spec.measurement] === "LOWER_BOUND" ? "PARTIAL" : "MISSING", value: null };
  }
  const num = b[spec.numerator];
  const den = b[spec.denominator];
  if (num == null || den == null || den <= 0) return { kind: "MISSING", value: null };
  return { kind: "COMPLETE", value: num / den };
}

/** The proportion series, as fractions in [0,1]; `reported` carries the request count. */
export function buildWafRatioSeries(buckets: WafMetricBucket[], spec: RatioSpec): WafPoint[] {
  const base = buildWafSeries(buckets);
  const byTime = new Map(sorted(buckets).map((b) => [Date.parse(b.bucket_start), b]));
  return base.map((p) => {
    const b = byTime.get(p.t);
    if (!b || p.kind === "GAP") return p;
    const r = ratioOf(b, spec);
    return {
      ...p,
      observed: r.value,
      kind: r.kind,
      // The request count stays beside the proportion for the hover readout.
      reported: b.request_count,
      lowerBound: false,
    };
  });
}

/** Intervals whose proportion was not measured, for shading. */
export function wafRatioUnobserved(
  buckets: WafMetricBucket[],
  spec: RatioSpec,
): Array<{ start: number; end: number; kind: PointKind }> {
  const rows = sorted(buckets);
  const out: Array<{ start: number; end: number; kind: PointKind }> = [];
  for (let i = 0; i < rows.length; i += 1) {
    const b = rows[i];
    const t = Date.parse(b.bucket_start);
    const width = b.bucket_seconds * 1000;
    const r = ratioOf(b, spec);
    if (r.kind !== "COMPLETE") out.push({ start: t, end: t + width, kind: r.kind });
    const next = rows[i + 1];
    if (next && width > 0 && Date.parse(next.bucket_start) - t > width * GAP_FACTOR) {
      out.push({ start: t + width, end: Date.parse(next.bucket_start), kind: "GAP" });
    }
  }
  return out;
}
