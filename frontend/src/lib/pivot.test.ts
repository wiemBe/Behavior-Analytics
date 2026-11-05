import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { loadPivot, type Pivot } from "@/lib/pivot";

const PIVOT: Pivot = {
  kind: "anomaly",
  aql: "SELECT 1",
  console_url: null,
  aql_search_url: null,
  console_unavailable_reason: "none",
  time_range: { start: "2026-11-05T10:00:00Z", stop: "2026-11-05T10:30:00Z" },
  notes: [],
};

describe("loadPivot", () => {
  it("returns the pivot when the read succeeds", async () => {
    expect(await loadPivot(async () => PIVOT)).toEqual({ pivot: PIVOT, failed: false });
  });

  it("returns null for a 404, so the page omits the block", async () => {
    expect(await loadPivot(async () => Promise.reject(new ApiError(404, "gone")))).toBeNull();
  });

  it("returns failed for any other error, so the block says so", async () => {
    for (const err of [new ApiError(500, "boom"), new ApiError(403, "no"), new Error("net")]) {
      expect(await loadPivot(async () => Promise.reject(err))).toEqual({
        pivot: null,
        failed: true,
      });
    }
  });
});
