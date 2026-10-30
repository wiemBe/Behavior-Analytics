// @vitest-environment node
// A server-rendered read reaches the backend from this container, which the backend trusts as a
// forwarder. It forwards the client address the bundled proxy appended (the LAST hop), so the
// backend counts the call under the analyst and not under the container (RL-1).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const incoming = vi.hoisted(() => ({ forwarded: null as string | null, outside: false }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined }),
  headers: async () => {
    if (incoming.outside) throw new Error("headers was called outside a request scope");
    return new Headers(incoming.forwarded === null ? {} : { "x-forwarded-for": incoming.forwarded });
  },
}));

import { fleetApi, request } from "./api";

function stubFetch() {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sent(fetchMock: ReturnType<typeof stubFetch>, call = 0): string | null {
  const init = fetchMock.mock.calls[call][1] as RequestInit;
  return new Headers(init.headers).get("X-Forwarded-For");
}

beforeEach(() => {
  incoming.forwarded = null;
  incoming.outside = false;
});
afterEach(() => vi.unstubAllGlobals());

describe("the client address a server render forwards", () => {
  it("sends the incoming address on a GET", async () => {
    incoming.forwarded = "198.51.100.7";
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sent(fetchMock)).toBe("198.51.100.7");
  });

  it("sends only the last hop, never an earlier one the browser could have written", async () => {
    incoming.forwarded = "6.6.6.6, 10.0.0.9, 198.51.100.7";
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sent(fetchMock)).toBe("198.51.100.7");
  });

  it("sends the last hop of a header that ends in a space or an empty entry", async () => {
    incoming.forwarded = "6.6.6.6 , 198.51.100.7 ";
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sent(fetchMock)).toBe("198.51.100.7");
  });

  it("sends an IPv6 address", async () => {
    incoming.forwarded = "x, 2001:db8::7";
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sent(fetchMock)).toBe("2001:db8::7");
  });

  it("sends no header when the incoming request had none", async () => {
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sent(fetchMock)).toBeNull();
  });

  it.each(["", " ", "6.6.6.6,", "not-an-address", "1.2.3.4; evil", "unknown"])(
    "sends no header for the unusable last hop %j",
    async (value) => {
      incoming.forwarded = value;
      const fetchMock = stubFetch();
      await request("/alerts");
      expect(sent(fetchMock)).toBeNull();
    },
  );

  it.each(["POST", "PUT", "PATCH", "DELETE"])("sends none on a %s", async (method) => {
    incoming.forwarded = "198.51.100.7";
    const fetchMock = stubFetch();
    await request("/alerts", { method, body: "{}" });
    expect(sent(fetchMock)).toBeNull();
  });

  it("lets a caller's own X-Forwarded-For win", async () => {
    incoming.forwarded = "198.51.100.7";
    const fetchMock = stubFetch();
    await request("/alerts", { headers: { "X-Forwarded-For": "203.0.113.1" } });
    expect(sent(fetchMock)).toBe("203.0.113.1");
  });

  it("sends none outside a request scope, and does not throw", async () => {
    incoming.outside = true;
    const fetchMock = stubFetch();
    await expect(request("/alerts")).resolves.toEqual({});
    expect(sent(fetchMock)).toBeNull();
  });

  it("reads the header on every call: a later request sees the later address", async () => {
    const fetchMock = stubFetch();
    incoming.forwarded = "198.51.100.1";
    await request("/a");
    incoming.forwarded = "198.51.100.2";
    await request("/b");
    incoming.forwarded = null;
    await request("/c");
    expect([0, 1, 2].map((i) => sent(fetchMock, i))).toEqual(["198.51.100.1", "198.51.100.2", null]);
  });

  it("forwards it on the fleet client too", async () => {
    incoming.forwarded = "198.51.100.7";
    const fetchMock = stubFetch();
    await fleetApi.rules();
    expect(sent(fetchMock)).toBe("198.51.100.7");
  });
});
