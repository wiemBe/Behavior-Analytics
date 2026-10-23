import { describe, expect, it } from "vitest";

import { epsPoint, epsSeries, behavior } from "./fixtures";
import { bucketsAsOf, epsSeriesAsOf, rulesAsOf, sourcesAsOf, wafSourcesAsOf } from "./asOf";

const T = (m: number) => new Date(Date.parse("2026-10-22T10:00:00Z") + m * 60_000).toISOString();

describe("epsSeriesAsOf", () => {
  it("is the end of the last measured step, not the series' window_end", () => {
    const series = epsSeries(
      [
        { ...epsPoint(0, 5), step_start: T(0) },
        { ...epsPoint(1, 6), step_start: T(5) },
        { ...epsPoint(2, null), step_start: T(10) },
      ],
      { window_end: T(999) },
    );
    expect(epsSeriesAsOf(series)).toBe(T(10));
  });

  it("is null when no step was measured, never the window's end", () => {
    const series = epsSeries([epsPoint(0, null)], { window_end: T(999) });
    expect(epsSeriesAsOf(series)).toBeNull();
  });
});

describe("bucketsAsOf", () => {
  it("adds each bucket's own width to its start and takes the latest", () => {
    expect(
      bucketsAsOf([
        { bucket_start: T(0), bucket_seconds: 300 },
        { bucket_start: T(5), bucket_seconds: 300 },
      ]),
    ).toBe(T(10));
  });

  it("is null for no buckets and skips a start it cannot read", () => {
    expect(bucketsAsOf([])).toBeNull();
    expect(bucketsAsOf([{ bucket_start: "nope", bucket_seconds: 300 }])).toBeNull();
  });
});

describe("sourcesAsOf", () => {
  const sources = [
    behavior({ last_bucket_at: T(0) }),
    behavior({ last_bucket_at: T(20) }),
    behavior({ last_bucket_at: null }),
  ];

  it("is the newest last_bucket_at, completed by the bucket width when known", () => {
    expect(sourcesAsOf(sources, null)).toBe(T(20));
    expect(sourcesAsOf(sources, 300)).toBe(T(25));
    expect(sourcesAsOf(sources, 0)).toBe(T(20));
  });

  it("is null when no source has a bucket", () => {
    expect(sourcesAsOf([behavior({ last_bucket_at: null })], 300)).toBeNull();
    expect(sourcesAsOf([], 300)).toBeNull();
  });
});

describe("wafSourcesAsOf", () => {
  it("takes the newest latest bucket with its width and ignores a source without one", () => {
    const bucket = (start: string) =>
      ({ latest_bucket: { bucket_start: start, bucket_seconds: 300 } }) as never;
    expect(
      wafSourcesAsOf([bucket(T(0)), bucket(T(5)), { latest_bucket: null } as never]),
    ).toBe(T(10));
    expect(wafSourcesAsOf([{ latest_bucket: null } as never])).toBeNull();
  });
});

describe("rulesAsOf", () => {
  it("is the newest evaluation, or null when none was evaluated", () => {
    expect(
      rulesAsOf([
        { health_evaluated_at: T(0) },
        { health_evaluated_at: T(30) },
        { health_evaluated_at: null },
      ]),
    ).toBe(T(30));
    expect(rulesAsOf([{ health_evaluated_at: null }])).toBeNull();
  });
});
