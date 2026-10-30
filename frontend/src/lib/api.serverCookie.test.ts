// @vitest-environment node
// A Server Component's `fetch` has no cookie jar: `request` forwards the incoming request's session
// cookie by hand, that cookie only, on a read only, per call (HOTFIX-1).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jar = vi.hoisted(() => ({ cookies: new Map<string, string>(), outside: false }));
vi.mock("next/headers", () => ({
  cookies: async () => {
    if (jar.outside) throw new Error("cookies was called outside a request scope");
    return { get: (name: string) => (jar.cookies.has(name) ? { name, value: jar.cookies.get(name) } : undefined) };
  },
}));

import { ApiError, fleetApi, request } from "./api";

function stubFetch(status = 200) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response("{}", { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentCookie(fetchMock: ReturnType<typeof stubFetch>): string | null {
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return new Headers(init.headers).get("Cookie");
}

beforeEach(() => {
  jar.cookies = new Map();
  jar.outside = false;
});
afterEach(() => vi.unstubAllGlobals());

describe("request on the server", () => {
  it("forwards the session cookie on a GET, and nothing else", async () => {
    jar.cookies.set("qd_session", "abc123");
    jar.cookies.set("ba_locale", "tr");
    jar.cookies.set("other", "x");
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sentCookie(fetchMock)).toBe("qd_session=abc123");
  });

  it("forwards it on a HEAD", async () => {
    jar.cookies.set("qd_session", "abc123");
    const fetchMock = stubFetch();
    await request("/alerts", { method: "HEAD" });
    expect(sentCookie(fetchMock)).toBe("qd_session=abc123");
  });

  it("sends no header without the cookie, even with other cookies", async () => {
    jar.cookies.set("ba_locale", "tr");
    const fetchMock = stubFetch();
    await request("/alerts");
    expect(sentCookie(fetchMock)).toBeNull();
  });

  it.each(["POST", "PUT", "PATCH", "DELETE"])("sends no cookie on a %s", async (method) => {
    jar.cookies.set("qd_session", "abc123");
    const fetchMock = stubFetch();
    await request("/alerts", { method, body: "{}" });
    expect(sentCookie(fetchMock)).toBeNull();
  });

  it("lets a caller's own Cookie header win", async () => {
    jar.cookies.set("qd_session", "abc123");
    const fetchMock = stubFetch();
    await request("/alerts", { headers: { Cookie: "qd_session=mine" } });
    expect(sentCookie(fetchMock)).toBe("qd_session=mine");
  });

  it("sends no header outside a request scope, and does not throw", async () => {
    jar.outside = true;
    const fetchMock = stubFetch();
    await expect(request("/alerts")).resolves.toEqual({});
    expect(sentCookie(fetchMock)).toBeNull();
  });

  it("reads the cookie on every call: a later request sees the later cookie", async () => {
    const fetchMock = stubFetch();
    jar.cookies.set("qd_session", "first");
    await request("/a");
    jar.cookies.set("qd_session", "second");
    await request("/b");
    jar.cookies.delete("qd_session");
    await request("/c");
    const seen = fetchMock.mock.calls.map((c) => new Headers(c[1]?.headers).get("Cookie"));
    expect(seen).toEqual(["qd_session=first", "qd_session=second", null]);
  });

  it("never puts the cookie value in an ApiError", async () => {
    jar.cookies.set("qd_session", "s3cret-value");
    stubFetch(401);
    const err = await request("/alerts").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(JSON.stringify([(err as ApiError).message, (err as ApiError).detail, (err as ApiError).body])).not.toContain(
      "s3cret-value",
    );
  });

  it("forwards it on the fleet client too", async () => {
    jar.cookies.set("qd_session", "abc123");
    const fetchMock = stubFetch();
    await fleetApi.rules();
    expect(sentCookie(fetchMock)).toBe("qd_session=abc123");
  });
});
