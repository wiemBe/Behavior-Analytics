// The widget registry (UR.5.1): every widget the catalog can place, in one list.
//
// The Phase 2 Home components are registered as they are: each one reads its own data through
// `useApiResource` and renders its own frame and states, so the registry wraps them and does
// not rewrite them. The catalog lists only what exists. A `blocked` entry is a widget whose
// data does not exist yet; the catalog shows it disabled with the reason and no layout can
// hold it (the registry test enforces that for the templates).

import dynamic from "next/dynamic";
import type { ComponentType } from "react";

import { placeholderFor } from "./Placeholder";
import type { AnyWidgetDef, WidgetCategory, WidgetProps, WidgetSize } from "./types";

type NoConfig = Record<string, never>;

/** Loads a Phase 2 component with `next/dynamic`, `ssr: false`, under a text-only state. */
function load(
  title: string,
  kpi: boolean,
  loader: () => Promise<ComponentType>,
): ComponentType<WidgetProps<NoConfig>> {
  return dynamic(loader, {
    ssr: false,
    loading: placeholderFor(title, kpi),
  }) as unknown as ComponentType<WidgetProps<NoConfig>>;
}

interface LiveInput {
  type: string;
  title: string;
  description: string;
  category: WidgetCategory;
  requires: string[];
  sources: string[];
  size: WidgetSize;
  drillDown: string;
  loader: () => Promise<ComponentType>;
}

function live(input: LiveInput): AnyWidgetDef {
  const { drillDown, loader, ...def } = input;
  return {
    ...def,
    status: "live",
    range: null,
    config: {},
    defaultConfig: {},
    drillDown: () => drillDown,
    Component: load(def.title, def.category === "KPI", loader),
  };
}

function blocked(
  input: Pick<AnyWidgetDef, "type" | "title" | "description" | "category" | "size"> & {
    blockedBy: string;
  },
): AnyWidgetDef {
  return {
    ...input,
    requires: [],
    sources: [],
    status: "blocked",
    range: null,
    config: {},
    defaultConfig: {},
    drillDown: () => null,
    // A blocked widget is never placed, so it has nothing to render.
    Component: placeholderFor(input.title, false) as unknown as ComponentType<WidgetProps<NoConfig>>,
  };
}

// Sizes are grid cells of the 12-column layout (row heights: `--dash-row` in tokens.css).
const KPI: WidgetSize = { w: 3, h: 7, minW: 2, minH: 6, maxW: 6 };
const WIDE: WidgetSize = { w: 8, h: 12, minW: 6, minH: 10 };
const THIRD: WidgetSize = { w: 4, h: 12, minW: 3, minH: 10 };
const PANEL: WidgetSize = { w: 4, h: 15, minW: 3, minH: 11 };
const HALF: WidgetSize = { w: 6, h: 15, minW: 4, minH: 11 };

export const WIDGETS: readonly AnyWidgetDef[] = [
  live({
    type: "kpi.total_eps",
    title: "Total EPS",
    description: "Fleet events per second over 24 h, against the baselines that exist.",
    category: "KPI",
    requires: ["read:fleet"],
    sources: ["/fleet/summary", "/fleet/eps-series"],
    size: KPI,
    drillDown: "/fleet",
    loader: () => import("@/components/home/KpiTotalEps").then((m) => m.KpiTotalEps),
  }),
  live({
    type: "kpi.log_sources",
    title: "Log sources",
    description: "How many sources are monitored now and how many are judged normal.",
    category: "KPI",
    requires: ["read:anomalies", "read:overview"],
    sources: ["/anomalies/summary", "/behavior/sources", "/overview"],
    size: KPI,
    drillDown: "/behavior/sources",
    loader: () => import("@/components/home/KpiLogSources").then((m) => m.KpiLogSources),
  }),
  live({
    type: "kpi.offenses",
    title: "Open offenses",
    description: "Open offenses in the latest snapshot, by severity.",
    category: "KPI",
    requires: ["read:offenses"],
    sources: ["/offenses/analytics", "/offenses"],
    size: KPI,
    drillDown: "/offenses?status=OPEN",
    loader: () => import("@/components/home/KpiOffenses").then((m) => m.KpiOffenses),
  }),
  live({
    type: "kpi.anomalies",
    title: "Active anomalies",
    description: "Anomalies active now, by detector and lifecycle.",
    category: "KPI",
    requires: ["read:anomalies"],
    sources: ["/anomalies/summary"],
    size: KPI,
    drillDown: "/anomalies?active_only=true",
    loader: () => import("@/components/home/KpiAnomalies").then((m) => m.KpiAnomalies),
  }),
  live({
    type: "chart.eps_fleet_total",
    title: "Fleet EPS",
    description: "Observed fleet EPS against the sum of per-source expected EPS, 1 h to 7 d.",
    category: "Chart",
    requires: ["read:fleet"],
    sources: ["/fleet/eps-series"],
    size: WIDE,
    drillDown: "/fleet",
    loader: () => import("@/components/home/EpsChart").then((m) => m.EpsChart),
  }),
  live({
    type: "chart.eps_by_class",
    title: "EPS by source class",
    description: "Mean EPS of each source class over 24 h, with its share of the fleet.",
    category: "Chart",
    requires: ["read:fleet"],
    sources: ["/fleet/summary"],
    size: THIRD,
    drillDown: "/fleet",
    loader: () => import("@/components/home/EpsByClass").then((m) => m.EpsByClass),
  }),
  live({
    type: "table.top_sources",
    title: "Top log sources by EPS",
    description: "The five busiest sources over 24 h with their deviation from expected.",
    category: "Table",
    requires: ["read:fleet", "read:anomalies"],
    sources: ["/fleet/summary", "/behavior/sources"],
    size: PANEL,
    drillDown: "/behavior/sources",
    loader: () => import("@/components/home/TopSources").then((m) => m.TopSources),
  }),
  live({
    type: "chart.anomaly_severity",
    title: "Anomalies by severity",
    description: "Anomalies per hour over 24 h, stacked by severity (the latest 200 are read).",
    category: "Chart",
    requires: ["read:anomalies"],
    sources: ["/anomalies"],
    size: PANEL,
    drillDown: "/anomalies",
    loader: () => import("@/components/home/SeverityChart").then((m) => m.SeverityChart),
  }),
  live({
    type: "health.log_sources",
    title: "Log source health",
    description: "Sources by verdict now, and the categories that need a decision.",
    category: "Health",
    requires: ["read:anomalies", "read:fleet"],
    sources: ["/behavior/sources", "/anomalies/summary", "/fleet/summary", "/overview"],
    size: PANEL,
    drillDown: "/behavior/sources",
    loader: () => import("@/components/home/LogSourceHealth").then((m) => m.LogSourceHealth),
  }),
  live({
    type: "table.recent_anomalies",
    title: "Recent anomalies",
    description: "The latest active anomalies with their deviation and a link to investigate.",
    category: "Table",
    requires: ["read:anomalies"],
    sources: ["/anomalies", "/anomalies/summary"],
    size: HALF,
    drillDown: "/anomalies",
    loader: () => import("@/components/home/RecentAnomalies").then((m) => m.RecentAnomalies),
  }),
  live({
    type: "table.recent_offenses",
    title: "Recent offenses",
    description: "The latest open offenses by last update, with their magnitude.",
    category: "Table",
    requires: ["read:offenses"],
    sources: ["/offenses"],
    size: HALF,
    drillDown: "/offenses",
    loader: () => import("@/components/home/RecentOffenses").then((m) => m.RecentOffenses),
  }),
  blocked({
    type: "map.geography",
    title: "Geography",
    description: "Where traffic comes from, by country.",
    category: "Chart",
    size: { w: 6, h: 15, minW: 4, minH: 11 },
    blockedBy: "the map widget is not built yet; geo collection is",
  }),
  blocked({
    type: "ips.source_anomalies",
    title: "IPS source anomalies",
    description: "Behavioral anomalies of IPS log sources.",
    category: "Table",
    size: { w: 6, h: 15, minW: 4, minH: 11 },
    blockedBy: "the IPS analyst API is being built",
  }),
];

const BY_TYPE = new Map(WIDGETS.map((w) => [w.type, w]));

/** The registry entry for a layout's `type`, or undefined (rendered "Unsupported widget"). */
export function widgetDef(type: string): AnyWidgetDef | undefined {
  return BY_TYPE.get(type);
}
