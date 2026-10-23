// Dashboard templates (UR.5.7): versioned layouts that live next to the registry, so the
// widget types and the layouts that name them have one source of truth. Templates are not
// stored rows. A test asserts every template widget is in the registry and `live`.

import type { DashboardTemplate, LayoutWidget } from "./types";

export type DescribedDashboardTemplate = DashboardTemplate & { description: string };

function at(
  id: string,
  type: string,
  x: number,
  y: number,
  w: number,
  h: number,
): LayoutWidget {
  return { id: `w_${id}`, type, x, y, w, h, range: null, config: {} };
}

/**
 * The Home. Positions: the KPI row (4 x 3 columns), fleet EPS
 * (8) beside EPS by class (4), top sources, severity and health (3 x 4), recent anomalies and
 * recent offenses (2 x 6). Left out until their data exists: the geography map.
 */
export const SOC_OVERVIEW: DescribedDashboardTemplate = {
  key: "soc_overview",
  version: 1,
  name: "SOC Overview",
  description:
    "Fleet volume, source health, anomalies and offenses. Geography is left out until its map is available.",
  layout: {
    v: 1,
    range: "24h",
    widgets: [
      at("kpieps", "kpi.total_eps", 0, 0, 3, 7),
      at("kpisrc", "kpi.log_sources", 3, 0, 3, 7),
      at("kpioff", "kpi.offenses", 6, 0, 3, 7),
      at("kpianom", "kpi.anomalies", 9, 0, 3, 7),
      at("epsfleet", "chart.eps_fleet_total", 0, 7, 8, 14),
      at("epsclass", "chart.eps_by_class", 8, 7, 4, 14),
      at("topsrc", "table.top_sources", 0, 21, 4, 15),
      at("sevchart", "chart.anomaly_severity", 4, 21, 4, 15),
      at("srchealth", "health.log_sources", 8, 21, 4, 15),
      at("recanom", "table.recent_anomalies", 0, 36, 6, 15),
      at("recoff", "table.recent_offenses", 6, 36, 6, 15),
    ],
  },
};

/** QRadar operation and collection health, without incident-triage widgets. */
export const QRADAR_ADMINISTRATION: DescribedDashboardTemplate = {
  key: "qradar_administration",
  version: 1,
  name: "QRadar Administration",
  description: "Search cadence, source classification, outage policy and detection evidence health.",
  layout: {
    v: 1,
    range: "24h",
    widgets: [
      at("admsrch", "health.collection", 0, 0, 6, 16),
      at("admdetect", "health.detection", 6, 0, 6, 9),
      at("admevid", "health.evidence", 6, 9, 6, 7),
      at("admcatch", "table.catchall_senders", 0, 16, 6, 14),
      at("admclass", "table.fleet_classes", 6, 16, 6, 14),
      at("admout", "table.outage_policy", 0, 30, 12, 10),
    ],
  },
};

/** The anomaly worklist and the observations needed to judge it. */
export const BEHAVIORAL_ANALYTICS: DescribedDashboardTemplate = {
  key: "behavioral_analytics",
  version: 1,
  name: "Behavioral Analytics",
  description:
    "Active anomalies, evidence health and the anomaly worklist. The observed-versus-expected chart is left out: it needs a log source chosen per dashboard.",
  layout: {
    v: 1,
    range: "24h",
    widgets: [
      at("behkpi", "kpi.anomalies", 0, 0, 4, 7),
      at("behevid", "health.evidence", 0, 7, 4, 8),
      at("behsev", "chart.anomaly_severity", 4, 0, 8, 15),
      at("behrecent", "table.recent_anomalies", 0, 15, 6, 15),
      at("behhealth", "health.log_sources", 6, 15, 6, 15),
    ],
  },
};

/** The source inventory's operational health and its highest-volume senders. */
export const LOG_SOURCE_MONITORING: DescribedDashboardTemplate = {
  key: "log_source_monitoring",
  version: 1,
  name: "Log Source Monitoring",
  description: "Source verdicts, traffic mix, source classes and catch-all senders requiring review.",
  layout: {
    v: 1,
    range: "24h",
    widgets: [
      at("loghealth", "health.log_sources", 0, 0, 4, 16),
      at("logtop", "table.top_sources", 4, 0, 4, 16),
      at("logclass", "chart.eps_by_class", 8, 0, 4, 16),
      at("logfleet", "table.fleet_classes", 0, 16, 6, 14),
      at("logcatch", "table.catchall_senders", 6, 16, 6, 14),
    ],
  },
};

/** WAF endpoint behavior and IPS findings; every placed type is currently live. */
export const WAF_IPS_MONITORING: DescribedDashboardTemplate = {
  key: "waf_ips_monitoring",
  version: 1,
  name: "WAF / IPS Monitoring",
  description:
    "WAF sources beside the latest WAF and IPS anomalies. The endpoint traffic chart is left out: it needs an endpoint chosen per dashboard.",
  layout: {
    v: 1,
    range: "24h",
    widgets: [
      at("wafsrc", "waf.sources", 0, 0, 12, 7),
      at("wafrecent", "table.recent_waf_anomalies", 0, 7, 6, 17),
      at("ipsrecent", "ips.source_anomalies", 6, 7, 6, 17),
    ],
  },
};

export const TEMPLATES: readonly DescribedDashboardTemplate[] = [
  SOC_OVERVIEW,
  QRADAR_ADMINISTRATION,
  BEHAVIORAL_ANALYTICS,
  LOG_SOURCE_MONITORING,
  WAF_IPS_MONITORING,
];

export const templateByKey = (key: string | null) =>
  key === null ? undefined : TEMPLATES.find((template) => template.key === key);
