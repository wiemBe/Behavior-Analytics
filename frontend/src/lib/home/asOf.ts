// How far a read's data reaches (UI-7.2): the instant after which nothing has been collected.
//
// Each function returns an ISO instant, or null when the response says nothing: a null is "no
// as-of", never "fresh" and never an invented time. `window_end` of the fleet reads is the
// request's own `now`, so it is not an as-of and is not used here.

import type { FleetEpsSeries, RuleSummary, SourceBehavior, WafSourceBehavior } from "@/lib/api";

const iso = (ms: number): string => new Date(ms).toISOString();

/** The latest of the instants that can be read, or null when none can. */
function latest(instants: Array<number | null>): string | null {
  let best: number | null = null;
  for (const t of instants) {
    if (t !== null && !Number.isNaN(t) && (best === null || t > best)) best = t;
  }
  return best === null ? null : iso(best);
}

/** The end of the last step whose EPS was measured (every expected cell complete). */
export function epsSeriesAsOf(series: FleetEpsSeries): string | null {
  return latest(
    series.series.flatMap((s) =>
      s.points
        .filter((p) => p.eps !== null)
        .map((p) => Date.parse(p.step_start) + series.step_seconds * 1000),
    ),
  );
}

/** The end of the newest bucket of a metric series; the bucket's own width. */
export function bucketsAsOf(
  buckets: ReadonlyArray<{ bucket_start: string; bucket_seconds: number }>,
): string | null {
  return latest(buckets.map((b) => Date.parse(b.bucket_start) + b.bucket_seconds * 1000));
}

/**
 * The newest `last_bucket_at` over the sources. The field is a bucket's start; the family's
 * bucket width, when the cadence read gave it, completes it to the bucket's end.
 */
export function sourcesAsOf(
  sources: readonly SourceBehavior[],
  bucketSeconds: number | null,
): string | null {
  const width = bucketSeconds !== null && bucketSeconds > 0 ? bucketSeconds * 1000 : 0;
  return latest(sources.map((s) => (s.last_bucket_at ? Date.parse(s.last_bucket_at) + width : null)));
}

/** The newest latest bucket over the WAF sources. */
export function wafSourcesAsOf(sources: readonly WafSourceBehavior[]): string | null {
  return latest(
    sources.map((s) =>
      s.latest_bucket
        ? Date.parse(s.latest_bucket.bucket_start) + s.latest_bucket.bucket_seconds * 1000
        : null,
    ),
  );
}

/** The newest rule-health evaluation among the rules a page holds. */
export function rulesAsOf(rules: readonly Pick<RuleSummary, "health_evaluated_at">[]): string | null {
  return latest(rules.map((r) => (r.health_evaluated_at ? Date.parse(r.health_evaluated_at) : null)));
}
