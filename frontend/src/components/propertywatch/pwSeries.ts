// Turning a property watch's per-value series into a plottable model, honestly.
//
// The API sends a point only for a bucket that was collected: a gap is an absent point. This
// module puts every point on a fixed grid of bucket slots, so a slot with no point is a null on
// the line and a shaded span, and `connectNulls: false` can leave it open. Four rules, each
// with a test:
//
//   * an uncollected bucket is a gap, never a zero;
//   * an UNKNOWN reading (a lower-bound bucket in which the value was absent) and a value that
//     was not tracked at that bucket are gaps, never a zero;
//   * a MEASURED_ZERO reading is drawn as 0;
//   * a LOWER_BOUND count is plotted as "at least n" and flagged, not drawn as an exact count.
//
// A fact that cannot be placed under one of these (a COUNT with no measurement, a measured zero
// carrying a count) fails closed to a gap. No colour, no ECharts and no I/O here.

import type { MeasurementKind, PwBaselineState, PwSeriesPoint, PwValueSeries } from "@/lib/api";

/** The chart draws at most this many values: five overlapping lines are the most one can read. */
export const PW_MAX_SERIES = 5;

export type PlotKind =
  | "EXACT"
  | "LOWER_BOUND"
  | "MEASURED_ZERO"
  | "UNKNOWN"
  | "NOT_AN_OBSERVATION"
  | "NOT_COLLECTED";

export interface PlotPoint {
  /** Bucket start, epoch milliseconds. */
  t: number;
  /** The plotted number, or `null` for a gap. */
  value: number | null;
  kind: PlotKind;
  /** The reading's own count, `null` when there is none (never 0 for an unknown). */
  events: number | null;
  baseline: PwBaselineState | null;
  low: number | null;
  median: number | null;
  high: number | null;
}

export interface PlotLine {
  valueId: string;
  label: string;
  pinned: boolean;
  points: PlotPoint[];
}

export interface Span {
  start: number;
  end: number;
}

export interface PlotModel {
  /** Every slot, ascending, epoch milliseconds. */
  slots: number[];
  bucketMs: number;
  lines: PlotLine[];
  /** Slots known not to have been collected, merged into spans. */
  uncollected: Span[];
  /** How many slots lie in the window and how many of them were collected. */
  collectedSlots: number | null;
  windowSlots: number;
  /** True when a single value is plotted and at least one point has a reliable range. */
  hasBand: boolean;
  /** Series beyond `PW_MAX_SERIES` that were handed in and not drawn. */
  dropped: number;
}

export interface PlotInput {
  series: PwValueSeries[];
  bucketSeconds: number;
  /** The window the axis covers, ISO instants. Omitted: the extent of the points. */
  windowStart?: string | null;
  windowEnd?: string | null;
  /**
   * Bucket starts known to have been collected, ISO. Omitted: taken from the series' points,
   * which are exactly the collected buckets. With neither, nothing is said about collection.
   */
  collected?: string[] | null;
}

function pointKind(p: PwSeriesPoint): { kind: PlotKind; value: number | null } {
  switch (p.reading) {
    case "COUNT": {
      if (p.events === null || p.measurement === null) return { kind: "UNKNOWN", value: null };
      return { kind: p.measurement === "LOWER_BOUND" ? "LOWER_BOUND" : "EXACT", value: p.events };
    }
    case "MEASURED_ZERO":
      return p.events === 0 ? { kind: "MEASURED_ZERO", value: 0 } : { kind: "UNKNOWN", value: null };
    case "NOT_AN_OBSERVATION":
      return { kind: "NOT_AN_OBSERVATION", value: null };
    case "NO_BUCKET":
      return { kind: "NOT_COLLECTED", value: null };
    default:
      // UNKNOWN, and anything this build does not know: not a number.
      return { kind: "UNKNOWN", value: null };
  }
}

function mergeSpans(starts: number[], bucketMs: number): Span[] {
  const spans: Span[] = [];
  for (const start of starts) {
    const last = spans[spans.length - 1];
    if (last && last.end === start) last.end = start + bucketMs;
    else spans.push({ start, end: start + bucketMs });
  }
  return spans;
}

export function buildPlot(input: PlotInput): PlotModel {
  const bucketMs = Math.max(1, input.bucketSeconds) * 1000;
  const drawn = input.series.slice(0, PW_MAX_SERIES);
  const dropped = input.series.length - drawn.length;

  const collectedTimes = new Set<number>();
  const source: string[] =
    input.collected ?? (input.series[0] ? input.series[0].points.map((p) => p.bucket_start) : []);
  for (const iso of source) {
    const t = Date.parse(iso);
    if (!Number.isNaN(t)) collectedTimes.add(t);
  }
  const collectionKnown = input.collected != null || input.series.length > 0;

  const times = new Set<number>(collectedTimes);
  for (const s of drawn) for (const p of s.points) times.add(Date.parse(p.bucket_start));

  const parse = (iso?: string | null) => {
    const t = iso ? Date.parse(iso) : NaN;
    return Number.isNaN(t) ? null : t;
  };
  const known = [...times].filter((t) => !Number.isNaN(t));
  const startMs = parse(input.windowStart) ?? (known.length ? Math.min(...known) : null);
  const endMs = parse(input.windowEnd) ?? (known.length ? Math.max(...known) : null);

  const grid: number[] = [];
  if (startMs !== null && endMs !== null && endMs >= startMs) {
    // Buckets are aligned to their width, so the grid is too.
    for (let t = Math.ceil(startMs / bucketMs) * bucketMs; t <= endMs; t += bucketMs) grid.push(t);
  }
  const slotSet = new Set<number>([...grid, ...known]);
  const slots = [...slotSet].sort((a, b) => a - b);

  const lines: PlotLine[] = drawn.map((s) => {
    const byTime = new Map<number, PwSeriesPoint>();
    for (const p of s.points) byTime.set(Date.parse(p.bucket_start), p);
    return {
      valueId: s.value_id,
      label: s.value_key,
      pinned: s.pinned,
      points: slots.map((t): PlotPoint => {
        const p = byTime.get(t);
        if (!p) {
          return {
            t,
            value: null,
            kind: "NOT_COLLECTED",
            events: null,
            baseline: null,
            low: null,
            median: null,
            high: null,
          };
        }
        const { kind, value } = pointKind(p);
        const reliable = p.baseline_state === "RELIABLE";
        return {
          t,
          value,
          kind,
          events: p.events,
          baseline: p.baseline_state,
          // A range that is not reliable is not drawn, whatever number rides along.
          low: reliable ? p.expected_low : null,
          median: reliable ? p.expected_median : null,
          high: reliable ? p.expected_high : null,
        };
      }),
    };
  });

  const gridInWindow = grid.length ? grid : [];
  const uncollectedStarts = collectionKnown
    ? gridInWindow.filter((t) => !collectedTimes.has(t))
    : [];
  const single = lines.length === 1 ? lines[0] : null;
  return {
    slots,
    bucketMs,
    lines,
    uncollected: mergeSpans(uncollectedStarts, bucketMs),
    collectedSlots: collectionKnown ? gridInWindow.filter((t) => collectedTimes.has(t)).length : null,
    windowSlots: gridInWindow.length,
    hasBand: single !== null && single.points.some((p) => p.low !== null && p.high !== null),
    dropped,
  };
}

/** A lone measured point between gaps cannot be seen as a line, so it is marked. */
export function isolated(points: PlotPoint[], index: number): boolean {
  const p = points[index];
  if (p.value === null) return false;
  return points[index - 1]?.value == null && points[index + 1]?.value == null;
}

/** A measurement as the words the tooltip and the table use. */
export function kindWords(kind: PlotKind): string {
  switch (kind) {
    case "EXACT":
      return "exact count";
    case "LOWER_BOUND":
      return "lower bound: at least this many";
    case "MEASURED_ZERO":
      return "measured zero";
    case "UNKNOWN":
      return "unknown: not a zero";
    case "NOT_AN_OBSERVATION":
      return "this value was not tracked then";
    case "NOT_COLLECTED":
      return "bucket not collected";
  }
}

export function measurementOf(kind: PlotKind): MeasurementKind | null {
  return kind === "EXACT" || kind === "MEASURED_ZERO"
    ? "EXACT"
    : kind === "LOWER_BOUND"
      ? "LOWER_BOUND"
      : null;
}
