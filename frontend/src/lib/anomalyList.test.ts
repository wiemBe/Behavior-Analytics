import { describe, expect, it } from "vitest";

import { ageSeconds, changePercent, isNoEvents, silenceSeconds } from "./anomalyList";

const NOW = Date.parse("2026-11-05T19:05:00Z");

describe("ageSeconds", () => {
  it("is whole minutes once there is a minute", () => {
    expect(ageSeconds({ anomaly_start: "2026-11-05T16:54:21Z" }, NOW)).toBe(2 * 3600 + 10 * 60);
  });
  it("keeps seconds under a minute", () => {
    expect(ageSeconds({ anomaly_start: "2026-11-05T19:04:30Z" }, NOW)).toBe(30);
  });
  it("is null, not zero, with no start, a bad start or a future one", () => {
    expect(ageSeconds({ anomaly_start: null }, NOW)).toBeNull();
    expect(ageSeconds({ anomaly_start: "garbage" }, NOW)).toBeNull();
    expect(ageSeconds({ anomaly_start: "2026-11-05T19:05:01Z" }, NOW)).toBeNull();
  });
});

describe("changePercent", () => {
  it("is the ratio's distance from 1, in percent", () => {
    expect(changePercent({ deviation_ratio: 3 })).toBeCloseTo(200);
    expect(changePercent({ deviation_ratio: 0.25 })).toBeCloseTo(-75);
    expect(changePercent({ deviation_ratio: 1 })).toBe(0);
  });
  it("is null when there is no ratio", () => {
    expect(changePercent({ deviation_ratio: null })).toBeNull();
    expect(changePercent({ deviation_ratio: Number.NaN })).toBeNull();
    expect(changePercent({ deviation_ratio: Number.POSITIVE_INFINITY })).toBeNull();
  });
});

describe("isNoEvents / silenceSeconds", () => {
  it("only a Phase A NO_EVENTS row", () => {
    expect(isNoEvents({ anomaly_type: "NO_EVENTS", subject_kind: "LOG_SOURCE" })).toBe(true);
    expect(isNoEvents({ anomaly_type: "NO_EVENTS" })).toBe(true);
    expect(isNoEvents({ anomaly_type: "VOLUME_DROP" })).toBe(false);
    expect(isNoEvents({ anomaly_type: "NO_EVENTS", subject_kind: "IPS_SOURCE" })).toBe(false);
  });
  it("prefers the recorded duration, else the age", () => {
    expect(silenceSeconds({ anomaly_start: "2026-11-05T18:00:00Z", duration_seconds: 600 }, NOW)).toBe(600);
    expect(silenceSeconds({ anomaly_start: "2026-11-05T18:00:00Z", duration_seconds: null }, NOW)).toBe(3900);
    expect(silenceSeconds({ anomaly_start: null, duration_seconds: null }, NOW)).toBeNull();
  });
});
