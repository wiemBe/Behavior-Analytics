// KPI card 4 (UR.4.2): active anomalies of every family.
//
// The total is the sum of the four families' active counts (log sources, WAF, IPS, property
// watches), as `GET /anomalies/summary` measured them in the active list's window. The card
// shows two partitions of it: by family (log sources split by detector, then the other three
// as their own parts) and by lifecycle state. A family whose count is absent was not read: it is
// named as such, never drawn as 0, and the total becomes a lower bound. Severity is retained
// for the chart model and is a lower bound when the active list is capped.

import type { AnomalyFamily, AnomalySummary, BehaviorSummary, Page } from "@/lib/api";

import { truncated, type Qualifier } from "./qualifiers";

export const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;

export interface TypeCount {
  key: "spikes" | "drops" | "silent" | "other" | "open" | "candidates" | "recovering";
  label: string;
  count: number;
}

/** One of the three families the card names beside the log sources. */
export interface FamilyCount {
  key: Exclude<AnomalyFamily, "log_source">;
  /** Null when the count could not be read: not a zero. */
  count: number | null;
}

export interface SeverityCount {
  severity: string;
  count: number;
}

export const FAMILY_ORDER: AnomalyFamily[] = ["log_source", "waf", "ips", "property_watch"];

export interface AnomaliesCard {
  /** All-family active count over the families read; null when none was read. */
  open: number | null;
  /** True when a family was not read, so `open` is a floor. */
  lowerBound: boolean;
  /** The families whose counts were not read. */
  unread: AnomalyFamily[];
  /** Log-source active anomalies; null when not read. */
  logSources: number | null;
  /**
   * Log sources by detector; the remainder is "other", so the parts sum to `logSources`.
   * Empty when the family was not read or the detector counts disagree with it.
   */
  byDetector: TypeCount[];
  /** WAF, IPS and property watches, each its own part of the total. */
  families: FamilyCount[];
  /** Active anomalies by lifecycle state, over the families read. */
  byLifecycle: TypeCount[];
  severity: { entries: SeverityCount[]; qualifiers: Qualifier[] };
  /** Log sources without a baseline: unjudged, so absent from every count above. */
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
  const by = summary.active_by_family ?? {};
  const unread = FAMILY_ORDER.filter((family) => by[family] == null);
  const read = FAMILY_ORDER.flatMap((family) => by[family] ?? []);
  const logSource = by.log_source ?? null;

  // The detector counts are the Phase A summary's own (any age); the family count is bounded to
  // the list's window. If they disagree there is no honest "other" to draw, so the split is
  // dropped and the family shows as one part.
  const named = summary.spikes + summary.drops + summary.silent_sources;
  const splittable = logSource !== null && named <= logSource.total;
  const otherDetectors = logSource !== null ? logSource.total - named : 0;
  const n = summary.insufficient_data_sources;
  return {
    open: read.length === 0 ? null : read.reduce((sum, c) => sum + c.total, 0),
    lowerBound: unread.length > 0,
    unread,
    logSources: logSource?.total ?? null,
    byDetector: splittable
      ? [
          { key: "spikes", label: "Spikes", count: summary.spikes },
          { key: "drops", label: "Drops", count: summary.drops },
          { key: "silent", label: "Silent sources", count: summary.silent_sources },
          ...(otherDetectors > 0
            ? [{ key: "other" as const, label: "Other detectors", count: otherDetectors }]
            : []),
        ]
      : [],
    families: [
      { key: "waf", count: by.waf?.total ?? null },
      { key: "ips", count: by.ips?.total ?? null },
      { key: "property_watch", count: by.property_watch?.total ?? null },
    ],
    byLifecycle: [
      { key: "open", label: "Open", count: read.reduce((sum, c) => sum + c.open, 0) },
      {
        key: "candidates",
        label: "Candidates",
        count: read.reduce((sum, c) => sum + c.candidate, 0),
      },
      {
        key: "recovering",
        label: "Recovering",
        count: read.reduce((sum, c) => sum + c.recovering, 0),
      },
    ],
    severity: { entries: countBySeverity(active.items), qualifiers },
    notJudged: n,
    notJudgedText: `${n.toLocaleString("en-US")} source${n === 1 ? "" : "s"} not judged`,
  };
}
