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

function mergedItems(
  entities: Page<AnomalySummary>,
  sources: Page<AnomalySummary>,
  limit: number,
): AnomalySummary[] {
  const seen = new Set<string>();
  return [...entities.items, ...sources.items]
    .filter((a) => (seen.has(a.id) ? false : (seen.add(a.id), true)))
    // Newest first; the id breaks a tie so the order never depends on which list came first.
    .sort((a, b) => Date.parse(b.detected_at) - Date.parse(a.detected_at) || a.id.localeCompare(b.id))
    .slice(0, limit);
}

export type IpsKind = "IPS_ENTITY" | "IPS_SOURCE";

export interface IpsDrill {
  kind: IpsKind;
  href: string;
  label: string;
  ariaLabel: string;
  otherHref: string;
  otherLabel: string;
}

const IPS_DRILL: Record<IpsKind, { href: string; label: string; ariaLabel: string }> = {
  IPS_ENTITY: {
    href: "/anomalies?subject_kind=IPS_ENTITY&active_only=true",
    label: "Entities",
    ariaLabel: "View active IPS entity anomalies",
  },
  IPS_SOURCE: {
    href: "/anomalies?subject_kind=IPS_SOURCE&active_only=true",
    label: "Sources",
    ariaLabel: "View active IPS source anomalies",
  },
};

/** While the two lists are still loading, the count is unknown, so the link opens entities. */
export const IPS_DRILL_UNKNOWN: IpsDrill = {
  kind: "IPS_ENTITY",
  ...IPS_DRILL.IPS_ENTITY,
  otherHref: IPS_DRILL.IPS_SOURCE.href,
  otherLabel: "Source-level anomalies",
};

/**
 * The anomalies page can select one IPS kind. The header opens the kind this widget
 * shows most of (the rows on screen, then the totals). A tie stays on entities.
 */
export function ipsMajorityKind(
  shown: readonly { subject_kind?: string | null }[],
  entityTotal: number,
  sourceTotal: number,
): IpsKind {
  let entityShown = 0;
  let sourceShown = 0;
  for (const row of shown) {
    if (row.subject_kind === "IPS_ENTITY") entityShown += 1;
    else if (row.subject_kind === "IPS_SOURCE") sourceShown += 1;
  }
  if (sourceShown > entityShown) return "IPS_SOURCE";
  if (entityShown > sourceShown) return "IPS_ENTITY";
  if (sourceTotal > entityTotal) return "IPS_SOURCE";
  return "IPS_ENTITY";
}

export function ipsDrillLink(
  entities: Page<AnomalySummary>,
  sources: Page<AnomalySummary>,
  limit: number = IPS_ANOMALIES_LIMIT,
): IpsDrill {
  const kind = ipsMajorityKind(mergedItems(entities, sources, limit), entities.total, sources.total);
  const other: IpsKind = kind === "IPS_ENTITY" ? "IPS_SOURCE" : "IPS_ENTITY";
  return {
    kind,
    ...IPS_DRILL[kind],
    otherHref: IPS_DRILL[other].href,
    otherLabel: other === "IPS_SOURCE" ? "Source-level anomalies" : "Entity anomalies",
  };
}

export function mergeIpsAnomalies(
  entities: Page<AnomalySummary>,
  sources: Page<AnomalySummary>,
  limit: number = IPS_ANOMALIES_LIMIT,
): IpsAnomaliesView {
  const merged = mergedItems(entities, sources, limit);
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
