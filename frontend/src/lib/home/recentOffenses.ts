// Recent offenses (UR.4.3): the five most recently updated open offenses, from
// GET /offenses?status=OPEN&sort=last_updated_time&limit=5, and the one-row probe
// GET /offenses?limit=1 that tells "no open offenses" from "no snapshot yet"
// (UI-2.1 report: the analytics return zeros in both cases; the probe's `total`
// is 0 only when no snapshot exists).
//
// Fail closed: an empty open list with no probe answer is "could not confirm", not
// "no open offenses".

import type { OffenseSummary, Page } from "@/lib/api";

import { notCollected, type Qualifier } from "./qualifiers";

export const RECENT_OFFENSES_LIMIT = 5;
export const MAGNITUDE_SEGMENTS = 10;

export interface RecentOffenseRow {
  id: number;
  description: string | null;
  lastUpdated: string | null;
  /** 0-10 as QRadar gives it; null when it is not known. */
  magnitude: number | null;
  /** Filled segments of the bar; null (no bar at all) when magnitude is null. */
  segments: number | null;
  status: string;
  href: string;
}

export interface RecentOffensesView {
  rows: RecentOffenseRow[];
  openTotal: number;
  /** "Showing the latest 5 of 12"; null when every open offense is listed. */
  shown: string | null;
  /** The empty state's words; null when there are rows. */
  empty: string | null;
  qualifiers: Qualifier[];
}

/** Filled segments for a magnitude. Null is unknown: no bar, not an empty one. */
export function magnitudeSegments(magnitude: number | null | undefined): number | null {
  if (magnitude == null || !Number.isFinite(magnitude)) return null;
  return Math.min(MAGNITUDE_SEGMENTS, Math.max(0, Math.round(magnitude)));
}

export function recentOffenses(
  open: Page<OffenseSummary>,
  probe: Page<OffenseSummary> | null,
): RecentOffensesView {
  const rows: RecentOffenseRow[] = open.items.slice(0, RECENT_OFFENSES_LIMIT).map((o) => ({
    id: o.qradar_offense_id,
    description: o.description,
    lastUpdated: o.last_updated_time,
    magnitude: o.magnitude,
    segments: magnitudeSegments(o.magnitude),
    status: o.status,
    href: `/offenses/${o.qradar_offense_id}`,
  }));
  const base = { rows, openTotal: open.total };
  if (rows.length > 0) {
    return {
      ...base,
      shown:
        open.total > rows.length
          ? `Showing the latest ${rows.length} of ${open.total.toLocaleString("en-US")}`
          : null,
      empty: null,
      qualifiers: [],
    };
  }
  if (probe !== null && probe.total === 0) {
    const reason = "No offense snapshot has been collected yet.";
    return { ...base, shown: null, empty: null, qualifiers: [notCollected(reason)] };
  }
  if (probe === null) {
    const reason = "Could not confirm that an offense snapshot exists.";
    return { ...base, shown: null, empty: null, qualifiers: [notCollected(reason)] };
  }
  return {
    ...base,
    shown: null,
    empty: "No open offenses in the latest snapshot.",
    qualifiers: [],
  };
}
