// SOC-14b: the Latest handover widget is in the SOC Overview only, on a row of its own.

import { describe, expect, it } from "vitest";

import { widgetDef } from "./registry";
import { SOC_OVERVIEW, TEMPLATES } from "./templates";

describe("the Latest handover widget", () => {
  const def = widgetDef("table.handover")!;

  it("is a live table that reads /handover/latest and drills down to /handover in every config", () => {
    expect(def.status).toBe("live");
    expect(def.category).toBe("Table");
    expect(def.requires).toEqual(["read:anomalies"]);
    expect(def.sources).toEqual(["/handover/latest"]);
    expect(def.size).toEqual({ w: 6, h: 9, minW: 4, minH: 7 });
    expect(def.range).toBeNull();
    expect(def.drillDown({})).toBe("/handover");
  });

  it("is placed once, in the SOC Overview only, on a row no other widget shares", () => {
    const placed = SOC_OVERVIEW.layout.widgets.filter((w) => w.type === "table.handover");
    expect(placed).toHaveLength(1);
    expect(placed[0].id).toBe("w_handover");
    expect(placed[0].h).toBeGreaterThanOrEqual(def.size.minH);
    expect(placed[0].w).toBeGreaterThanOrEqual(def.size.minW);
    const sharing = SOC_OVERVIEW.layout.widgets.filter(
      (w) => w !== placed[0] && w.y < placed[0].y + placed[0].h && placed[0].y < w.y + w.h,
    );
    expect(sharing).toEqual([]);
    for (const t of TEMPLATES.filter((x) => x.key !== "soc_overview")) {
      expect(t.layout.widgets.some((w) => w.type === "table.handover"), t.key).toBe(false);
    }
  });
});
