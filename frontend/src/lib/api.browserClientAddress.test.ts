// In the browser the client address is the browser's own to send, and `X-Forwarded-For` is a header
// a page may not set: the browser path never reads the incoming request or sends the header (RL-1).

import { afterEach, describe, expect, it, vi } from "vitest";

const read = vi.hoisted(() => ({ headers: 0 }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined }),
  headers: async () => {
    read.headers += 1;
    return new Headers({ "x-forwarded-for": "198.51.100.7" });
  },
}));

import { fleetApi, request } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("request in the browser", () => {
  it("never sends X-Forwarded-For and never reads the incoming headers", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await request("/alerts");
    await fleetApi.rules();
    for (const call of fetchMock.mock.calls) {
      expect(new Headers((call[1] as RequestInit).headers).get("X-Forwarded-For")).toBeNull();
    }
    expect(read.headers).toBe(0);
  });
});
