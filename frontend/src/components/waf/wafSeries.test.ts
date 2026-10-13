import { describe, expect, it } from "vitest";

import { wafBucket } from "./fixtures";
import { buildWafSeries, wafUnobserved } from "./wafSeries";

const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

describe("buildWafSeries", () => {
  it("plots a complete bucket's request count and its coverage", () => {
    const [p] = buildWafSeries([wafBucket({ request_count: 41, endpoint_coverage_ratio: 0.97 })]);
    expect(p.observed).toBe(41);
    expect(p.coverage).toBe(0.97);
    expect(p.kind).toBe("COMPLETE");
  });

  it("keeps a measured zero as 0 on the line", () => {
    const [p] = buildWafSeries([wafBucket({ request_count: 0 })]);
    expect(p.observed).toBe(0);
  });

  it("turns a PARTIAL bucket into a null, keeping what it reported only as 'reported'", () => {
    const [p] = buildWafSeries([
      wafBucket({ completeness: "PARTIAL", partial: true, request_count: 17 }),
    ]);
    expect(p.observed).toBeNull();
    expect(p.reported).toBe(17);
    expect(p.kind).toBe("PARTIAL");
  });

  it("never turns a null count into 0, even on a bucket marked COMPLETE", () => {
    const [p] = buildWafSeries([wafBucket({ request_count: null })]);
    expect(p.observed).toBeNull();
    expect(p.reported).toBeNull();
    expect(p.kind).toBe("MISSING");
  });

  it("declares an interval with no stored bucket a gap, not a zero", () => {
    const points = buildWafSeries([wafBucket({ bucket_start: T(0) }), wafBucket({ bucket_start: T(20) })]);
    expect(points.map((p) => p.kind)).toEqual(["COMPLETE", "GAP", "COMPLETE"]);
    const gap = points[1];
    expect(gap.observed).toBeNull();
    expect(gap.coverage).toBeNull();
  });

  it("does not declare a gap between adjacent buckets", () => {
    const points = buildWafSeries([wafBucket({ bucket_start: T(0) }), wafBucket({ bucket_start: T(5) })]);
    expect(points).toHaveLength(2);
  });

  it("flags a lower-bound count, which is still plotted", () => {
    const [p] = buildWafSeries([wafBucket({ request_count: 412, request_count_measurement: "LOWER_BOUND" })]);
    expect(p.observed).toBe(412);
    expect(p.lowerBound).toBe(true);
  });

  it("sorts out-of-order rows rather than fabricating a backwards segment", () => {
    const points = buildWafSeries([wafBucket({ bucket_start: T(5) }), wafBucket({ bucket_start: T(0) })]);
    expect(points.map((p) => p.t)).toEqual([Date.parse(T(0)), Date.parse(T(5))]);
  });
});

describe("wafUnobserved", () => {
  it("lists partial buckets and uncollected spans for shading, and only those", () => {
    const spans = wafUnobserved([
      wafBucket({ bucket_start: T(0) }),
      wafBucket({ bucket_start: T(5), completeness: "PARTIAL", partial: true }),
      wafBucket({ bucket_start: T(30) }),
    ]);
    expect(spans.map((s) => s.kind)).toEqual(["PARTIAL", "GAP"]);
    expect(spans[1].start).toBe(Date.parse(T(10)));
    expect(spans[1].end).toBe(Date.parse(T(30)));
  });

  it("is empty when everything was collected", () => {
    expect(wafUnobserved([wafBucket({ bucket_start: T(0) }), wafBucket({ bucket_start: T(5) })])).toEqual([]);
  });
});
