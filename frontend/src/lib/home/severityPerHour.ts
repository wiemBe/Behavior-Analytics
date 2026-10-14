// Anomaly severity per hour (UR.4.3): counts per (UTC hour, severity) over a
// range, from the capped list GET /anomalies?since&until&limit=200.
//
// When the list's `total` exceeds what came back the counts are lower bounds:
// the result is TRUNCATED and says so, and a widget must not draw the partial
// stack as the whole. An hour with no anomaly in a *complete* list is a measured
// zero; in a truncated list it is "none seen".

import type { AnomalySummary, Page } from "@/lib/api";

import { countBySeverity } from "./anomalies";
import { insufficient, truncated, type Qualifier } from "./qualifiers";

const HOUR_MS = 3_600_000;
/** More hours than a 7-day range needs; beyond this the range is not an hour chart. */
export const MAX_HOURS = 24 * 7 + 1;

export interface HourBucket {
  /** ISO instant at the start of the UTC hour. */
  start: string;
  counts: Record<string, number>;
  total: number;
}

export interface SeverityPerHour {
  hours: HourBucket[];
  /** Severities present, in rank order, for the legend. */
  severities: string[];
  /** Rows whose `detected_at` fell outside the range and were not counted. */
  outsideRange: number;
  /** True when the counts are lower bounds. */
  lowerBound: boolean;
  qualifiers: Qualifier[];
}

export function severityPerHour(
  page: Page<AnomalySummary>,
  range: { since: string; until: string },
): SeverityPerHour {
  const from = Math.floor(Date.parse(range.since) / HOUR_MS) * HOUR_MS;
  const to = Date.parse(range.until);
  const count = Math.ceil((to - from) / HOUR_MS);
  if (!Number.isFinite(from) || !Number.isFinite(to) || count <= 0 || count > MAX_HOURS) {
    return {
      hours: [],
      severities: [],
      outsideRange: 0,
      lowerBound: false,
      qualifiers: [insufficient("The range is not a valid span of hours.")],
    };
  }

  const hours: HourBucket[] = Array.from({ length: count }, (_, i) => ({
    start: new Date(from + i * HOUR_MS).toISOString(),
    counts: {},
    total: 0,
  }));
  let outsideRange = 0;
  const counted: Pick<AnomalySummary, "severity">[] = [];
  for (const item of page.items) {
    const t = Date.parse(item.detected_at);
    const i = Math.floor((t - from) / HOUR_MS);
    if (!Number.isFinite(t) || t >= to || i < 0 || i >= count) {
      outsideRange += 1;
      continue;
    }
    const h = hours[i];
    h.counts[item.severity] = (h.counts[item.severity] ?? 0) + 1;
    h.total += 1;
    counted.push(item);
  }

  const qualifiers: Qualifier[] = [];
  const lowerBound = page.total > page.items.length;
  if (lowerBound) qualifiers.push(truncated(page.items.length, page.total));
  return {
    hours,
    severities: countBySeverity(counted).map((c) => c.severity),
    outsideRange,
    lowerBound,
    qualifiers,
  };
}

