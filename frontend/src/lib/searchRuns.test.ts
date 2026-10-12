import { describe, expect, it } from "vitest";

import type { ResultMetricPoint, SearchExecution } from "./api";
import {
  axisTimeLabel,
  formatRunDuration,
  isolatedIndices,
  resultCount, thresholdVerdict,
  trendRows,
  truncatedRunTimes,
} from "./searchRuns";

function exec(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1", search_id: "s-1", query_version: 1, status: "COMPLETED", trigger: "SCHEDULED",
    triggered_by: null, ariel_search_id: null, ariel_status: null,
    started_at: "2026-07-20T10:00:00Z", completed_at: "2026-07-20T10:00:30Z",
    duration_ms: 1000, result_count: 10, truncated: false, error_type: null,
    error_message: null, retry_count: 0, threshold_breached: false, ...over,
  };
}

function point(at: string): ResultMetricPoint {
  return {
    bucket_start: at, metric_key: "total", value: 5, dimensions: {}, execution_id: "e",
    execution_status: "COMPLETED", duration_ms: 1, result_count: 5, threshold_breached: false,
    query_version: 1, query_version_id: null,
  };
}

describe("resultCount", () => {
  it("reads a truncated count as a lower bound", () => {
    expect(resultCount(exec({ truncated: true, result_count: 500 }))).toEqual({
      kind: "lower-bound", value: 500,
    });
  });
  it("keeps a measured zero as a measured zero", () => {
    expect(resultCount(exec({ result_count: 0 }))).toEqual({ kind: "measured", value: 0 });
  });
  it("does not turn a missing count into zero", () => {
    expect(resultCount(exec({ result_count: null }))).toEqual({ kind: "none", value: null });
    expect(resultCount(exec({ truncated: true, result_count: null })).kind).toBe(
      "truncated-unknown",
    );
  });
});

describe("thresholdVerdict", () => {
  it("is breached whenever it breached, truncated or not", () => {
    expect(thresholdVerdict({ threshold_breached: true, truncated: true })).toBe("breached");
    expect(thresholdVerdict({ threshold_breached: true, truncated: false })).toBe("breached");
  });
  it("gives a truncated run that did not breach no verdict", () => {
    expect(thresholdVerdict({ threshold_breached: false, truncated: true })).toBe("no-verdict");
  });
  it("keeps a measured run within its threshold distinct from no verdict", () => {
    expect(thresholdVerdict({ threshold_breached: false, truncated: false })).toBe("within");
  });
});

describe("truncatedRunTimes", () => {
  it("lists only truncated runs, by completion time, falling back to start", () => {
    expect(
      truncatedRunTimes([
        exec({ id: "a", truncated: true }),
        exec({ id: "b", truncated: false }),
        exec({ id: "c", truncated: true, completed_at: null, started_at: "2026-07-20T09:00:00Z" }),
        exec({ id: "d", truncated: true, completed_at: null, started_at: null }),
      ]),
    ).toEqual(["2026-07-20T10:00:30Z", "2026-07-20T09:00:00Z"]);
  });
});

describe("trendRows", () => {
  const pts = [
    point("2026-07-20T10:00:00Z"),
    point("2026-07-20T12:00:00Z"),
    point("2026-07-20T13:00:00Z"),
  ];

  it("returns the points unchanged when no run was truncated", () => {
    const rows = trendRows(pts, []);
    expect(rows.map((r) => r.point)).toEqual(pts);
  });

  it("puts an empty position between the points a truncated run falls between", () => {
    const rows = trendRows(pts, ["2026-07-20T11:00:00Z"]);
    expect(rows.map((r) => r.point?.bucket_start ?? null)).toEqual([
      "2026-07-20T10:00:00Z",
      null,
      "2026-07-20T12:00:00Z",
      "2026-07-20T13:00:00Z",
    ]);
  });

  it("keeps a trailing truncated run as a gap at the end", () => {
    const rows = trendRows(pts, ["2026-07-20T14:00:00Z"]);
    expect(rows[rows.length - 1]).toEqual({ at: "2026-07-20T14:00:00Z", point: null });
  });

  it("ignores a truncated run older than the first point", () => {
    expect(trendRows(pts, ["2026-07-19T00:00:00Z"])).toHaveLength(3);
  });

  it("orders several gaps by time", () => {
    const rows = trendRows(pts, ["2026-07-20T12:30:00Z", "2026-07-20T11:00:00Z"]);
    expect(rows.map((r) => r.point === null)).toEqual([false, true, false, true, false]);
  });

  it("has nothing to draw without points", () => {
    expect(trendRows([], ["2026-07-20T11:00:00Z"])).toEqual([]);
  });
});

describe("formatRunDuration", () => {
  it("gives every duration a unit", () => {
    expect(formatRunDuration(850)).toBe("850 ms");
    expect(formatRunDuration(41200)).toBe("41.2 s");
    expect(formatRunDuration(30000)).toBe("30 s");
    expect(formatRunDuration(300000)).toBe("5 min 0 s");
    expect(formatRunDuration(125000)).toBe("2 min 5 s");
  });
  it("keeps a measured zero and does not turn null into one", () => {
    expect(formatRunDuration(0)).toBe("0 ms");
    expect(formatRunDuration(null)).toBe("—");
  });
});

describe("axisTimeLabel", () => {
  it("is the UTC time without the zone, which the axis title carries", () => {
    expect(axisTimeLabel("2026-10-12T09:05:40Z")).toBe("10-12 09:05");
  });
});

describe("isolatedIndices", () => {
  it("finds a measured value with no measured neighbour", () => {
    expect(isolatedIndices([1, 2, null, 3, null, 4, 5])).toEqual([3]);
  });
  it("counts the ends as having no neighbour", () => {
    expect(isolatedIndices([7, null, 8])).toEqual([0, 2]);
    expect(isolatedIndices([9])).toEqual([0]);
  });
  it("finds none in a connected line or an all-gap series", () => {
    expect(isolatedIndices([1, 2, 3])).toEqual([]);
    expect(isolatedIndices([null, null])).toEqual([]);
  });
});
