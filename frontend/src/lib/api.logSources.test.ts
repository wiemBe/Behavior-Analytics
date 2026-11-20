import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

describe("api.logSources", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("writes the query in a fixed order whatever order the caller used", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await api.logSources({ offset: 40, limit: 20, ids: ["a", "b"], q: "fw" });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url.slice(url.indexOf("/log-sources"))).toBe(
      "/log-sources?q=fw&ids=a&ids=b&limit=20&offset=40",
    );
  });
});
