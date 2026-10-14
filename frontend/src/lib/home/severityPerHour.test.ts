import { describe, expect, it } from "vitest";

import { anomaly, page } from "./fixtures";
import { MAX_HOURS, lastHours, severityPerHour, severityText } from "./severityPerHour";

const range = { since: "2026-10-14T08:00:00Z", until: "2026-10-14T12:00:00Z" };
const at = (iso: string, severity = "HIGH") => anomaly({ detected_at: iso, severity });

describe("severityPerHour", () => {
  it("counts per UTC hour and severity, with every hour of the range present", () => {
    const r = severityPerHour(
      page([
        at("2026-10-14T09:10:00Z", "HIGH"),
        at("2026-10-14T09:50:00Z", "HIGH"),
        at("2026-10-14T09:55:00Z", "LOW"),
        at("2026-10-14T11:59:59Z", "CRITICAL"),
      ]),
      range,
    );
    expect(r.hours.map((h) => h.start)).toEqual([
      "2026-10-14T08:00:00.000Z",
      "2026-10-14T09:00:00.000Z",
      "2026-10-14T10:00:00.000Z",
      "2026-10-14T11:00:00.000Z",
    ]);
    expect(r.hours[1]).toMatchObject({ counts: { HIGH: 2, LOW: 1 }, total: 3 });
    expect(r.hours[3]).toMatchObject({ counts: { CRITICAL: 1 }, total: 1 });
    expect(r.severities).toEqual(["CRITICAL", "HIGH", "LOW"]);
    expect(r.lowerBound).toBe(false);
    expect(r.qualifiers).toEqual([]);
  });

  it("treats an empty hour of a complete list as a measured zero", () => {
    const r = severityPerHour(page([at("2026-10-14T09:10:00Z")]), range);
    expect(r.hours[0]).toMatchObject({ counts: {}, total: 0 });
  });

  it("floors a range that starts mid-hour to the hour", () => {
    const r = severityPerHour(page([]), {
      since: "2026-10-14T08:30:00Z",
      until: "2026-10-14T10:15:00Z",
    });
    expect(r.hours.map((h) => h.start)).toEqual([
      "2026-10-14T08:00:00.000Z",
      "2026-10-14T09:00:00.000Z",
      "2026-10-14T10:00:00.000Z",
    ]);
  });

  it("is TRUNCATED, with lower-bound counts, when the list is capped", () => {
    const r = severityPerHour(page([at("2026-10-14T09:10:00Z")], 450), range);
    expect(r.lowerBound).toBe(true);
    expect(r.qualifiers).toEqual([
      { kind: "TRUNCATED", shown: 1, total: 450, text: "First 1 of 450" },
    ]);
  });

  it("does not count rows outside the range, and says how many", () => {
    const r = severityPerHour(
      page([
        at("2026-10-14T07:59:59Z"),
        at("2026-10-14T12:00:00Z"),
        at("not a date"),
        at("2026-10-14T10:00:00Z"),
      ]),
      range,
    );
    expect(r.outsideRange).toBe(3);
    expect(r.hours.reduce((s, h) => s + h.total, 0)).toBe(1);
  });

  it("is INSUFFICIENT_DATA for an invalid or oversized range", () => {
    for (const bad of [
      { since: "x", until: "y" },
      { since: "2026-10-14T12:00:00Z", until: "2026-10-14T08:00:00Z" },
      { since: "2026-01-01T00:00:00Z", until: "2026-10-14T00:00:00Z" },
    ]) {
      const r = severityPerHour(page([]), bad);
      expect(r.hours).toEqual([]);
      expect(r.qualifiers.map((q) => q.kind)).toEqual(["INSUFFICIENT_DATA"]);
    }
    expect(MAX_HOURS).toBe(169);
  });
});

describe("lastHours", () => {
  it("ends with the hour that contains now, and keeps its key within the hour", () => {
    const a = lastHours(Date.parse("2026-10-14T13:05:00Z"), 24);
    const b = lastHours(Date.parse("2026-10-14T13:59:59Z"), 24);
    expect(a).toEqual(b);
    expect(a).toEqual({ since: "2026-10-13T14:00:00.000Z", until: "2026-10-14T14:00:00.000Z" });
  });

  it("spans exactly the requested number of hour columns", () => {
    const r = lastHours(Date.parse("2026-10-14T13:05:00Z"), 24);
    expect(severityPerHour(page([]), r).hours).toHaveLength(24);
  });
});

describe("severityText", () => {
  it("states a lower bound when the list was capped", () => {
    const items = [at("2026-10-14T09:10:00Z", "HIGH")];
    const r = severityPerHour({ ...page(items), total: 345 }, range);
    expect(severityText(r, range)).toMatch(/at least 1/);
    expect(severityText(r, range)).toMatch(/lower bound/);
  });

  it("does not claim a lower bound for a complete list", () => {
    const r = severityPerHour(page([at("2026-10-14T09:10:00Z", "HIGH")]), range);
    expect(severityText(r, range)).not.toMatch(/lower bound|at least/);
  });
});
