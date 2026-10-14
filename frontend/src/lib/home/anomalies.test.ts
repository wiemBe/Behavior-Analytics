import { describe, expect, it } from "vitest";

import { anomaliesCard, countBySeverity } from "./anomalies";
import { anomaly, page, summary } from "./fixtures";

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
