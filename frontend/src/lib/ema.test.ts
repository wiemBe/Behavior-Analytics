import { describe, expect, it } from "vitest";

import { bucketSecondsOf, EMA_PERIODS, ema, hasEmaRun, spanText } from "./ema";

const ramp = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22];

describe("ema", () => {
  it.each(EMA_PERIODS)("EMA %i first has a value at index period - 1, the mean of the first period", (p) => {
    const out = ema(ramp, p);
    expect(out.slice(0, p - 1).every((v) => v === null)).toBe(true);
    const mean = ramp.slice(0, p).reduce((a, b) => a + b, 0) / p;
    expect(out[p - 1]).toBeCloseTo(mean, 10);
  });

  it("follows alpha = 2 / (period + 1) on a hand-computed series", () => {
    // period 3: alpha 0.5. Seed (2+4+6)/3 = 4; then 0.5*10+0.5*4 = 7; 0.5*0+0.5*7 = 3.5.
    expect(ema([2, 4, 6, 10, 0], 3)).toEqual([null, null, 4, 7, 3.5]);
    // period 5: alpha 1/3. Seed 3; then 8/3 + 2 = 14/3.
    const out = ema([1, 2, 3, 4, 5, 8], 5);
    expect(out[4]).toBeCloseTo(3, 10);
    expect(out[5]).toBeCloseTo((1 / 3) * 8 + (2 / 3) * 3, 10);
  });

  it("ends the line at a null and restarts the warm-up after it", () => {
    const out = ema([2, 4, 6, 10, null, 1, 1, 1, 5], 3);
    expect(out.slice(0, 4)).toEqual([null, null, 4, 7]);
    expect(out[4]).toBeNull();
    expect(out[5]).toBeNull();
    expect(out[6]).toBeNull();
    // Seed of the new run is the mean of 1,1,1 only: nothing carries over the gap.
    expect(out[7]).toBe(1);
    expect(out[8]).toBe(3);
  });

  it("draws nothing when a gap keeps interrupting before the warm-up completes", () => {
    expect(ema([1, 2, null, 3, 4, null, 5, 6], 3)).toEqual(new Array(8).fill(null));
  });

  it("gives all nulls for a series shorter than the period", () => {
    expect(ema([1, 2, 3], 5)).toEqual([null, null, null]);
    expect(ema([], 3)).toEqual([]);
  });

  it("averages a real zero as zero and never treats a null as one", () => {
    expect(ema([0, 0, 0], 3)).toEqual([null, null, 0]);
    const out = ema([3, 0, 3, 3], 2);
    expect(out.slice(0, 3)).toEqual([null, 1.5, 2.5]);
    expect(out[3]).toBeCloseTo((2 / 3) * 3 + (1 / 3) * 2.5, 10);
  });

  it("keeps the input length and does not mutate the input", () => {
    const input = [1, null, 3, 4, 5];
    const copy = [...input];
    expect(ema(input, 2)).toHaveLength(input.length);
    expect(input).toEqual(copy);
  });

  it("never exceeds the observed extremes", () => {
    const series = [3, 9, 1, 7, 2, 8, 4, 4, 9, 1];
    const out = ema(series, 3).filter((v): v is number => v != null);
    expect(Math.max(...out)).toBeLessThanOrEqual(Math.max(...series));
    expect(Math.min(...out)).toBeGreaterThanOrEqual(Math.min(...series));
  });

  it("returns all nulls for an invalid period", () => {
    expect(ema([1, 2, 3], 0)).toEqual([null, null, null]);
  });

  it("hasEmaRun tells whether anything is drawn", () => {
    expect(hasEmaRun([1, 2, 3], 3)).toBe(true);
    expect(hasEmaRun([1, null, 3], 3)).toBe(false);
  });
});

describe("span text", () => {
  it("states the span from the bucket length", () => {
    expect(spanText(20, 300)).toBe("100 min");
    expect(spanText(3, 3600)).toBe("3 h");
    expect(spanText(3, 300)).toBe("15 min");
    expect(spanText(20, 3600)).toBe("20 h");
    expect(spanText(10, 7200)).toBe("20 h");
  });

  it("falls back to buckets when the length is unknown", () => {
    expect(spanText(20, null)).toBe("20 buckets");
  });

  it("finds a single bucket length or none", () => {
    expect(bucketSecondsOf([{ bucket_seconds: 300 }, { bucket_seconds: 300 }])).toBe(300);
    expect(bucketSecondsOf([{ bucket_seconds: 300 }, { bucket_seconds: 3600 }])).toBeNull();
    expect(bucketSecondsOf([])).toBeNull();
  });
});
