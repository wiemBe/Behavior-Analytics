// Recent IPS anomalies (UI-3.5b): the latest active anomalies of both IPS subject kinds.
//
// The widget reads two bounded lists (entity and source-level, `limit` newest each) and merges
// them. The newest `limit` of the union always lie
// inside the union of the two newest-`limit` lists: a row in the union's top `limit` has fewer
// than `limit` newer rows in its own list, so its own list's top `limit` contains it. Two bounded
// reads are therefore enough, and no row is missed by cutting each list first.

import type { AnomalySummary, Page } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { ipsFigures } from "@/components/ips/ipsDetectors";
import { ENGLISH_IPS } from "@/components/ips/ipsI18n";
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
  /** The same count without the "latest" lead, for the expanded widget. */
  shownShort: string | null;
}

const UNIT_KEYS: Record<string, string> = {
  events: "ips.unit.perBucket",
  ports: "ips.unit.ports",
  since: "ips.unit.sinceShort",
};

function row(a: AnomalySummary, i18n: BehaviorI18n): IpsAnomalyRow {
  const f = ipsFigures(a, null);
  const none = a.anomaly_type === "IPS_NEW_DESTINATION_PORT";
  return {
    id: a.id,
    detectedAt: a.detected_at,
    subject: ipsSubjectText(a, i18n),
    sourceName: a.log_source_name ?? a.log_source_id ?? i18n.t("ips.recent.unknownSource"),
    typeLabel: rowTypeLabel(a, i18n),
    severity: a.severity,
    state: a.state,
    observed: f.unit == null ? null : formatFigure(f.unit, f.observed, a.observed_measurement, i18n),
    // A new port expects none: the figure is the word, never a 0.
    expected:
      f.unit == null
        ? null
        : none
          ? i18n.t("common.none")
          : formatFigure(f.unit, f.expected, undefined, i18n),
    unitWords: f.unit == null ? "" : UNIT_KEYS[f.unit] ? i18n.t(UNIT_KEYS[f.unit]) : "",
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

export function mergeIpsAnomalies(
  entities: Page<AnomalySummary>,
  sources: Page<AnomalySummary>,
  limit: number = IPS_ANOMALIES_LIMIT,
  i18n: BehaviorI18n = ENGLISH_IPS,
): IpsAnomaliesView {
  const merged = mergedItems(entities, sources, limit);
  const total = entities.total + sources.total;
  const n = (v: number) => formatCount(v, i18n.locale);
  return {
    rows: merged.map((a) => row(a, i18n)),
    total,
    badge: i18n.t("ips.recent.badge", { count: n(total) }),
    shown:
      merged.length > 0 && total > merged.length
        ? i18n.t("ips.recent.shown", { count: n(merged.length), total: n(total) })
        : null,
    shownShort:
      merged.length > 0 && total > merged.length
        ? i18n.t("ips.recent.shownShort", { count: n(merged.length), total: n(total) })
        : null,
  };
}
