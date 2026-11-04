import { describe, expect, it } from "vitest";

import {
  formatAge,
  formatDuration,
  formatExactDuration,
  relativeAge,
  relativeAgeFromSeconds,
} from "./time";

const NOW = Date.parse("2026-10-20T12:00:00Z");

describe("relativeAge", () => {
  it("uses the guidelines' spelling, largest unit, under a minute first", () => {
    expect(relativeAge(new Date(NOW).toISOString(), NOW)).toBe("under 1 min ago");
    expect(relativeAge(new Date(NOW - 59_000).toISOString(), NOW)).toBe("under 1 min ago");
    expect(relativeAge(new Date(NOW - 60_000).toISOString(), NOW)).toBe("1 min ago");
    expect(relativeAge(new Date(NOW - 12 * 60_000).toISOString(), NOW)).toBe("12 min ago");
    expect(relativeAge(new Date(NOW - 59 * 60_000).toISOString(), NOW)).toBe("59 min ago");
    expect(relativeAge(new Date(NOW - 60 * 60_000).toISOString(), NOW)).toBe("1 h ago");
    expect(relativeAge(new Date(NOW - 24 * 60 * 60_000).toISOString(), NOW)).toBe("1 d ago");
    expect(relativeAge(new Date(NOW - 2 * 24 * 60 * 60_000).toISOString(), NOW)).toBe("2 d ago");
  });

  it("accepts a Date as now", () => {
    expect(relativeAge("2026-10-20T11:48:00Z", new Date(NOW))).toBe("12 min ago");
  });

  it("renders a missing, unparsable or future instant as an em dash, never 0", () => {
    expect(relativeAge(null, NOW)).toBe("—");
    expect(relativeAge(undefined, NOW)).toBe("—");
    expect(relativeAge("", NOW)).toBe("—");
    expect(relativeAge("not-a-date", NOW)).toBe("—");
    expect(relativeAge(new Date(NOW + 60_000).toISOString(), NOW)).toBe("—");
    expect(relativeAge("2026-10-20T12:00:00Z", Number.NaN)).toBe("—");
    for (const text of [relativeAge(null, NOW), relativeAge("nope", NOW)]) {
      expect(text).not.toBe("0");
      expect(text).not.toContain("0 min");
    }
  });
});

describe("relativeAgeFromSeconds", () => {
  it("matches relativeAge at the same boundaries", () => {
    expect(relativeAgeFromSeconds(0)).toBe("under 1 min ago");
    expect(relativeAgeFromSeconds(59)).toBe("under 1 min ago");
    expect(relativeAgeFromSeconds(60)).toBe("1 min ago");
    expect(relativeAgeFromSeconds(59 * 60)).toBe("59 min ago");
    expect(relativeAgeFromSeconds(60 * 60)).toBe("1 h ago");
    expect(relativeAgeFromSeconds(24 * 60 * 60)).toBe("1 d ago");
  });

  it("renders a missing or negative duration as an em dash, never 0", () => {
    expect(relativeAgeFromSeconds(null)).toBe("—");
    expect(relativeAgeFromSeconds(undefined)).toBe("—");
    expect(relativeAgeFromSeconds(Number.NaN)).toBe("—");
    expect(relativeAgeFromSeconds(-1)).toBe("—");
  });
});

describe("formatDuration and formatAge", () => {
  it("keeps at most two units, with a space before the unit", () => {
    expect(formatDuration(0)).toBe("0 s");
    expect(formatDuration(59)).toBe("59 s");
    expect(formatDuration(60)).toBe("1 min");
    expect(formatDuration(59 * 60)).toBe("59 min");
    expect(formatDuration(60 * 60)).toBe("1 h");
    expect(formatDuration(3 * 3600 + 20 * 60)).toBe("3 h 20 min");
    expect(formatDuration(24 * 3600)).toBe("1 d");
    expect(formatDuration(2 * 86400 + 4 * 3600)).toBe("2 d 4 h");
    expect(formatDuration(90)).toBe("1 min 30 s");
    expect(formatDuration(4020)).toBe("1 h 7 min");
    expect(formatDuration(3601)).toBe("1 h");
    expect(formatAge(90000)).toBe("1 d 1 h");
    expect(formatAge(3660)).toBe("1 h 1 min");
    expect(formatAge(120)).toBe("2 min");
    expect(formatAge(7200)).toBe("2 h");
  });

  it("floors the units below the second one away, never rounds", () => {
    const table: [number, string][] = [
      [0, "0 s"], [59, "59 s"], [60, "1 min"], [3599, "59 min 59 s"], [3600, "1 h"],
      [86399, "23 h 59 min"], [86400, "1 d"], [90061, "1 d 1 h"],
      [86400 + 7 * 60 + 39, "1 d"], [86400 + 3 * 3600 + 7 * 60, "1 d 3 h"],
      [3 * 3600 + 20 * 60 + 5, "3 h 20 min"], [59.9, "59 s"], [3600 + 59.9, "1 h"],
    ];
    for (const [seconds, text] of table) expect(formatDuration(seconds)).toBe(text);
  });

  it("gives a negative or unmeasured value an em dash", () => {
    for (const value of [-1, -0.5, null, undefined, Number.NaN, Infinity]) {
      expect(formatDuration(value)).toBe("—");
    }
  });

  it("spells a configured value whole with formatExactDuration", () => {
    expect(formatExactDuration(3601)).toBe("1 h 1 s");
    expect(formatExactDuration(86400 + 7 * 60 + 39)).toBe("1 d 7 min 39 s");
    expect(formatExactDuration(0)).toBe("0 s");
    expect(formatExactDuration(null)).toBe("—");
  });

  it("is one spelling: formatAge and formatDuration agree", () => {
    expect(formatAge(3 * 3600 + 20 * 60)).toBe(formatDuration(3 * 3600 + 20 * 60));
    expect(formatAge(0)).toBe("0 s");
  });

  it("renders a missing or invalid duration as an em dash, never 0", () => {
    for (const fn of [formatDuration, formatAge]) {
      expect(fn(null)).toBe("—");
      expect(fn(undefined)).toBe("—");
      expect(fn(Number.NaN)).toBe("—");
      expect(fn(-1)).toBe("—");
      expect(fn(null)).not.toBe("0");
      expect(fn(Number.NaN)).not.toContain("0 s");
    }
  });
});
