import { describe, expect, it } from "vitest";

import { ipsEntityBucket, ipsSourceBucket } from "./fixtures";
import { buildIpsBaselineLookup } from "./ipsBaseline";
import { ipsCell } from "./fixtures";
import {
  buildIpsEntitySeries,
  buildIpsSourceSeries,
  ipsMarkedIntervals,
  ipsSeriesCounts,
} from "./ipsSeries";

const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

describe("entity series", () => {
  it("plots an exact count and a measured zero as values", () => {
    const pts = buildIpsEntitySeries([
      ipsEntityBucket({ bucket_start: T(0), events: 0 }),
      ipsEntityBucket({ bucket_start: T(5), events: 41 }),
    ]);
    expect(pts.map((p) => p.observed)).toEqual([0, 41]);
  });

  it("makes a missing row a gap point, never a zero", () => {
    const pts = buildIpsEntitySeries([
      ipsEntityBucket({ bucket_start: T(0) }),
      ipsEntityBucket({ bucket_start: T(20) }),
    ]);
    expect(pts.map((p) => p.kind)).toEqual(["COMPLETE", "GAP", "COMPLETE"]);
    expect(pts[1].observed).toBeNull();
    expect(ipsSeriesCounts(pts).gaps).toBe(1);
  });

  it("does not plot a bucket whose measurement is missing or not collected, but keeps what it reported", () => {
    const pts = buildIpsEntitySeries([
      ipsEntityBucket({ bucket_start: T(0), measurement: null, events: 7 }),
      ipsEntityBucket({ bucket_start: T(5), measurement: "NOT_COLLECTED", events: 8 }),
      ipsEntityBucket({ bucket_start: T(10), measurement: "FAILED", events: 9 }),
    ]);
    expect(pts.map((p) => p.observed)).toEqual([null, null, null]);
    expect(pts.map((p) => p.reported)).toEqual([7, 8, 9]);
    expect(ipsSeriesCounts(pts).notMeasured).toBe(3);
  });

  it("plots a LOWER_BOUND bucket and flags it as not exact", () => {
    const rows = [ipsEntityBucket({ bucket_start: T(0), measurement: "LOWER_BOUND", events: 50 })];
    const pts = buildIpsEntitySeries(rows);
    expect(pts[0]).toMatchObject({ observed: 50, lowerBound: true });
    expect(ipsMarkedIntervals(rows, pts).map((m) => m.mark)).toEqual(["NOT_EXACT"]);
    expect(ipsSeriesCounts(pts).notExact).toBe(1);
  });

  it("marks maintenance and truncated admission", () => {
    const rows = [
      ipsEntityBucket({ bucket_start: T(0), in_maintenance: true, admission_truncated: true }),
    ];
    const pts = buildIpsEntitySeries(rows);
    expect(pts[0]).toMatchObject({ maintenance: true, admissionTruncated: true });
    expect(ipsMarkedIntervals(rows, pts).map((m) => m.mark)).toContain("MAINTENANCE");
    expect(ipsSeriesCounts(pts)).toMatchObject({ maintenance: 1, admissionTruncated: 1 });
  });

  it("marks the never-stored interval", () => {
    const rows = [
      ipsEntityBucket({ bucket_start: T(0) }),
      ipsEntityBucket({ bucket_start: T(20) }),
    ];
    const marks = ipsMarkedIntervals(rows, buildIpsEntitySeries(rows));
    expect(marks.filter((m) => m.mark === "GAP")).toHaveLength(1);
  });
});

describe("source series", () => {
  it("fan-out plots max_distinct_ports, and a null is a gap", () => {
    const pts = buildIpsSourceSeries(
      [
        ipsSourceBucket({ bucket_start: T(0), max_distinct_ports: 12 }),
        ipsSourceBucket({ bucket_start: T(5), max_distinct_ports: null }),
      ],
      "fanout",
    );
    expect(pts.map((p) => p.observed)).toEqual([12, null]);
  });

  it("concentration plots top-1 over source-IP events, with its counts", () => {
    const [p] = buildIpsSourceSeries([ipsSourceBucket()], "concentration");
    expect(p).toMatchObject({ observed: 0.2, numerator: 200, denominator: 1000 });
  });

  it("block ratio divides blocked by every action-bearing event, and is a gap if one count is missing", () => {
    const [ok] = buildIpsSourceSeries([ipsSourceBucket()], "block_ratio");
    expect(ok.observed).toBeCloseTo(0.3);
    const [gap] = buildIpsSourceSeries([ipsSourceBucket({ unmapped_events: null })], "block_ratio");
    expect(gap.observed).toBeNull();
  });

  it("a share of nothing is a gap, not 0 %", () => {
    const [p] = buildIpsSourceSeries(
      [ipsSourceBucket({ source_events: 0, top1_events: 0 })],
      "concentration",
    );
    expect(p.observed).toBeNull();
  });

  it("does not plot a share from a LOWER_BOUND breakdown, but plots a lower-bound fan-out", () => {
    const lb = (dimension: "SOURCE_IP" | "PORT_FANOUT") => ({
      dimensions: [
        { dimension, measurement: "LOWER_BOUND", coverage_ratio: 0.9, coverage_lower_bound: true },
      ],
    });
    const [share] = buildIpsSourceSeries([ipsSourceBucket(lb("SOURCE_IP"))], "concentration");
    expect(share.observed).toBeNull();
    const [fan] = buildIpsSourceSeries([ipsSourceBucket(lb("PORT_FANOUT"))], "fanout");
    expect(fan).toMatchObject({ observed: 12, lowerBound: true });
  });

  it("does not plot a bucket whose dimension was not collected", () => {
    const [p] = buildIpsSourceSeries(
      [ipsSourceBucket({ dimensions: [] })],
      "fanout",
    );
    expect(p.observed).toBeNull();
    expect(p.measurement).toBeNull();
  });
});

describe("band cell lookup", () => {
  const at = (iso: string) => Date.parse(iso);

  it("finds the cell of the bucket's ISO weekday and UTC hour", () => {
    const look = buildIpsBaselineLookup({
      metric: "entity_events",
      pooled: false,
      cells: [
        ipsCell({ weekday: 1, hour: 9, median: 120 }),
        ipsCell({ weekday: 1, hour: 10, median: 300 }),
        ipsCell({ weekday: 2, hour: 9, median: 999 }),
      ],
    });
    expect(look(at("2026-10-12T09:25:00Z"))?.median).toBe(120); // Monday
    expect(look(at("2026-10-12T10:00:00Z"))?.median).toBe(300);
    expect(look(at("2026-10-13T09:00:00Z"))?.median).toBe(999); // Tuesday
    expect(look(at("2026-10-18T09:00:00Z"))).toBeNull(); // Sunday = 7, no cell
  });

  it("has no band for an unreliable cell or another metric", () => {
    const look = buildIpsBaselineLookup({
      metric: "entity_events",
      pooled: false,
      cells: [ipsCell({ is_reliable: false }), ipsCell({ hour: 10, metric_name: "other" })],
    });
    expect(look(at("2026-10-12T09:00:00Z"))).toBeNull();
    expect(look(at("2026-10-12T10:00:00Z"))).toBeNull();
  });

  it("uses the pooled sum ratio as a share's expectation, and none without sums", () => {
    const look = buildIpsBaselineLookup({
      metric: "top1_source_share",
      pooled: true,
      cells: [
        ipsCell({
          metric_name: "top1_source_share",
          median: 0.5,
          numerator_sum: 30,
          denominator_sum: 100,
          p05: 0.1,
          p95: 0.4,
        }),
        ipsCell({ metric_name: "top1_source_share", hour: 10, median: 0.5 }),
      ],
    });
    expect(look(at("2026-10-12T09:00:00Z"))).toEqual({ median: 0.3, low: 0.1, high: 0.4 });
    expect(look(at("2026-10-12T10:00:00Z"))?.median).toBeNull();
  });

  it("gives no band when a percentile is missing", () => {
    const look = buildIpsBaselineLookup({
      metric: "entity_events",
      pooled: false,
      cells: [ipsCell({ p05: null })],
    });
    expect(look(at("2026-10-12T09:00:00Z"))).toMatchObject({ low: null, high: null, median: 120 });
  });
});
