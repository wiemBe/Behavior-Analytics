"use client";

// The Home KPI row: four cards of four different structures (UR.4.2). Each one
// reads its own data and renders its own states, so one refused or failed read
// leaves the other three standing.

import { KpiAnomalies } from "./KpiAnomalies";
import { KpiLogSources } from "./KpiLogSources";
import { KpiOffenses } from "./KpiOffenses";
import { KpiTotalEps } from "./KpiTotalEps";

import "./kpi.css";

export function KpiRow() {
  return (
    <section className="kpi-row" aria-label="Key figures">
      <div className="kpi-grid">
        <KpiTotalEps />
        <KpiLogSources />
        <KpiOffenses />
        <KpiAnomalies />
      </div>
    </section>
  );
}
