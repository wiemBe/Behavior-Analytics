import { describe, expect, it } from "vitest";

import { newestCapture, parsePad, windowText } from "./related";

describe("parsePad", () => {
  it("accepts the four pads", () => {
    expect(["30m", "1h", "2h", "6h"].map(parsePad)).toEqual(["30m", "1h", "2h", "6h"]);
  });
  it("falls back to 2h for anything else, never to an error", () => {
    expect(parsePad(undefined)).toBe("2h");
    expect(parsePad("12h")).toBe("2h");
    expect(parsePad("")).toBe("2h");
    expect(parsePad("constructor")).toBe("2h");
  });
  it("takes the first of a repeated parameter", () => {
    expect(parsePad(["6h", "30m"])).toBe("6h");
  });
});

describe("windowText", () => {
  it("is a clock range inside one UTC day", () => {
    expect(windowText("2026-11-05T16:30:00Z", "2026-11-05T20:30:00Z")).toBe("16:30–20:30 UTC");
  });
  it("names both dates across midnight", () => {
    expect(windowText("2026-11-05T22:00:00Z", "2026-11-06T02:00:00Z")).toBe(
      "2026-11-05 22:00 – 2026-11-06 02:00 UTC",
    );
  });
  it("is null for an unreadable bound", () => {
    expect(windowText("nope", "2026-11-06T02:00:00Z")).toBeNull();
  });
});

describe("newestCapture", () => {
  it("is the latest readable instant", () => {
    expect(newestCapture(["2026-11-05T18:00:00Z", "2026-11-05T18:30:00Z", "bad"])).toBe(
      Date.parse("2026-11-05T18:30:00Z"),
    );
  });
  it("is null for nothing", () => {
    expect(newestCapture([])).toBeNull();
  });
});
