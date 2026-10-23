// EPS by source class needs 14 grid rows at 1440x900 to hold its donut, the completeness note
// (rendered first, under the badge) and five class rows without an inner scroll (UI-7.2, measured
// in the production build at the three viewports). A saved layout is not migrated.

import { describe, expect, it } from "vitest";

import { widgetDef } from "./registry";
import { SOC_OVERVIEW } from "./templates";

const EPS_BY_CLASS_ROWS = 14;

describe("EPS by source class default height", () => {
  it("is tall enough in the SOC Overview template", () => {
    const tile = SOC_OVERVIEW.layout.widgets.find((w) => w.type === "chart.eps_by_class")!;
    expect(tile.h).toBeGreaterThanOrEqual(EPS_BY_CLASS_ROWS);
  });

  it("is tall enough where the catalog places it", () => {
    expect(widgetDef("chart.eps_by_class")!.size.h).toBeGreaterThanOrEqual(EPS_BY_CLASS_ROWS);
  });

  it("leaves no widget of the template on top of another", () => {
    const widgets = SOC_OVERVIEW.layout.widgets;
    for (const [i, a] of widgets.entries()) {
      for (const b of widgets.slice(i + 1)) {
        const apart =
          a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
        expect(apart, `${a.id} and ${b.id}`).toBe(true);
      }
    }
  });
});
