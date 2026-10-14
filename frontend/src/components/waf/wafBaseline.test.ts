import { describe, expect, it } from "vitest";

import { wafBaselineCell } from "./fixtures";
import { buildBaselineLookup, seasonalKey } from "./wafBaseline";

// 2026-10-12 is a Monday; 2026-10-18 a Sunday.
const at = (iso: string) => Date.parse(iso);

describe("seasonalKey", () => {
  it("is ISO weekday (Monday = 1, Sunday = 7) and the UTC hour", () => {
    expect(seasonalKey(at("2026-10-12T09:05:00Z"))).toEqual([1, 9]);
    expect(seasonalKey(at("2026-10-18T23:55:00Z"))).toEqual([7, 23]);
    // Not the viewer's zone: 00:30 UTC is still hour 0.
    expect(seasonalKey(at("2026-10-13T00:30:00Z"))).toEqual([2, 0]);
  });
});

describe("buildBaselineLookup", () => {
  it("finds the cell of the bucket's own weekday and hour", () => {
    const lookup = buildBaselineLookup(
      [
        wafBaselineCell({ weekday: 1, hour: 9, median: 41.5, p05: 22, p95: 68 }),
        wafBaselineCell({ weekday: 1, hour: 10, median: 90, p05: 70, p95: 120 }),
      ],
      "request_count",
      2,
    );
    expect(lookup(at("2026-10-12T09:05:00Z"))).toEqual({ median: 41.5, low: 22, high: 68 });
    expect(lookup(at("2026-10-12T10:00:00Z"))).toEqual({ median: 90, low: 70, high: 120 });
  });

  it("returns null, never a zero band, for a missing cell", () => {
    const lookup = buildBaselineLookup([wafBaselineCell({ weekday: 1, hour: 9 })], "request_count", 2);
    expect(lookup(at("2026-10-12T11:00:00Z"))).toBeNull();
    expect(lookup(at("2026-10-13T09:00:00Z"))).toBeNull();
  });

  it("returns null for a cell that is not reliable", () => {
    const lookup = buildBaselineLookup(
      [wafBaselineCell({ weekday: 1, hour: 9, is_reliable: false })],
      "request_count",
      2,
    );
    expect(lookup(at("2026-10-12T09:00:00Z"))).toBeNull();
  });

  it("gives a median but no band for a reliable cell without percentiles", () => {
    const lookup = buildBaselineLookup(
      [wafBaselineCell({ weekday: 1, hour: 9, p05: null, p95: null })],
      "request_count",
      2,
    );
    expect(lookup(at("2026-10-12T09:00:00Z"))).toEqual({ median: 41.5, low: null, high: null });
  });

  it("reads only the requested metric and normalizer version", () => {
    const lookup = buildBaselineLookup(
      [
        wafBaselineCell({ metric_name: "block_ratio", median: 0.02 }),
        wafBaselineCell({ normalizer_version: 1, median: 5 }),
      ],
      "request_count",
      2,
    );
    expect(lookup(at("2026-10-12T09:00:00Z"))).toBeNull();
  });
});
