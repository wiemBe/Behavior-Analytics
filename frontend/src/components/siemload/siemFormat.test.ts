import { describe, expect, it } from "vitest";

import { DEFAULT_RANGE, formatMs, parseRange, rangeHours, rangeHref } from "./siemFormat";

describe("formatMs", () => {
  it("carries its unit: ms under a second, s under a minute, then the shared duration", () => {
    expect(formatMs(820)).toBe("820 ms");
    expect(formatMs(4230)).toBe("4.2 s");
    expect(formatMs(59_949)).toBe("59.9 s");
    expect(formatMs(125_000)).toBe("2 min 5 s");
    expect(formatMs(3_600_000)).toBe("1 h");
  });

  it("keeps a measured zero apart from an unmeasured figure", () => {
    expect(formatMs(0)).toBe("0 ms");
    expect(formatMs(null)).toBe("—");
    expect(formatMs(undefined)).toBe("—");
    expect(formatMs(Number.NaN)).toBe("—");
    expect(formatMs(-5)).toBe("—");
  });
});

describe("the summary range", () => {
  it("reads 1 h, 24 h, 7 d and 30 d as 1, 24, 168 and 720 hours", () => {
    expect(["1h", "24h", "7d", "30d"].map((id) => rangeHours(parseRange(id)))).toEqual([1, 24, 168, 720]);
  });

  it("falls back to 24 h for anything else, never to a value the API would refuse", () => {
    for (const raw of [undefined, "", "2h", "9999", "24H", "constructor", "1h&x=1"]) {
      expect(parseRange(raw)).toBe(DEFAULT_RANGE);
    }
    expect(rangeHours(DEFAULT_RANGE)).toBe(24);
  });

  it("keeps the default off the URL and puts the others in the range parameter", () => {
    expect(rangeHref("24h")).toBe("/admin/siem-load");
    expect(rangeHref("7d")).toBe("/admin/siem-load?range=7d");
  });
});
