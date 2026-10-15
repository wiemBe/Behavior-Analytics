// Pure derivations for the two subject-chart widgets (UI-3.5b): one WAF endpoint's requests and
// one log source's EPS, each against its seasonal band. A bucket that was not fully collected is
// never drawn as a measurement, a missing cell is never a band, and a missing measurement is
// never a zero.

import type { ResourceState } from "@/hooks/useApiResource";
import type { BaselineCell } from "@/lib/api";
import { insufficient, partial, truncated, type Qualifier } from "@/lib/home/qualifiers";
import { seasonalKey, type BaselineLookup } from "@/components/waf/wafBaseline";

/** What the widget needs of a stored bucket, whichever family it came from. */
export interface BucketFacts {
  start: string;
  completeness: string;
}

/**
 * The caveats a series carries, worst first.
 *
 * - INSUFFICIENT_DATA: not one bucket falls in a reliable seasonal cell, so there is no
 *   expectation to compare with. The line may still be drawn; no verdict is implied.
 * - PARTIAL: stored buckets that were not fully collected (they are shaded, not plotted).
 * - TRUNCATED: the read stopped before the window's end.
 */
export function seriesQualifiers(
  buckets: readonly BucketFacts[],
  lookup: BaselineLookup | null,
  truncatedAt: { shown: number; total: number } | null,
  noBaselineReason: string,
): Qualifier[] {
  const out: Qualifier[] = [];
  if (buckets.length > 0 && !(lookup && buckets.some((b) => lookup(Date.parse(b.start)) != null))) {
    out.push(insufficient(noBaselineReason));
  }
  const complete = buckets.filter((b) => b.completeness === "COMPLETE").length;
  if (complete < buckets.length) out.push(partial(complete, buckets.length, "stored buckets"));
  if (truncatedAt) out.push(truncated(truncatedAt.shown, truncatedAt.total));
  return out;
}

/** True when at least one stored bucket was fully collected, so a line can be drawn. */
export function hasMeasured(buckets: readonly BucketFacts[]): boolean {
  return buckets.some((b) => b.completeness === "COMPLETE");
}

/**
 * A Phase A source's seasonal cells by (ISO weekday, UTC hour) for `average_eps`, like the
 * WAF lookup: a cell that is missing or not yet reliable yields null, never a zero band.
 */
export function sourceCellLookup(cells: readonly BaselineCell[]): BaselineLookup {
  const index = new Map<string, BaselineCell>();
  for (const c of cells) {
    if (c.metric_name === "average_eps") index.set(`${c.weekday}-${c.hour}`, c);
  }
  return (t) => {
    const [weekday, hour] = seasonalKey(t);
    const cell = index.get(`${weekday}-${hour}`);
    if (!cell || !cell.is_reliable) return null;
    const both = cell.p05 != null && cell.p95 != null;
    return { median: cell.median, low: both ? cell.p05 : null, high: both ? cell.p95 : null };
  };
}

/** A read's state with its data mapped; every state without data passes through unchanged. */
export function mapResource<A, B>(state: ResourceState<A>, map: (data: A) => B): ResourceState<B> {
  return state.status === "ok" ? { ...state, data: map(state.data) } : state;
}
