import { describe, expect, it } from "vitest";

import { formatAgo, signedPercent } from "./format";

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

describe("signedPercent", () => {
  it("uses a true minus for a negative value", () => {
    expect(signedPercent(-3.94)).toBe("−3.9 %");
    expect(signedPercent(-3.94)).not.toContain("-");
  });
  it("signs a positive value and leaves zero bare", () => {
    expect(signedPercent(3.2)).toBe("+3.2 %");
    expect(signedPercent(0)).toBe("0.0 %");
  });
});
