
import type { ResultMetricPoint, SearchExecution } from "@/lib/api";
import { utcTimeText } from "@/components/ui/UtcTime";


export interface ResultCount {
  kind: "measured" | "lower-bound" | "truncated-unknown" | "none";
  /** The figure alone, without the bound marker. Null when there is none. */
  value: number | null;
}

/** What the Results column may claim for a run. */
export function resultCount(e: Pick<SearchExecution, "result_count" | "truncated">): ResultCount {
  if (e.truncated) {
    return e.result_count == null
      ? { kind: "truncated-unknown", value: null }
      : { kind: "lower-bound", value: e.result_count };
  }
  return e.result_count == null
    ? { kind: "none", value: null }
    : { kind: "measured", value: e.result_count };
}

export type ThresholdVerdict = "breached" | "no-verdict" | "within";

/**
 * The Threshold column. A truncated run that did not breach has no verdict:
 * its true count may still be above the threshold, so it must not read the
 * same as a run that was measured and stayed within it.
 */
export function thresholdVerdict(
  e: Pick<SearchExecution, "threshold_breached" | "truncated">,
): ThresholdVerdict {
  if (e.threshold_breached) return "breached";
  return e.truncated ? "no-verdict" : "within";
}

/** When a run's stored point would have been: the minute it completed. */
export function runBucketTime(e: Pick<SearchExecution, "completed_at" | "started_at">): string | null {
  return e.completed_at ?? e.started_at;
}

/** Start times of the truncated runs, for the trend to mark as gaps. */
export function truncatedRunTimes(executions: SearchExecution[]): string[] {
  const times: string[] = [];
  for (const e of executions) {
    if (!e.truncated) continue;
    const at = runBucketTime(e);
    if (at) times.push(at);
  }
  return times;
}

export interface TrendRow {
  at: string;
  /** Null for a truncated run: drawn as a gap, never as zero. */
  point: ResultMetricPoint | null;
}

/**
 * The trend's x positions: every stored point, plus one empty position for
 * each truncated run that falls at or after the first point. The empty
 * position is what makes the line break; without it the two neighbours would
 * be joined by a straight segment that implies a measurement in between.
 *
 * Points arrive in ascending order from the API and keep that order. A
 * truncated run is placed before the first point that is later than it.
 */
export function trendRows(points: ResultMetricPoint[], truncatedAt: string[]): TrendRow[] {
  if (points.length === 0) return [];
  const first = Date.parse(points[0].bucket_start);
  const gaps = truncatedAt
    .filter((t) => Date.parse(t) >= first)
    .sort((a, b) => Date.parse(a) - Date.parse(b));

  const rows: TrendRow[] = [];
  let g = 0;
  for (const p of points) {
    while (g < gaps.length && Date.parse(gaps[g]) < Date.parse(p.bucket_start)) {
      rows.push({ at: gaps[g], point: null });
      g += 1;
    }
    rows.push({ at: p.bucket_start, point: p });
  }
  for (; g < gaps.length; g += 1) rows.push({ at: gaps[g], point: null });
  return rows;
}

/** A time whose zone is stated once elsewhere (an axis title, a column header): no "UTC". */
export function axisTimeLabel(iso: string | null): string {
  return utcTimeText(iso, "compact", true);
}

/** Indices of measured values with no measured neighbour: a line cannot show them. */
export function isolatedIndices(values: (number | null)[]): number[] {
  const out: number[] = [];
  values.forEach((v, i) => {
    if (v === null) return;
    if ((values[i - 1] ?? null) === null && (values[i + 1] ?? null) === null) out.push(i);
  });
  return out;
}

/** The `units` namespace's translator, as `formatRunDuration` takes it. */
export type RunUnits = (key: string, values: { count: number | string }) => string;

/** A run duration with its unit: `850 ms`, `41.2 s`, `5 min 0 s`. Null is not zero. */
export function formatRunDuration(
  ms: number | null,
  units?: RunUnits,
  locale: "en" | "tr" = "en",
): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  // `units` is the `units` namespace's translator (`second`, `minute`); without it, English.
  const second = (n: number | string) => (units ? units("second", { count: n }) : `${n} s`);
  if (ms < 60000) {
    const seconds = (Math.round(ms / 100) / 10).toString();
    return second(locale === "tr" ? seconds.replace(".", ",") : seconds);
  }
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  return units
    ? `${units("minute", { count: minutes })} ${second(total % 60)}`
    : `${minutes} min ${total % 60} s`;
}
