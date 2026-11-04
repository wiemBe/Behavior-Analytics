import { describe, expect, it } from "vitest";

import { anomaliesCard, countBySeverity } from "./anomalies";
import { activeCounts, anomaly, page, summary } from "./fixtures";

describe("anomaliesCard", () => {
  it("reports exact detector and lifecycle partitions plus the not-judged companion", () => {
    const r = anomaliesCard(
      summary({
        open_anomalies: 9,
        spikes: 4,
        drops: 2,
        silent_sources: 1,
        candidates: 3,
        recovering: 1,
        insufficient_data_sources: 7,
      }),
      page([anomaly({ severity: "HIGH" })]),
    );
    expect(r.open).toBe(9);
    expect(r.byDetector.map((t) => [t.key, t.count])).toEqual([
      ["spikes", 4],
      ["drops", 2],
      ["silent", 1],
      ["other", 2],
    ]);
    expect(r.byLifecycle.map((t) => [t.key, t.count])).toEqual([
      ["open", 5],
      ["candidates", 3],
      ["recovering", 1],
    ]);
    expect(r.byDetector.reduce((sum, item) => sum + item.count, 0)).toBe(r.open);
    expect(r.byLifecycle.reduce((sum, item) => sum + item.count, 0)).toBe(r.open);
    expect(r.notJudged).toBe(7);
    expect(r.notJudgedText).toBe("7 sources not judged");
  });

  it("sums every family, and the family and lifecycle partitions both sum to it", () => {
    const r = anomaliesCard(
      summary({
        spikes: 1,
        active_by_family: {
          log_source: activeCounts(1, 1, 0),
          waf: activeCounts(0, 0, 1),
          ips: activeCounts(2),
          property_watch: activeCounts(0, 3),
        },
      }),
      page([]),
    );
    expect(r.open).toBe(8);
    expect(r.lowerBound).toBe(false);
    const families = (r.logSources ?? 0) + r.families.reduce((sum, f) => sum + (f.count ?? 0), 0);
    expect(families).toBe(8);
    expect(r.byDetector.reduce((sum, item) => sum + item.count, 0)).toBe(r.logSources);
    expect(r.byLifecycle.map((t) => [t.key, t.count])).toEqual([
      ["open", 3],
      ["candidates", 4],
      ["recovering", 1],
    ]);
    expect(r.byLifecycle.reduce((sum, item) => sum + item.count, 0)).toBe(8);
  });

  it("leaves an unread family out of the total as a floor and keeps it null, not 0", () => {
    const r = anomaliesCard(
      summary({ active_by_family: { log_source: activeCounts(2), waf: activeCounts(1) } }),
      page([]),
    );
    expect(r.open).toBe(3);
    expect(r.lowerBound).toBe(true);
    expect(r.unread).toEqual(["ips", "property_watch"]);
    expect(r.families.map((f) => [f.key, f.count])).toEqual([
      ["waf", 1],
      ["ips", null],
      ["property_watch", null],
    ]);
  });

  it("has no total, and no log-source split, when nothing was read", () => {
    const r = anomaliesCard(summary({ active_by_family: undefined, spikes: 2 }), page([]));
    expect(r.open).toBeNull();
    expect(r.logSources).toBeNull();
    expect(r.byDetector).toEqual([]);
    expect(r.unread).toHaveLength(4);
  });

  it("drops the detector split when the detector counts exceed the family's windowed count", () => {
    const r = anomaliesCard(
      summary({ spikes: 5, active_by_family: { log_source: activeCounts(3) } }),
      page([]),
    );
    expect(r.logSources).toBe(3);
    expect(r.byDetector).toEqual([]);
  });

  it("matches the severity parts to the total when the list is complete", () => {
    const items = [
      anomaly({ severity: "HIGH" }),
      anomaly({ severity: "LOW" }),
      anomaly({ severity: "LOW" }),
    ];
    const r = anomaliesCard(
      summary({
        active_by_family: {
          log_source: activeCounts(1),
          waf: activeCounts(1),
          ips: activeCounts(0),
          property_watch: activeCounts(1),
        },
      }),
      page(items),
    );
    expect(r.severity.qualifiers).toEqual([]);
    expect(r.severity.entries.reduce((sum, e) => sum + e.count, 0)).toBe(r.open);
  });

  it("uses the singular for one source", () => {
    expect(anomaliesCard(summary({ insufficient_data_sources: 1 }), page([])).notJudgedText).toBe(
      "1 source not judged",
    );
  });

  it("counts severity from the list, in rank order, with no qualifier when complete", () => {
    const r = anomaliesCard(
      summary(),
      page([
        anomaly({ severity: "LOW" }),
        anomaly({ severity: "CRITICAL" }),
        anomaly({ severity: "LOW" }),
        anomaly({ severity: "WEIRD" }),
      ]),
    );
    expect(r.severity.entries).toEqual([
      { severity: "CRITICAL", count: 1 },
      { severity: "LOW", count: 2 },
      { severity: "WEIRD", count: 1 },
    ]);
    expect(r.severity.qualifiers).toEqual([]);
  });

  it("is TRUNCATED, first n of total, when the list is capped", () => {
    const r = anomaliesCard(summary(), page([anomaly(), anomaly()], 345));
    expect(r.severity.qualifiers).toEqual([
      { kind: "TRUNCATED", shown: 2, total: 345, text: "First 2 of 345" },
    ]);
  });

  it("gives an empty severity breakdown, not zeros, for an empty list", () => {
    expect(countBySeverity([])).toEqual([]);
  });
});
