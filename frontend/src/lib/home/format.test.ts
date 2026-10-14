import { describe, expect, it } from "vitest";

import { formatAgo, formatEps } from "./format";

describe("formatEps", () => {
  it("is an em dash for an unmeasured value and a real figure for a measured zero", () => {
    expect(formatEps(null)).toBe("—");
    expect(formatEps(undefined)).toBe("—");
    expect(formatEps(Number.NaN)).toBe("—");
    expect(formatEps(0)).toBe("0.00");
  });

  it("keeps the digits that carry meaning at each size", () => {
    expect(formatEps(0.0412)).toBe("0.04");
    expect(formatEps(12.345)).toBe("12.3");
    expect(formatEps(1240.6)).toBe("1,241");
  });
});

describe("formatAgo", () => {
  const now = Date.parse("2026-10-14T14:00:00Z");

  it("states minutes, hours and days", () => {
    expect(formatAgo("2026-10-14T13:59:30Z", now)).toBe("under 1 min ago");
    expect(formatAgo("2026-10-14T13:48:00Z", now)).toBe("12 min ago");
    expect(formatAgo("2026-10-14T09:00:00Z", now)).toBe("5 h ago");
    expect(formatAgo("2026-10-10T14:00:00Z", now)).toBe("4 d ago");
  });

  it("prints no age for a missing, unparsable or future instant", () => {
    expect(formatAgo(null, now)).toBeNull();
    expect(formatAgo("not a date", now)).toBeNull();
    expect(formatAgo("2026-10-14T15:00:00Z", now)).toBeNull();
  });
});
