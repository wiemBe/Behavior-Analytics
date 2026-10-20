import { describe, expect, it } from "vitest";

import { PROPERTY_WATCH_OPTIONS } from "./optionSources";
import { widgetDef } from "./registry";
import { isReadField } from "./types";

import { watch } from "@/components/propertywatch/fixtures";

describe("chart.property_watch in the registry", () => {
  const def = widgetDef("chart.property_watch");

  it("is a live, expandable chart that needs read:property_watches", () => {
    expect(def?.status).toBe("live");
    expect(def?.category).toBe("Chart");
    expect(def?.requires).toEqual(["read:property_watches"]);
    expect(def?.expandable).toBe(true);
    expect(def?.sources.some((s) => s.includes("/series"))).toBe(true);
  });

  it("takes the watch from an option read, never from free text, and no AQL", () => {
    const field = def?.config.watch_id;
    expect(field && isReadField(field)).toBe(true);
    expect(Object.keys(def?.config ?? {})).toEqual(["watch_id"]);
    expect(def?.defaultConfig).toEqual({ watch_id: null });
  });

  it("drills down to the watch in every config, and to the list when none is chosen", () => {
    expect(def?.drillDown({ watch_id: "abc" })).toBe("/behavior/watches/abc");
    expect(def?.drillDown({ watch_id: null })).toBe("/behavior/watches");
  });
});

describe("PROPERTY_WATCH_OPTIONS", () => {
  it("reads GET /property-watches and offers ids with names, sorted, disabled ones marked", async () => {
    const read = PROPERTY_WATCH_OPTIONS.read(null);
    expect(read?.key).toBe("/property-watches");
    const list = read?.options([
      watch({ id: "2", name: "Zeta" }),
      watch({ id: "1", name: "Alpha", enabled: false }),
    ]);
    expect(list).toEqual({
      options: [
        { id: "1", label: "Alpha (disabled)" },
        { id: "2", label: "Zeta" },
      ],
      total: 2,
    });
  });

  it("says why it may be empty", () => {
    expect(PROPERTY_WATCH_OPTIONS.emptyReason).toMatch(/No property watch exists/);
  });
});
