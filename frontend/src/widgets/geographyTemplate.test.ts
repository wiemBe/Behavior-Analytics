// GEO-4: the SOC Overview carries the Geography map across the bottom of the Home.

import { describe, expect, it } from "vitest";

import { widgetDef } from "./registry";
import { SOC_OVERVIEW, TEMPLATES } from "./templates";

describe("the SOC Overview and the map", () => {
  const placed = SOC_OVERVIEW.layout.widgets.find((w) => w.type === "map.geography");

  it("places the map once, below every other widget and across the full width", () => {
    expect(SOC_OVERVIEW.layout.widgets.filter((w) => w.type === "map.geography")).toHaveLength(1);
    expect(placed).toBeDefined();
    const others = SOC_OVERVIEW.layout.widgets.filter((w) => w !== placed);
    expect(placed!.x).toBe(0);
    expect(placed!.w).toBe(12);
    expect(placed!.y).toBeGreaterThanOrEqual(Math.max(...others.map((w) => w.y + w.h)));
  });

  it("gives the map at least its own minimum height, and stores the default configuration", () => {
    expect(placed!.h).toBeGreaterThanOrEqual(widgetDef("map.geography")!.size.minH);
    expect(placed!.config).toEqual({});
  });

  it("no longer says the geography is left out", () => {
    expect(SOC_OVERVIEW.description).not.toMatch(/left out/i);
    expect(SOC_OVERVIEW.description).toMatch(/countries/);
  });
});

// GEO-5: a dashboard created from a template records the template's version (`template_version`),
// and nothing compares it with the current one, so the number only labels what the layout was.
// The SOC Overview's layout changed in GEO-4, so its version is 2; the other templates did not.
describe("the template versions", () => {
  it("is 2 for the SOC Overview, whose layout gained the map", () => {
    expect(SOC_OVERVIEW.version).toBe(2);
  });

  it("is unchanged for the templates whose layout did not change", () => {
    const others = TEMPLATES.filter((t) => t.key !== "soc_overview");
    expect(others.length).toBeGreaterThan(0);
    expect(others.map((t) => t.version)).toEqual(others.map(() => 1));
  });
});
