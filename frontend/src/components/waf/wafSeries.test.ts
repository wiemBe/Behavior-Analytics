import { describe, expect, it } from "vitest";

import { wafBucket } from "./fixtures";
import {
  RATIO_SPECS,
  buildWafRatioSeries,
  buildWafSeries,
  wafRatioUnobserved,
  wafUnobserved,
} from "./wafSeries";

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

describe("buildWafRatioSeries", () => {
  const block = RATIO_SPECS.WAF_BLOCK_RATIO_SPIKE;

  it("is blocked / action events where the action breakdown is exact", () => {
    const [p] = buildWafRatioSeries([wafBucket({ blocked_count: 10, action_events: 40 })], block);
    expect(p.observed).toBe(0.25);
    expect(p.kind).toBe("COMPLETE");
    // The request count stays beside it for the hover readout.
    expect(p.reported).toBe(41);
  });

  it("keeps a measured 0 blocked of a non-zero denominator as 0", () => {
    const [p] = buildWafRatioSeries([wafBucket({ blocked_count: 0, action_events: 40 })], block);
    expect(p.observed).toBe(0);
  });

  it("makes a breakdown that was not collected a gap, never 0", () => {
    for (const status of ["NOT_COLLECTED", "FAILED", null]) {
      const [p] = buildWafRatioSeries(
        [wafBucket({ action_measurement: status, blocked_count: null, action_events: null })],
        block,
      );
      expect(p.observed).toBeNull();
      expect(p.kind).toBe("MISSING");
    }
  });

  it("makes a capped breakdown (LOWER_BOUND) a gap: its share is not a measurement", () => {
    const [p] = buildWafRatioSeries(
      [wafBucket({ action_measurement: "LOWER_BOUND", blocked_count: 10, action_events: 40 })],
      block,
    );
    expect(p.observed).toBeNull();
    expect(p.kind).toBe("PARTIAL");
  });

  it("makes a PARTIAL bucket a gap even when its counts divide", () => {
    const [p] = buildWafRatioSeries(
      [wafBucket({ completeness: "PARTIAL", partial: true, blocked_count: 10, action_events: 40 })],
      block,
    );
    expect(p.observed).toBeNull();
    expect(p.kind).toBe("PARTIAL");
  });

  it("makes a zero denominator a gap, not 0 %", () => {
    const [p] = buildWafRatioSeries([wafBucket({ blocked_count: 0, action_events: 0 })], block);
    expect(p.observed).toBeNull();
    expect(p.kind).toBe("MISSING");
  });

  it("makes a null numerator a gap", () => {
    const [p] = buildWafRatioSeries([wafBucket({ blocked_count: null, action_events: 40 })], block);
    expect(p.observed).toBeNull();
  });

  it("divides each detector's own counts", () => {
    const b = wafBucket({
      server_error_count: 4,
      client_error_count: 8,
      response_code_events: 40,
      top1_client_requests: 30,
      client_events: 40,
    });
    expect(buildWafRatioSeries([b], RATIO_SPECS.WAF_ERROR_RATIO_SPIKE)[0].observed).toBe(0.1);
    expect(buildWafRatioSeries([b], RATIO_SPECS.WAF_CLIENT_ERROR_RATIO_SPIKE)[0].observed).toBe(0.2);
    expect(buildWafRatioSeries([b], RATIO_SPECS.WAF_CLIENT_CONCENTRATION_SPIKE)[0].observed).toBe(0.75);
  });

  it("judges each breakdown by its own status, not another's", () => {
    const [p] = buildWafRatioSeries(
      [wafBucket({ client_measurement: "FAILED", client_events: null, top1_client_requests: null })],
      RATIO_SPECS.WAF_CLIENT_CONCENTRATION_SPIKE,
    );
    expect(p.observed).toBeNull();
    const [q] = buildWafRatioSeries(
      [wafBucket({ client_measurement: "FAILED", client_events: null, top1_client_requests: null })],
      RATIO_SPECS.WAF_BLOCK_RATIO_SPIKE,
    );
    expect(q.observed).not.toBeNull();
  });

  it("still declares an interval with no stored bucket a gap", () => {
    const points = buildWafRatioSeries(
      [wafBucket({ bucket_start: T(0) }), wafBucket({ bucket_start: T(20) })],
      block,
    );
    expect(points.map((p) => p.kind)).toEqual(["COMPLETE", "GAP", "COMPLETE"]);
  });

  it("lists unmeasured proportions for shading", () => {
    const spans = wafRatioUnobserved(
      [wafBucket({ bucket_start: T(0) }), wafBucket({ bucket_start: T(5), action_events: 0 })],
      block,
    );
    expect(spans.map((s) => s.kind)).toEqual(["MISSING"]);
  });
});
