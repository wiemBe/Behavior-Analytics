import { describe, expect, it } from "vitest";

import { WIDGETS } from "./registry";

describe("registry range and expand declarations (UI-3.3)", () => {
  // UI-3.5b: the two subject charts take the same presets, as `since`/`until` on their reads.
  it("declares the windows on exactly the seven widgets whose reads take one", () => {
    const ranged = WIDGETS.filter((w) => w.range !== null);
    expect(ranged.map((w) => w.type)).toEqual([
      "kpi.total_eps",
      "chart.eps_fleet_total",
      "chart.eps_by_class",
      "table.top_sources",
      "table.fleet_classes",
      "waf.endpoint_traffic",
      "chart.eps_observed_expected",
    ]);
    for (const def of ranged) expect(def.range, def.type).toEqual(["1h", "24h", "7d"]);
  });

  it("makes charts and tables expandable, and KPI, health and admin cards not", () => {
    for (const def of WIDGETS) {
      const chartOrTable = def.category === "Chart" || def.category === "Table";
      expect(def.expandable, def.type).toBe(chartOrTable && def.status === "live");
    }
  });
});
