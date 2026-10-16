// The two shared fetch helpers send `Content-Type: application/json` only when the request has a
// body (F3): a bodyless GET that carries it is not a CORS-simple request, so every cross-origin
// read pays a preflight. `cache: "no-store"` stays either way.

import { afterEach, describe, expect, it, vi } from "vitest";

import { fleetApi, request } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubJson() {
  const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function initOf(fetchMock: ReturnType<typeof stubJson>): RequestInit {
  return fetchMock.mock.calls[0][1] as RequestInit;
}

function header(init: RequestInit, name: string): string | undefined {
  const headers = init.headers as Record<string, string> | undefined;
  return headers?.[name];
}

describe("request: Content-Type only with a body", () => {
  it("sends no Content-Type on a bodyless GET, and keeps cache: no-store", async () => {
    const fetchMock = stubJson();
    await request("/x");
    const init = initOf(fetchMock);
    expect(init.method).toBeUndefined();
    expect(header(init, "Content-Type")).toBeUndefined();
    expect(init.cache).toBe("no-store");
  });

  it("sends application/json on a POST with a body", async () => {
    const fetchMock = stubJson();
    await request("/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
    expect(header(initOf(fetchMock), "Content-Type")).toBe("application/json");
  });

  it("lets a caller's own header win", async () => {
    const fetchMock = stubJson();
    await request("/x", {
      method: "POST",
      body: "{}",
      headers: { "Content-Type": "text/plain", "X-Trace": "1" },
    });
    const init = initOf(fetchMock);
    expect(header(init, "Content-Type")).toBe("text/plain");
    expect(header(init, "X-Trace")).toBe("1");
  });

  it("does not strip a Content-Type the caller set on a bodyless GET", async () => {
    const fetchMock = stubJson();
    await request("/x", { headers: { "Content-Type": "application/json" } });
    expect(header(initOf(fetchMock), "Content-Type")).toBe("application/json");
  });
});

describe("fleetRequest: Content-Type only with a body", () => {
  it("sends no Content-Type on a GET", async () => {
    const fetchMock = stubJson();
    await fleetApi.summary("24h");
    expect(header(initOf(fetchMock), "Content-Type")).toBeUndefined();
  });

  it("sends no Content-Type on a bodyless DELETE", async () => {
    const fetchMock = stubJson();
    await fleetApi.deleteRule("r");
    expect(header(initOf(fetchMock), "Content-Type")).toBeUndefined();
  });

  it("sends no Content-Type on a bodyless POST", async () => {
    const fetchMock = stubJson();
    await fleetApi.markReviewed("s");
    expect(header(initOf(fetchMock), "Content-Type")).toBeUndefined();
  });

  it("sends application/json on a PATCH with a body", async () => {
    const fetchMock = stubJson();
    await fleetApi.setOverride("s", null);
    expect(header(initOf(fetchMock), "Content-Type")).toBe("application/json");
  });
});
