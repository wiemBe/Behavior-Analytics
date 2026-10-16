import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function ok(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  };
}

describe("search authoring requests", () => {
  it("patches enabled alone", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ id: "s-1" }));
    vi.stubGlobal("fetch", fetchMock);
    await api.setSearchEnabled("s-1", true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/searches\/s-1$/);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ enabled: true });
  });

  it("posts a run with no query body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ id: "e-1" }));
    vi.stubGlobal("fetch", fetchMock);
    await api.runSearch("s-9");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/searches\/s-9\/run$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});
