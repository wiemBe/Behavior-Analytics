import { describe, expect, it } from "vitest";

import { bucketWidth, formatPercent, formatPoints, formatRequests, requestsUnit } from "./wafFormat";

describe("formatRequests", () => {
  it("renders a measured zero as 0 and a missing value as an em dash", () => {
    expect(formatRequests(0)).toBe("0");
    expect(formatRequests(null)).toBe("—");
    expect(formatRequests(undefined)).toBe("—");
  });

  it("renders a lower bound as 'at least', never as the bare count", () => {
    expect(formatRequests(412, "LOWER_BOUND")).toBe("≥ 412");
    expect(formatRequests(412, "EXACT")).toBe("412");
    expect(formatRequests(412, null)).toBe("412");
  });

  it("keeps one decimal on a fractional median and groups thousands", () => {
    expect(formatRequests(41.5)).toBe("41.5");
    expect(formatRequests(12345)).toBe("12,345");
  });

  it("does not invent a bound for a missing lower-bound value", () => {
    expect(formatRequests(null, "LOWER_BOUND")).toBe("—");
  });
});

describe("formatPercent and formatPoints", () => {
  it("joins the number and its unit with U+00A0", () => {
    expect(formatPercent(0.318)).toBe("31.8\u00A0%");
    expect(formatPercent(0.318, null, 2)).toBe("31.80\u00A0%");
    expect(formatPoints(29.7)).toBe("+29.7\u00A0pp");
  });

  it("formats a fraction as a percentage, with a lower bound as ≥", () => {
    expect(formatPercent(0.318)).toBe("31.8\u00A0%");
    expect(formatPercent(0.624, "LOWER_BOUND")).toBe("≥ 62.4\u00A0%");
    expect(formatPercent(0)).toBe("0.0\u00A0%");
    expect(formatPercent(0.0003)).toBe("<0.1\u00A0%");
    expect(formatPercent(null)).toBe("—");
  });

  it("signs a change in points so a drop is visibly a drop", () => {
    expect(formatPoints(29.7)).toBe("+29.7\u00A0pp");
    expect(formatPoints(-5)).toBe("-5.0\u00A0pp");
    expect(formatPoints(null)).toBe("—");
  });
});

describe("bucket width", () => {
  it("speaks a bucket in minutes or hours, and says so when unknown", () => {
    expect(bucketWidth(300)).toBe("5 min");
    expect(bucketWidth(3600)).toBe("1 h");
    expect(bucketWidth(45)).toBe("45 s");
    expect(bucketWidth(null)).toBeNull();
    expect(requestsUnit(300)).toBe("requests / 5 min");
    expect(requestsUnit(null)).toBe("requests / bucket");
  });
});
