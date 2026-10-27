import { describe, expect, it } from "vitest";

import { widgetDef } from "./registry";

// Eight search-family rows need 18 grid rows at every supported viewport. This pins the catalog
// placement default to the measured height; existing saved layouts are not migrated.
describe("Scheduled searches default height", () => {
  it("fits all eight families without an inner scrollbar", () => {
    expect(widgetDef("health.collection")!.size).toEqual({
      w: 6,
      h: 18,
      minW: 4,
      minH: 10,
    });
  });
});
