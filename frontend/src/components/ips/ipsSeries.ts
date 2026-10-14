// Turning stored IPS buckets into a plottable series, honestly.
//
// The Phase A and WAF rules apply unchanged (`lib/timeseries.ts`,
// `waf/wafSeries.ts`): an interval with no stored row is a gap, and a value the
// bucket did not measure is a null on the line, never a zero. Neither is "no
// events": whether absence means zero is the detectors' own reasoning, and the
// UI does not reconstruct it. IPS-specific rules on top:
//
//   * An entity bucket is trusted only as far as its dimension's measurement.
//     EXACT is plotted; LOWER_BOUND is plotted and flagged "at least n"; any
//     other status (not collected, failed, rejected) and a missing bucket record
//     (null) are not plotted, and the count they reported is kept for the tooltip.
//   * A source-level share needs both of its counts and an EXACT breakdown; a
//     share of nothing (zero denominator) is a gap, not 0 %.
//   * Maintenance buckets are plotted and marked: the engine does not judge them.

import type { IpsEntityMetricBucket, IpsSourceMetricBucket } from "@/lib/api";
import { isolatedPoints, kindMeaning, type PointKind, type SeriesPoint } from "@/lib/timeseries";

export { isolatedPoints, kindMeaning };

export interface IpsPoint extends SeriesPoint {
  /** True when the plotted value is "at least n". */
  lowerBound: boolean;
  /** The dimension's measurement for the bucket; null when its record is missing. */
  measurement: string | null;
  maintenance: boolean;
  /** Entity buckets: admission lost entities to the quota, ceiling or top-N cut. */
  admissionTruncated: boolean;
  /** Share points: the counts the proportion divides, for the tooltip. */
  numerator: number | null;
  denominator: number | null;
}

/** Why an interval is marked on the chart. */
export type IpsMark = "GAP" | "NOT_MEASURED" | "NOT_EXACT" | "MAINTENANCE";

export interface IpsMarkedInterval {
  start: number;
  end: number;
  mark: IpsMark;
}

/** A step wider than the bucket by this factor is declared a gap (Phase A's rule). */
const GAP_FACTOR = 1.5;

/** Which source-level series a detector judges. */
export type SourceSeriesKind = "concentration" | "fanout" | "block_ratio";

export const SOURCE_SERIES: Record<string, SourceSeriesKind> = {
  IPS_SOURCE_CONCENTRATION_SPIKE: "concentration",
  IPS_PORT_FANOUT_SPIKE: "fanout",
  IPS_BLOCK_RATIO_SHIFT: "block_ratio",
};

const SERIES_DIMENSION: Record<SourceSeriesKind, string> = {
  concentration: "SOURCE_IP",
  fanout: "PORT_FANOUT",
  block_ratio: "ACTION",
};

interface Measured {
  value: number | null;
  lowerBound: boolean;
  kind: PointKind;
  reported: number | null;
  measurement: string | null;
  numerator: number | null;
  denominator: number | null;
}

function byTime<T extends { bucket_start: string }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => Date.parse(a.bucket_start) - Date.parse(b.bucket_start));
}

/** What a measurement status allows: plotted, plotted as a floor, or not plotted. */
function admit(
  measurement: string | null,
): { plotted: boolean; lowerBound: boolean; kind: PointKind } {
  if (measurement === "EXACT") return { plotted: true, lowerBound: false, kind: "COMPLETE" };
  if (measurement === "LOWER_BOUND") return { plotted: true, lowerBound: true, kind: "COMPLETE" };
  return { plotted: false, lowerBound: false, kind: "MISSING" };
}

function measuredEntity(b: IpsEntityMetricBucket): Measured {
  const m = b.measurement ?? null;
  const a = admit(m);
  const plotted = a.plotted && b.events != null;
  return {
    value: plotted ? b.events : null,
    lowerBound: plotted && a.lowerBound,
    kind: plotted ? "COMPLETE" : "MISSING",
    reported: b.events ?? null,
    measurement: m,
    numerator: null,
    denominator: null,
  };
}

function dimensionMeasurement(b: IpsSourceMetricBucket, dimension: string): string | null {
  return b.dimensions.find((d) => d.dimension === dimension)?.measurement ?? null;
}

function measuredSource(b: IpsSourceMetricBucket, kind: SourceSeriesKind): Measured {
  const m = dimensionMeasurement(b, SERIES_DIMENSION[kind]);
  const a = admit(m);
  let raw: number | null = null;
  let numerator: number | null = null;
  let denominator: number | null = null;

  if (kind === "fanout") {
    raw = b.max_distinct_ports;
  } else if (kind === "concentration") {
    numerator = b.top1_events;
    denominator = b.source_events;
  } else {
    const parts = [b.blocked_events, b.allowed_events, b.unmapped_events];
    numerator = b.blocked_events;
    // Every action-bearing event: blocked, allowed and unmapped. Unknown if any is.
    denominator = parts.some((p) => p == null) ? null : parts.reduce<number>((s, p) => s + (p ?? 0), 0);
  }
  if (kind !== "fanout") {
    raw = numerator != null && denominator != null && denominator > 0 ? numerator / denominator : null;
  }

  // A share is exact or nothing: with a floor on one count, the other direction is unknown.
  const shareNeedsExact = kind !== "fanout";
  const usable = a.plotted && raw != null && (!shareNeedsExact || !a.lowerBound);
  let pointKind: PointKind = "COMPLETE";
  if (!usable) pointKind = m === "LOWER_BOUND" ? "PARTIAL" : "MISSING";
  return {
    value: usable ? raw : null,
    lowerBound: usable && a.lowerBound,
    kind: pointKind,
    reported: raw,
    measurement: m,
    numerator,
    denominator,
  };
}

function toPoint(t: number, m: Measured, maintenance: boolean, truncated: boolean): IpsPoint {
  return {
    t,
    observed: m.value,
    kind: m.kind,
    reported: m.reported,
    lowerBound: m.lowerBound,
    measurement: m.measurement,
    maintenance,
    admissionTruncated: truncated,
    numerator: m.numerator,
    denominator: m.denominator,
  };
}

function gapPoint(t: number): IpsPoint {
  return {
    t,
    observed: null,
    kind: "GAP",
    reported: null,
    lowerBound: false,
    measurement: null,
    maintenance: false,
    admissionTruncated: false,
    numerator: null,
    denominator: null,
  };
}

function build<T extends { bucket_start: string; bucket_seconds: number }>(
  rows: T[],
  measure: (b: T) => Measured,
  maintenance: (b: T) => boolean,
  truncated: (b: T) => boolean,
): IpsPoint[] {
  const sorted = byTime(rows);
  const points: IpsPoint[] = [];
  for (let i = 0; i < sorted.length; i += 1) {
    const b = sorted[i];
    const t = Date.parse(b.bucket_start);
    points.push(toPoint(t, measure(b), maintenance(b), truncated(b)));
    const next = sorted[i + 1];
    if (!next) continue;
    const width = b.bucket_seconds * 1000;
    // A missing row is not in the data at all: infer the absence from the step.
    if (width > 0 && Date.parse(next.bucket_start) - t > width * GAP_FACTOR) {
      points.push(gapPoint(t + width));
    }
  }
  return points;
}

export function buildIpsEntitySeries(buckets: IpsEntityMetricBucket[]): IpsPoint[] {
  return build(
    buckets,
    measuredEntity,
    (b) => b.in_maintenance === true,
    (b) => b.admission_truncated === true,
  );
}

export function buildIpsSourceSeries(
  buckets: IpsSourceMetricBucket[],
  kind: SourceSeriesKind,
): IpsPoint[] {
  return build(
    buckets,
    (b) => measuredSource(b, kind),
    (b) => b.in_maintenance === true,
    () => false,
  );
}

/**
 * Intervals to mark behind the line: never-stored intervals, buckets whose value
 * was not measured, buckets measured only as a floor, and maintenance buckets.
 * A bucket can be several at once; each reason is its own interval so the key can
 * name it.
 */
export function ipsMarkedIntervals(
  rows: Array<{ bucket_start: string; bucket_seconds: number }>,
  points: IpsPoint[],
): IpsMarkedInterval[] {
  const sorted = byTime(rows);
  const out: IpsMarkedInterval[] = [];
  const at = new Map(points.map((p) => [p.t, p]));
  for (let i = 0; i < sorted.length; i += 1) {
    const b = sorted[i];
    const t = Date.parse(b.bucket_start);
    const width = b.bucket_seconds * 1000;
    const p = at.get(t);
    if (p) {
      if (p.observed == null) out.push({ start: t, end: t + width, mark: "NOT_MEASURED" });
      else if (p.lowerBound || (p.measurement != null && p.measurement !== "EXACT")) {
        out.push({ start: t, end: t + width, mark: "NOT_EXACT" });
      }
      if (p.maintenance) out.push({ start: t, end: t + width, mark: "MAINTENANCE" });
    }
    const next = sorted[i + 1];
    if (next && width > 0 && Date.parse(next.bucket_start) - t > width * GAP_FACTOR) {
      out.push({ start: t + width, end: Date.parse(next.bucket_start), mark: "GAP" });
    }
  }
  return out;
}

export interface IpsSeriesCounts {
  /** Stored buckets. */
  stored: number;
  /** Intervals with no stored bucket at all. */
  gaps: number;
  /** Stored buckets whose value is not plotted. */
  notMeasured: number;
  /** Stored buckets plotted only as "at least n". */
  lowerBound: number;
  /** Stored buckets whose dimension measurement is not EXACT (a superset of the above two). */
  notExact: number;
  maintenance: number;
  admissionTruncated: number;
}

export function ipsSeriesCounts(points: IpsPoint[]): IpsSeriesCounts {
  const stored = points.filter((p) => p.kind !== "GAP");
  return {
    stored: stored.length,
    gaps: points.length - stored.length,
    notMeasured: stored.filter((p) => p.observed == null).length,
    lowerBound: stored.filter((p) => p.lowerBound).length,
    notExact: stored.filter((p) => p.measurement !== "EXACT").length,
    maintenance: stored.filter((p) => p.maintenance).length,
    admissionTruncated: stored.filter((p) => p.admissionTruncated).length,
  };
}
