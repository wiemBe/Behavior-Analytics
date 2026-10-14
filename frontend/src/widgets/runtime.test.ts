import { describe, expect, it } from "vitest";

import { DEFAULT_RANGE, effectiveRange, RANGE_TEXT } from "./runtime";

describe("effective range", () => {
  it("is the widget's override when it has one", () => {
    expect(effectiveRange("7d", "24h")).toBe("7d");
    expect(effectiveRange("1h", "7d")).toBe("1h");
  });

  it("is the dashboard's range when the widget follows it", () => {
    expect(effectiveRange(null, "7d")).toBe("7d");
    expect(effectiveRange(undefined, "1h")).toBe("1h");
  });

  it("defaults to 24 h and names every preset with a unit", () => {
    expect(DEFAULT_RANGE).toBe("24h");
    expect(RANGE_TEXT).toEqual({ "1h": "1 h", "24h": "24 h", "7d": "7 d" });
  });
});
