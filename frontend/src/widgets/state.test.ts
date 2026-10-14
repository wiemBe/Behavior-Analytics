import { describe, expect, it } from "vitest";

import { deriveStale, DEFAULT_CADENCE_SECONDS } from "./state";

const NOW = Date.parse("2026-10-14T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("deriveStale", () => {
  it("is fresh up to twice the cadence, and stale beyond it", () => {
    expect(deriveStale(ago(1800), 900, NOW)).toEqual({ stale: false, ageSeconds: 1800 });
    expect(deriveStale(ago(1801), 900, NOW)).toEqual({ stale: true, ageSeconds: 1801 });
  });

  it("assumes a 2 h cadence when it is unknown, so 4 h is the threshold", () => {
    expect(DEFAULT_CADENCE_SECONDS).toBe(7200);
    expect(deriveStale(ago(3 * 3600 + 59 * 60), null, NOW).stale).toBe(false);
    expect(deriveStale(ago(4 * 3600), null, NOW).stale).toBe(false);
    expect(deriveStale(ago(4 * 3600 + 60), null, NOW).stale).toBe(true);
    expect(deriveStale(ago(4 * 3600 + 60), 0, NOW).stale).toBe(true);
  });

  it("treats a time it cannot read as stale with no age", () => {
    expect(deriveStale("not a time", 900, NOW)).toEqual({ stale: true, ageSeconds: null });
  });

  it("treats a time ahead of now as fresh, with an age of zero", () => {
    expect(deriveStale(ago(-600), 900, NOW)).toEqual({ stale: false, ageSeconds: 0 });
  });

  it("accepts a Date for now", () => {
    expect(deriveStale(ago(10), 900, new Date(NOW)).ageSeconds).toBe(10);
  });
});
