import { describe, expect, it } from "vitest";

import { iso, point, series } from "./fixtures";
import { buildPlot, isolated, PW_MAX_SERIES } from "./pwSeries";

const WINDOW = { windowStart: iso(0), windowEnd: iso(5), bucketSeconds: 300 };

const line = (n: number, pts: ReturnType<typeof point>[]) =>
  buildPlot({ series: [series(`id-${n}`, `v${n}`, pts)], ...WINDOW }).lines[0];

describe("the gap rules", () => {
  it("makes an uncollected bucket a null and a shaded span, never a zero", () => {
    const plot = buildPlot({
      series: [series("a", "alice", [point(0), point(1), point(3), point(4), point(5)])],
      ...WINDOW,
    });
    const slot2 = plot.lines[0].points[2];
    expect(slot2.value).toBeNull();
    expect(slot2.kind).toBe("NOT_COLLECTED");
    expect(plot.uncollected).toEqual([{ start: Date.parse(iso(2)), end: Date.parse(iso(3)) }]);
    expect(plot.collectedSlots).toBe(5);
    expect(plot.windowSlots).toBe(6);
  });

  it("merges adjacent uncollected slots into one span", () => {
    const plot = buildPlot({
      series: [series("a", "alice", [point(0), point(5)])],
      ...WINDOW,
    });
    expect(plot.uncollected).toEqual([{ start: Date.parse(iso(1)), end: Date.parse(iso(5)) }]);
  });

  it("draws an UNKNOWN reading as a gap, not a zero", () => {
    const l = line(1, [point(0, { reading: "UNKNOWN", events: null, measurement: null })]);
    expect(l.points[0].value).toBeNull();
    expect(l.points[0].kind).toBe("UNKNOWN");
  });

  it("draws a value that was not tracked at the bucket as a gap", () => {
    const l = line(1, [point(0, { reading: "NOT_AN_OBSERVATION", events: null, measurement: null })]);
    expect(l.points[0].value).toBeNull();
    expect(l.points[0].kind).toBe("NOT_AN_OBSERVATION");
  });

  it("draws a NO_BUCKET reading as a gap, not a zero", () => {
    const l = line(1, [point(0, { reading: "NO_BUCKET", events: null, measurement: null })]);
    expect(l.points[0].value).toBeNull();
    expect(l.points[0].kind).toBe("NOT_COLLECTED");
  });

  it("draws a measured zero as 0", () => {
    const l = line(1, [point(0, { reading: "MEASURED_ZERO", events: 0, measurement: "EXACT" })]);
    expect(l.points[0].value).toBe(0);
    expect(l.points[0].kind).toBe("MEASURED_ZERO");
  });

  it("plots a lower-bound count and flags it as a lower bound", () => {
    const l = line(1, [point(0, { events: 40, measurement: "LOWER_BOUND" })]);
    expect(l.points[0].value).toBe(40);
    expect(l.points[0].kind).toBe("LOWER_BOUND");
  });

  it("fails closed: a count with no measurement, and a measured zero with a count, are gaps", () => {
    const l = line(1, [
      point(0, { events: 7, measurement: null }),
      point(1, { reading: "MEASURED_ZERO", events: 3, measurement: "EXACT" }),
      point(2, { reading: "COUNT", events: null }),
    ]);
    expect(l.points.slice(0, 3).map((p) => p.value)).toEqual([null, null, null]);
  });

  it("treats an unknown reading label as a gap", () => {
    const l = line(1, [point(0, { reading: "SOMETHING_NEW" as never })]);
    expect(l.points[0].value).toBeNull();
  });

  it("says nothing about collection when it cannot know", () => {
    const plot = buildPlot({ series: [], bucketSeconds: 300 });
    expect(plot.uncollected).toEqual([]);
    expect(plot.collectedSlots).toBeNull();
  });

  it("marks a lone measured point between gaps", () => {
    const l = line(1, [point(0), point(2)]);
    expect(isolated(l.points, 0)).toBe(true);
    const run = line(1, [point(0), point(1), point(2)]);
    expect(isolated(run.points, 1)).toBe(false);
    expect(isolated(l.points, 1)).toBe(false);
  });
});

describe("the series cap and the band", () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) => series(`id-${i}`, `v${i}`, [point(0), point(1)]));

  it("draws at most five values and counts the rest", () => {
    const plot = buildPlot({ series: many(7), ...WINDOW });
    expect(PW_MAX_SERIES).toBe(5);
    expect(plot.lines).toHaveLength(5);
    expect(plot.dropped).toBe(2);
  });

  it("has a band for one value and none for several", () => {
    expect(buildPlot({ series: many(1), ...WINDOW }).hasBand).toBe(true);
    expect(buildPlot({ series: many(2), ...WINDOW }).hasBand).toBe(false);
  });

  it("has no band where the baseline is not reliable, whatever numbers ride along", () => {
    const plot = buildPlot({
      series: [
        series("a", "alice", [
          point(0, { baseline_state: "LEARNING", expected_low: 1, expected_high: 2, expected_median: 1 }),
        ]),
      ],
      ...WINDOW,
    });
    expect(plot.hasBand).toBe(false);
    expect(plot.lines[0].points[0].low).toBeNull();
  });
});
