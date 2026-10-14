// Recent anomalies (UR.4.3): the five latest active ones, from
// GET /anomalies?active_only=true&limit=5, with the response's `total` as the badge.
//
// The badge says "n active", never "new": "new" has no definition here. An empty
// list is not "all clear": sources without a baseline are not being judged, and the
// empty text says how many.

import type { AnomalyState, AnomalySummary, BehaviorSummary, Page } from "@/lib/api";
import { ipsSubjectText, isIpsRow, rowTypeLabel } from "@/components/ips/ipsLabels";
import { endpointLabel, isWafRow } from "@/components/waf/wafLabels";

export const RECENT_ANOMALIES_LIMIT = 5;

export interface RecentAnomalyRow {
  id: string;
  detectedAt: string;
  /** What the anomaly is about: the source, `METHOD /path` for a WAF row, or the entity
   *  (or "Source-level") for an IPS row. */
  subject: string;
  /** The source's name, shown beneath a WAF or IPS row's subject; null otherwise. */
  sourceName: string | null;
  sourceHref: string;
  typeLabel: string;
  severity: string;
  state: AnomalyState;
  /** Observed over expected; null when no ratio exists, never 0. */
  deviation: number | null;
  href: string;
}

export interface RecentAnomaliesView {
  rows: RecentAnomalyRow[];
  active: number;
  /** "18 active" */
  badge: string;
  /** "Showing the latest 5 of 18"; null when every active anomaly is listed. */
  shown: string | null;
  /** The empty state's words; null when there are rows. */
  empty: string | null;
}

export function notJudgedText(n: number): string {
  return `${n.toLocaleString("en-US")} source${n === 1 ? "" : "s"}`;
}

export function recentAnomalies(
  active: Page<AnomalySummary>,
  summary: BehaviorSummary | null,
): RecentAnomaliesView {
  const rows: RecentAnomalyRow[] = active.items.slice(0, RECENT_ANOMALIES_LIMIT).map((a) => {
    const source = a.log_source_name ?? a.log_source_id;
    const waf = isWafRow(a) && a.waf_endpoint != null;
    const ips = isIpsRow(a);
    return {
      id: a.id,
      detectedAt: a.detected_at,
      subject: ips
        ? ipsSubjectText(a)
        : waf && a.waf_endpoint
          ? endpointLabel(a.waf_endpoint)
          : source,
      sourceName: waf || ips ? source : null,
      // No IPS behavior page exists for an analyst yet, and the Phase A source page
      // charts EPS: an IPS row links to its own investigation.
      sourceHref: ips
        ? `/anomalies/${a.id}`
        : waf
          ? `/behavior/waf/sources/${a.log_source_id}`
          : `/behavior/sources/${a.log_source_id}`,
      typeLabel: rowTypeLabel(a),
      severity: a.severity,
      state: a.state,
      deviation: a.deviation_ratio,
      href: `/anomalies/${a.id}`,
    };
  });
  const total = active.total;
  let empty: string | null = null;
  if (rows.length === 0) {
    empty =
      summary === null
        ? "No active anomalies. How many sources are not being judged could not be read."
        : `No active anomalies. Sources with no baseline are not being judged: ${notJudgedText(
            summary.insufficient_data_sources,
          )}.`;
  }
  return {
    rows,
    active: total,
    badge: `${total.toLocaleString("en-US")} active`,
    shown:
      total > rows.length && rows.length > 0
        ? `Showing the latest ${rows.length} of ${total.toLocaleString("en-US")}`
        : null,
    empty,
  };
}
