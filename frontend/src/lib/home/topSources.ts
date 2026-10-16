// Top log sources by EPS (UR.4.3): the global top five of the union of each
// class's `top_sources`. A source in the global top five is in its own class's
// top five, so the union loses nothing.
//
// EPS is the source's complete-bucket events over the window's seconds. The
// fleet summary does not give the seconds a source was actually measured over,
// so a source with gaps reads low: the row says "at least" when its class's
// window is incomplete.
//
// The deviation is joined from GET /behavior/sources, where it compares the
// *latest* bucket with the baseline, not this window's mean. The row carries
// that basis. It is null, never 0, when the source is not in that list, has no
// expectation, has no ratio, or its latest bucket is not COMPLETE.

import type { AnomalyState, FleetSummary, SourceBehavior, SourceClass } from "@/lib/api";
import { classLabel } from "@/lib/fleet";

export const TOP_N = 5;

export type DeviationGap =
  | "no_behavior_list"
  | "not_in_list"
  | "no_expectation"
  | "bucket_not_complete";

export interface TopSourceRow {
  id: string;
  name: string;
  sourceClass: SourceClass;
  classLabel: string;
  events: number;
  /** Null when the window's length is not usable. */
  eps: number | null;
  /** True when the class's window has missing or partial buckets: EPS is "at least". */
  epsIsLowerBound: boolean;
  /** Deviation of the latest bucket from expected; null when it is not measured. */
  deviation: number | null;
  deviationGap: DeviationGap | null;
  deviationBasis: "latest bucket";
  /** Lifecycle state; null when the source is not in the behavior list. */
  state: AnomalyState | null;
}

const GAP_TEXT: Record<DeviationGap, string> = {
  no_behavior_list: "Source states unavailable",
  not_in_list: "Not in the behavior list",
  no_expectation: "No expected value",
  bucket_not_complete: "Latest bucket incomplete",
};

export function deviationGapText(gap: DeviationGap): string {
  return GAP_TEXT[gap];
}

export function topSources(
  summary: FleetSummary,
  behaviors: SourceBehavior[] | null,
  limit: number = TOP_N,
): TopSourceRow[] {
  const seconds = (Date.parse(summary.window_end) - Date.parse(summary.window_start)) / 1000;
  const usable = Number.isFinite(seconds) && seconds > 0;
  const byId = new Map((behaviors ?? []).map((b) => [b.log_source_id, b]));

  const union = new Map<string, TopSourceRow>();
  for (const c of summary.classes) {
    const incomplete = c.buckets.partial + c.buckets.missing > 0;
    for (const s of c.top_sources) {
      if (union.has(s.id)) continue;
      const b = byId.get(s.id);
      let deviation: number | null = null;
      let gap: DeviationGap | null = null;
      if (behaviors === null) gap = "no_behavior_list";
      else if (!b) gap = "not_in_list";
      else if (b.expected_eps == null || b.deviation_ratio == null) gap = "no_expectation";
      else if (b.last_bucket_completeness !== "COMPLETE") gap = "bucket_not_complete";
      else deviation = b.deviation_ratio;
      union.set(s.id, {
        id: s.id,
        name: s.name,
        sourceClass: c.source_class,
        classLabel: classLabel(c.source_class),
        events: s.events,
        eps: usable ? s.events / seconds : null,
        epsIsLowerBound: incomplete,
        deviation,
        deviationGap: gap,
        deviationBasis: "latest bucket",
        state: b ? b.state : null,
      });
    }
  }
  return [...union.values()]
    .sort((a, b) => b.events - a.events || a.name.localeCompare(b.name) || a.id.localeCompare(b.id))
    .slice(0, limit);
}

/**
 * Each row's EPS as a share of the largest EPS on the list, 0-1, for the
 * magnitude bar. Null where the row has no EPS (no bar), and for every row when
 * the largest is not above zero (nothing to scale against; no bar rather than a
 * full or an empty one).
 */
export function epsShares(rows: TopSourceRow[]): (number | null)[] {
  const max = rows.reduce((m, r) => (r.eps !== null && r.eps > m ? r.eps : m), 0);
  return rows.map((r) => (r.eps === null || max <= 0 ? null : Math.min(1, r.eps / max)));
}
