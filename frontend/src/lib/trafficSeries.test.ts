import { describe, expect, it } from "vitest";

import { SPARK_H, sparkOf } from "./trafficSeries";

describe("sparkOf", () => {
  it("draws one connected line for a series with no gap", () => {
    const s = sparkOf([1, 2, 3, 2])!;
    expect(s.lines).toHaveLength(1);
    expect(s.lines[0].split(" ")).toHaveLength(4);
    expect(s.gaps).toBe(0);
    expect(s.min).toBe(1);
    expect(s.max).toBe(3);
  });

  it("breaks the line at a null instead of dropping to zero", () => {
    const s = sparkOf([5, 6, null, 7, 8])!;
    expect(s.lines).toHaveLength(2);
    expect(s.gaps).toBe(1);
    // the lowest drawn point is the lowest measured value, never a zero from the gap
    expect(s.min).toBe(5);
    const ys = s.lines.flatMap((l) => l.split(" ").map((p) => Number(p.split(",")[1])));
    expect(ys).toHaveLength(4);
  });

  it("shows a lone measured point between gaps as a dot", () => {
    const s = sparkOf([null, 4, null, 5, 6])!;
    expect(s.dots).toHaveLength(1);
    expect(s.lines).toHaveLength(1);
  });

  it("has nothing to draw when every point is null", () => {
    expect(sparkOf([null, null, null])).toBeNull();
    expect(sparkOf([])).toBeNull();
  });

  it("scales each series to its own min and max", () => {
    const small = sparkOf([1, 2])!;
    const large = sparkOf([1000, 2000])!;
    expect(small.lines).toEqual(large.lines);
    const ys = small.lines[0].split(" ").map((p) => Number(p.split(",")[1]));
    expect(ys[0]).toBeGreaterThan(ys[1]);
    expect(ys[1]).toBeLessThan(SPARK_H / 2);
  });

  it("puts a flat series in the middle and marks no peak", () => {
    const s = sparkOf([3, 3, 3])!;
    expect(s.peak).toBeNull();
    expect(s.lines[0]).toContain(`,${SPARK_H / 2}.0`);
  });

  it("marks the peak of a varying series", () => {
    expect(sparkOf([1, 9, 2])!.peak).not.toBeNull();
  });
});
