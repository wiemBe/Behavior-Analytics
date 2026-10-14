"use client";

// The Home (UR.4): the SOC Overview template rendered through `DashboardView` (UR.5).
//
//   KPI row (4)
//   Fleet EPS (8)                     | EPS by source class (4)
//   Top sources (4) | Severity (4)    | Log-source health (4)
//   Recent anomalies (6)              | Recent offenses (6)
//
// The widgets, their positions and their sizes are the template's (`widgets/templates.ts`).
// Every widget reads through `useApiResource`, so the shell's refresh tick refreshes them
// all, and each renders its own states: one refused or failed read leaves the others
// standing. The page itself reads nothing but the fleet series' end, which the top bar
// shows as the data age (the request is the same one the KPI card and the chart make, so
// it is shared, not repeated).

import { PageHeader } from "@/components/ui/PageHeader";
import { useReportDataAge } from "@/components/shell/AutoRefresh";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";

import { DashboardView } from "@/widgets/DashboardView";
import { SOC_OVERVIEW } from "@/widgets/templates";

import "./dashboard.css";

export function HomeDashboard() {
  const series = homeReads.epsSeries("24h", "none");
  const state = useApiResource(series.key, series.fetch);
  // Only a series that was read says how far the data reaches; anything else is unknown.
  useReportDataAge(state.status === "ok" ? state.data.window_end : null);

  return (
    <div className="home">
      <DashboardView
        layout={SOC_OVERVIEW.layout}
        renderHeader={(actions) => (
          <PageHeader
            title="Dashboard"
            description="24 h view of fleet volume, source health, anomalies and offenses. Times are UTC."
            actions={actions}
          />
        )}
      />
    </div>
  );
}
