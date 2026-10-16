import { describe, expect, it, vi } from "vitest";

import { loadWafEndpoints } from "./inventory";
import { api } from "@/lib/api";

describe("custom-rule subject inventory", () => {
  it("loads every page of an admitted endpoint inventory", async () => {
    const endpoint = (id: string) => ({
      id,
      normalized_endpoint: "/login",
      http_method: "POST",
      virtual_host: null,
      normalizer_version: 1,
      first_seen_at: "2026-10-16T07:00:00Z",
      last_seen_at: "2026-10-16T07:15:00Z",
      is_active: true,
      retired_at: null,
      tracked_since: "2026-10-16T07:00:00Z",
      baseline: { cells_total: 1, reliable_cells: 1 },
      baseline_rebuilt_at: null,
      state: null,
      active_anomaly_count: 0,
    });
    const request = vi.spyOn(api, "wafBehaviorEndpoints")
      .mockResolvedValueOnce({ items: [endpoint("endpoint-1")], total: 2, limit: 200, offset: 0 })
      .mockResolvedValueOnce({ items: [endpoint("endpoint-2")], total: 2, limit: 200, offset: 1 });

    await expect(loadWafEndpoints("waf-1", true)).resolves.toEqual([
      endpoint("endpoint-1"),
      endpoint("endpoint-2"),
    ]);
    expect(request).toHaveBeenNthCalledWith(1, "waf-1", {
      active_only: true,
      limit: 200,
      offset: 0,
    });
    expect(request).toHaveBeenNthCalledWith(2, "waf-1", {
      active_only: true,
      limit: 200,
      offset: 1,
    });
  });
});
