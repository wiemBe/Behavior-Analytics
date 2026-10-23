// Recent IPS anomalies (UI-3.5b): the latest active anomalies of both IPS subject kinds.
//
// The list route filters by one subject kind, so the widget reads two bounded lists (entity and
// source-level, `limit` newest each) and merges them. The newest `limit` of the union always lie
// inside the union of the two newest-`limit` lists: a row in the union's top `limit` has fewer
// than `limit` newer rows in its own list, so its own list's top `limit` contains it. Two bounded
// reads are therefore enough, and no row is missed by cutting each list first.

import type { AnomalySummary, Page } from "@/lib/api";
import { ipsFigures } from "@/components/ips/ipsDetectors";
import { formatFigure } from "@/components/ips/ipsFormat";
import { ipsSubjectText, rowTypeLabel } from "@/components/ips/ipsLabels";
import { formatCount } from "@/lib/format";

export const IPS_ANOMALIES_LIMIT = 5;

export interface IpsAnomalyRow {
  id: string;
  detectedAt: string;
  subject: string;
  sourceName: string;
  typeLabel: string;
  severity: string;
  state: AnomalySummary["state"];
  /** The observed figure in the detector's unit; null when the detector's unit is unknown. */
  observed: string | null;
  expected: string | null;
  /** The unit in words ("events / bucket"); empty for a share (the % says it). */
  unitWords: string;
  href: string;
}

export interface IpsAnomaliesView {
  rows: IpsAnomalyRow[];
  /** Active IPS anomalies of both kinds, from the two totals. */
  total: number;
  badge: string;
  /** "Showing the latest 5 of 18"; null when every active anomaly is listed. */
  shown: string | null;
}

const UNIT_WORDS: Record<string, string> = {
  events: "events / bucket",
  ports: "distinct ports",
  since: "events since first appeared",
};

function row(a: AnomalySummary): IpsAnomalyRow {
  const f = ipsFigures(a, null);
  const none = a.anomaly_type === "IPS_NEW_DESTINATION_PORT";
  return {
    id: a.id,
    detectedAt: a.detected_at,
    subject: ipsSubjectText(a),
    sourceName: a.log_source_name ?? a.log_source_id ?? "Unknown source",
    typeLabel: rowTypeLabel(a),
    severity: a.severity,
    state: a.state,
    observed: f.unit == null ? null : formatFigure(f.unit, f.observed, a.observed_measurement),
    // A new port expects none: the figure is the word, never a 0.
    expected: f.unit == null ? null : none ? "none" : formatFigure(f.unit, f.expected),
    unitWords: f.unit == null ? "" : (UNIT_WORDS[f.unit] ?? ""),
    href: `/anomalies/${a.id}`,
  };
}

export function mergeIpsAnomalies(
  entities: Page<AnomalySummary>,
  sources: Page<AnomalySummary>,
  limit: number = IPS_ANOMALIES_LIMIT,
): IpsAnomaliesView {
  const seen = new Set<string>();
  const merged = [...entities.items, ...sources.items]
    .filter((a) => (seen.has(a.id) ? false : (seen.add(a.id), true)))
    // Newest first; the id breaks a tie so the order never depends on which list came first.
    .sort((a, b) => Date.parse(b.detected_at) - Date.parse(a.detected_at) || a.id.localeCompare(b.id))
    .slice(0, limit);
  const total = entities.total + sources.total;
  const n = (v: number) => formatCount(v);
  return {
    rows: merged.map(row),
    total,
    badge: `${n(total)} active`,
    shown:
      merged.length > 0 && total > merged.length
        ? `Showing the latest ${merged.length} of ${n(total)}`
        : null,
  };
}
