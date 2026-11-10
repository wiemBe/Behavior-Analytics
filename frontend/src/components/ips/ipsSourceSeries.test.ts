import { describe, expect, it } from "vitest";

import { ipsSourceBucket } from "./fixtures";
import { buildIpsTotalsSeries } from "./ipsSeries";
import {
  SOURCE_SERIES_SPECS,
  actionMix,
  hasReliableCell,
  parseSeries,
  seriesPoints,
  sourceChartSummary,
} from "./ipsSourceSeries";
import { ipsCell } from "./fixtures";

const at = (minute: number) => `2026-10-12T09:${String(minute).padStart(2, "0")}:00Z`;

describe("total events series", () => {
  it("plots the measured total of each stored bucket", () => {
    const points = buildIpsTotalsSeries([
      ipsSourceBucket({ bucket_start: at(0), total_events: 1500 }),
      ipsSourceBucket({ bucket_start: at(5), total_events: 0 }),
    ]);
    expect(points.map((p) => p.observed)).toEqual([1500, 0]);
    expect(points.every((p) => !p.lowerBound && p.measurement === "EXACT")).toBe(true);
  });

  it("makes an interval with no stored bucket a gap with no value, not a zero", () => {
    const points = buildIpsTotalsSeries([
      ipsSourceBucket({ bucket_start: at(0) }),
      ipsSourceBucket({ bucket_start: at(20) }),
    ]);
    expect(points.map((p) => p.kind)).toEqual(["COMPLETE", "GAP", "COMPLETE"]);
    expect(points[1].observed).toBeNull();
  });

  it("carries maintenance and has no baseline metric", () => {
    const [p] = buildIpsTotalsSeries([ipsSourceBucket({ in_maintenance: true })]);
    expect(p.maintenance).toBe(true);
    expect(SOURCE_SERIES_SPECS.events.metric).toBeNull();
  });
});

describe("the other source series", () => {
  it("name their seasonal metric and plot a lower-bound concentration bucket as partial, not a value", () => {
    expect(SOURCE_SERIES_SPECS.concentration.metric).toBe("top1_source_share");
    expect(SOURCE_SERIES_SPECS.fanout.metric).toBe("max_distinct_ports");
    expect(SOURCE_SERIES_SPECS.block_ratio.metric).toBe("block_ratio");
    const partial = ipsSourceBucket({
      dimensions: [
        { dimension: "SOURCE_IP", measurement: "LOWER_BOUND", coverage_ratio: 0.6, coverage_lower_bound: true },
      ],
    });
    const [p] = seriesPoints("concentration", [partial]);
    expect(p.observed).toBeNull();
    expect(p.kind).toBe("PARTIAL");
  });

  it("falls back to total events for a series it does not know", () => {
    expect(parseSeries("fanout")).toBe("fanout");
    expect(parseSeries("nonsense")).toBe("events");
    expect(parseSeries(undefined)).toBe("events");
  });
});

describe("actionMix", () => {
  it("sums the buckets that carried a complete split", () => {
    const mix = actionMix([
      ipsSourceBucket({ bucket_start: at(0), blocked_events: 300, allowed_events: 600, unmapped_events: 100 }),
      ipsSourceBucket({ bucket_start: at(5), blocked_events: 50, allowed_events: 10, unmapped_events: 0 }),
    ]);
    expect(mix).toMatchObject({ blocked: 350, allowed: 610, unmapped: 100, measured: 2, stored: 2, lowerBound: false });
  });

  it("is null when the fields are null in every bucket: not mapped, never zeros", () => {
    const none = { blocked_events: null, allowed_events: null, unmapped_events: null };
    expect(actionMix([ipsSourceBucket(none), ipsSourceBucket({ ...none, bucket_start: at(5) })])).toBeNull();
    expect(actionMix([])).toBeNull();
  });

  it("leaves out a bucket with any part missing or whose action search was not collected", () => {
    const mix = actionMix([
      ipsSourceBucket({ bucket_start: at(0), blocked_events: 10, allowed_events: 20, unmapped_events: 30 }),
      ipsSourceBucket({ bucket_start: at(5), blocked_events: 10, allowed_events: null, unmapped_events: 30 }),
      ipsSourceBucket({
        bucket_start: at(10),
        dimensions: [{ dimension: "ACTION", measurement: "NOT_COLLECTED", coverage_ratio: null, coverage_lower_bound: null }],
      }),
    ]);
    expect(mix).toMatchObject({ blocked: 10, allowed: 20, unmapped: 30, measured: 1, stored: 3 });
  });

  it("marks the sums as a floor when a counted bucket measured the action as a lower bound", () => {
    const mix = actionMix([
      ipsSourceBucket({
        dimensions: [{ dimension: "ACTION", measurement: "LOWER_BOUND", coverage_ratio: 0.5, coverage_lower_bound: true }],
      }),
    ]);
    expect(mix?.lowerBound).toBe(true);
  });
});

describe("hasReliableCell and the summary", () => {
  it("is true only for a reliable cell of that metric", () => {
    const cells = [
      ipsCell({ metric_name: "block_ratio", is_reliable: false }),
      ipsCell({ metric_name: "max_distinct_ports", is_reliable: true }),
    ];
    expect(hasReliableCell(cells, "block_ratio")).toBe(false);
    expect(hasReliableCell(cells, "max_distinct_ports")).toBe(true);
  });

  it("describes gaps and maintenance in words for a reader without the canvas", () => {
    const points = buildIpsTotalsSeries([
      ipsSourceBucket({ bucket_start: at(0), in_maintenance: true }),
      ipsSourceBucket({ bucket_start: at(20) }),
    ]);
    const text = sourceChartSummary(points, "Total events", "lab-ips-01");
    expect(text).toContain("Total events for lab-ips-01 across 2 stored buckets.");
    expect(text).toContain("1 intervals had no stored bucket and are drawn as gaps.");
    expect(text).toContain("1 buckets were in maintenance.");
  });
});
