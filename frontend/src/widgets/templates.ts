// Dashboard templates (UR.5.7): versioned layouts that live next to the registry, so the
// widget types and the layouts that name them have one source of truth. Templates are not
// stored rows. A test asserts every template widget is in the registry and `live`.

import type { DashboardTemplate, LayoutWidget } from "./types";

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
 * The Home. Positions reproduce the Phase 2 Home: the KPI row (4 x 3 columns), fleet EPS
 * (8) beside EPS by class (4), top sources, severity and health (3 x 4), recent anomalies and
 * recent offenses (2 x 6). Left out until their data exists: the geography map.
 */
export const SOC_OVERVIEW: DashboardTemplate = {
  key: "soc_overview",
  version: 1,
  name: "SOC Overview",
  layout: {
    v: 1,
    range: "24h",
    widgets: [
      at("kpieps", "kpi.total_eps", 0, 0, 3, 7),
      at("kpisrc", "kpi.log_sources", 3, 0, 3, 7),
      at("kpioff", "kpi.offenses", 6, 0, 3, 7),
      at("kpianom", "kpi.anomalies", 9, 0, 3, 7),
      at("epsfleet", "chart.eps_fleet_total", 0, 7, 8, 12),
      at("epsclass", "chart.eps_by_class", 8, 7, 4, 12),
      at("topsrc", "table.top_sources", 0, 19, 4, 15),
      at("sevchart", "chart.anomaly_severity", 4, 19, 4, 15),
      at("srchealth", "health.log_sources", 8, 19, 4, 15),
      at("recanom", "table.recent_anomalies", 0, 34, 6, 15),
      at("recoff", "table.recent_offenses", 6, 34, 6, 15),
    ],
  },
};

export const TEMPLATES: readonly DashboardTemplate[] = [SOC_OVERVIEW];
