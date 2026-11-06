import { describe, expect, it } from "vitest";

import {
  MAX_RANGE_DAYS,
  RANGE_CHIPS,
  parseRange,
  rangeQuery,
  rangeWindow,
  stampText,
  urlInstant,
} from "./timeRange";

const NOW = Date.parse("2026-11-05T19:05:00Z");

describe("the range chips", () => {
  it("offers 1h, 24h, 7d and 30d, none wider than the backend's ceiling", () => {
    expect(RANGE_CHIPS.map((c) => c.key)).toEqual(["1h", "24h", "7d", "30d"]);
    expect(Math.max(...RANGE_CHIPS.map((c) => c.ms))).toBeLessThanOrEqual(
      MAX_RANGE_DAYS * 86_400_000,
    );
  });

  it("resolves a chip against the clock it is given and leaves the end open", () => {
    const r = parseRange({ range: "24h" }, NOW);
    expect(r.chip).toBe("24h");
    expect(r.since).toBe("2026-11-04T19:05:00.000Z");
    expect(r.until).toBe("");
    expect(r.custom).toBe(false);
  });

  it("stays relative in the URL: the key is the chip, never the instants it resolved to", () => {
    expect(rangeQuery(parseRange({ range: "7d" }, NOW))).toEqual({ range: "7d" });
    // A later clock moves the window, not the link.
    const later = parseRange({ range: "7d" }, NOW + 3_600_000);
    expect(later.since).toBe("2026-10-29T20:05:00.000Z");
    expect(rangeQuery(later)).toEqual({ range: "7d" });
  });
});

describe("a Custom window", () => {
  it("writes absolute UTC instants and no chip", () => {
    const r = parseRange({ since: "2026-11-05T00:00", until: "2026-11-05T12:30" }, NOW);
    expect(r.custom).toBe(true);
    expect(r.chip).toBe("");
    expect(rangeQuery(r)).toEqual({
      since: "2026-11-05T00:00:00Z",
      until: "2026-11-05T12:30:00Z",
    });
  });

  it("reads a value with no zone as UTC, not as the server's zone", () => {
    expect(parseRange({ since: "2026-11-05T10:00" }, NOW).since).toBe("2026-11-05T10:00:00.000Z");
    expect(parseRange({ since: "2026-11-05T10:00+02:00" }, NOW).since).toBe(
      "2026-11-05T08:00:00.000Z",
    );
  });

  it("swaps an inverted window and says so", () => {
    const r = parseRange({ since: "2026-11-05T12:00Z", until: "2026-11-05T08:00Z" }, NOW);
    expect(r.since < r.until).toBe(true);
    expect(r.issues).toContain("swapped");
  });

  it("narrows a window wider than the ceiling and says so", () => {
    const r = parseRange({ since: "2020-01-01T00:00Z", until: "2026-11-05T00:00Z" }, NOW);
    expect(Date.parse(r.until) - Date.parse(r.since)).toBe(MAX_RANGE_DAYS * 86_400_000);
    expect(r.issues).toContain("clamped");
  });
});

describe("values that do not parse", () => {
  it("drops an unknown chip and names it", () => {
    const r = parseRange({ range: "2d" }, NOW);
    expect(r.chip).toBe("");
    expect(r.since).toBe("");
    expect(r.issues).toEqual(["unknownRange"]);
  });

  it("drops an unreadable instant and names which", () => {
    expect(parseRange({ since: "yesterday" }, NOW).issues).toEqual(["badSince"]);
    expect(parseRange({ until: "soon" }, NOW).issues).toEqual(["badUntil"]);
  });

  it("lets a chip win over since/until and says the others were ignored", () => {
    const r = parseRange({ range: "1h", since: "2026-11-01T00:00Z" }, NOW);
    expect(r.chip).toBe("1h");
    expect(r.since).toBe("2026-11-05T18:05:00.000Z");
    expect(r.issues).toEqual(["chipWins"]);
  });

  it("is quiet when nothing is set", () => {
    const r = parseRange({}, NOW);
    expect(r).toEqual({ chip: "", custom: false, since: "", until: "", issues: [] });
    expect(rangeWindow(r, NOW)).toBeNull();
  });
});

describe("the absolute window", () => {
  it("ends at the moment of the read when the end is open", () => {
    const w = rangeWindow(parseRange({ range: "24h" }, NOW), NOW);
    expect(w).toEqual({
      from: "2026-11-04T19:05:00.000Z",
      to: "2026-11-05T19:05:00.000Z",
      toIsNow: true,
    });
  });

  it("states minutes with the year and the zone", () => {
    expect(stampText("2026-11-05T19:05:42Z")).toBe("2026-11-05 19:05 UTC");
    expect(stampText("2026-11-05T19:05:42Z", true)).toBe("2026-11-05 19:05");
    expect(stampText("nope")).toBe("—");
    expect(urlInstant("2026-11-05T19:05:00.000Z")).toBe("2026-11-05T19:05:00Z");
  });
});
