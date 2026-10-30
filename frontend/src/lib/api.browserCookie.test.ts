// The browser path is unchanged: the browser's own jar carries the cookie, and `next/headers`
// is never imported there (HOTFIX-1).

import { afterEach, describe, expect, it, vi } from "vitest";

const loaded = vi.hoisted(() => ({ headers: 0 }));
vi.mock("next/headers", () => {
  loaded.headers += 1;
  return { cookies: async () => ({ get: () => ({ value: "leak" }) }) };
});

import { request } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("request in the browser", () => {
  it("sets no Cookie header and never loads next/headers", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await request("/alerts");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toBeUndefined();
    expect(loaded.headers).toBe(0);
  });
});
