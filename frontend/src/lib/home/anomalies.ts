// KPI card 4 (UR.4.2): active anomalies.
//
// The breakdown by type comes from the summary and is complete. The breakdown by
// severity is counted client-side from the active list, which is capped; when the
// list's `total` exceeds what came back, the counts are lower bounds and say so.

import type { AnomalySummary, BehaviorSummary, Page } from "@/lib/api";

import { truncated, type Qualifier } from "./qualifiers";

export const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;

export interface TypeCount {
  key: "spikes" | "drops" | "silent" | "candidates" | "recovering";
  label: string;
  count: number;
}

export interface SeverityCount {
  severity: string;
  count: number;
}

export interface AnomaliesCard {
  open: number;
  byType: TypeCount[];
  severity: { entries: SeverityCount[]; qualifiers: Qualifier[] };
  /** Sources without a baseline: unjudged, so absent from every count above. */
  notJudged: number;
  notJudgedText: string;
}

/** Counts per severity: the known ones in rank order, then any unknown ones by name. */
export function countBySeverity(items: Pick<AnomalySummary, "severity">[]): SeverityCount[] {
  const counts = new Map<string, number>();
  for (const it of items) counts.set(it.severity, (counts.get(it.severity) ?? 0) + 1);
  const rank = (s: string) => {
    const i = (SEVERITY_ORDER as readonly string[]).indexOf(s);
    return i === -1 ? SEVERITY_ORDER.length : i;
  };
  return [...counts.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([severity, count]) => ({ severity, count }));
}

export function anomaliesCard(
  summary: BehaviorSummary,
  active: Page<AnomalySummary>,
): AnomaliesCard {
  const qualifiers: Qualifier[] = [];
  if (active.total > active.items.length) {
    qualifiers.push(truncated(active.items.length, active.total));
  }
  const n = summary.insufficient_data_sources;
  return {
    open: summary.open_anomalies,
    byType: [
      { key: "spikes", label: "Spikes", count: summary.spikes },
      { key: "drops", label: "Drops", count: summary.drops },
      { key: "silent", label: "Silent sources", count: summary.silent_sources },
      { key: "candidates", label: "Candidates", count: summary.candidates },
      { key: "recovering", label: "Recovering", count: summary.recovering },
    ],
    severity: { entries: countBySeverity(active.items), qualifiers },
    notJudged: n,
    notJudgedText: `${n.toLocaleString("en-US")} source${n === 1 ? "" : "s"} not judged`,
  };
}
