// KPI card 3 (UR.4.2): offenses, from GET /offenses/analytics.
//
// Finding (backend/app/repositories/offense.py, aggregate_metrics): with no
// snapshot at all the analytics return zeros (active 0, critical 0, ...), exactly
// as for "snapshots exist and none is open". So the analytics alone cannot tell
// "no offenses" from "not collected yet". `GET /offenses?limit=1` can: its
// `total` is 0 only when no snapshot exists. The two reads are combined here.
//
// Also from that code: `magnitude_distribution` keys a NULL magnitude as the
// string "unassigned" (a label shared with the assignment distribution), so a
// key that is not a number is counted as "magnitude unknown", not as low.

import type { OffenseAnalytics, OffenseSummary, Page } from "@/lib/api";

import { notCollected, type Qualifier } from "./qualifiers";

export interface OffensesCard {
  open: number | null;
  critical: number | null;
  unassigned: number | null;
  exceedingSla: number | null;
  /** The product's own threshold (settings), e.g. 7. */
  criticalMagnitude: number;
  /** open = critical + below + unknown; null parts are not derivable. */
  breakdown: { critical: number; below: number | null; unknown: number } | null;
  qualifiers: Qualifier[];
}

export function offensesCard(
  analytics: OffenseAnalytics,
  probe: Page<OffenseSummary> | null,
): OffensesCard {
  const a = analytics.aggregates;
  const base = { criticalMagnitude: a.critical_magnitude };

  // No snapshot: every figure is unknown, not zero.
  let reason: string | null = null;
  if (probe !== null && probe.total === 0) {
    reason = "No offense snapshot has been collected yet.";
  } else if (probe === null && a.active === 0) {
    reason = "Could not confirm that an offense snapshot exists.";
  }
  if (reason !== null) {
    return {
      ...base,
      open: null,
      critical: null,
      unassigned: null,
      exceedingSla: null,
      breakdown: null,
      qualifiers: [notCollected(reason)],
    };
  }

  const unknown = analytics.magnitude_distribution
    .filter((e) => !Number.isFinite(Number(e.key)) || e.key.trim() === "")
    .reduce((s, e) => s + e.count, 0);
  const below = a.active - a.critical - unknown;
  return {
    ...base,
    open: a.active,
    critical: a.critical,
    unassigned: a.unassigned,
    exceedingSla: a.exceeding_sla,
    breakdown: { critical: a.critical, below: below >= 0 ? below : null, unknown },
    qualifiers: [],
  };
}
