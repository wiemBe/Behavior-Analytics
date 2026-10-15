// The reads of the configurable catalog widgets (UI-3.5b): the URL each one requests carries the
// window the widget's range names, and the paging is bounded.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads, windowBounds } from "./api";

const NOW = Date.parse("2026-10-14T14:00:30Z");
const fetched: string[] = [];

function respond(handler: (url: URL) => unknown) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    // The client prefixes the API base; the assertions are about the route.
    const url = new URL(String(input).replace("/api/v1", ""), "http://x");
    fetched.push(url.pathname + url.search);
    return new Response(JSON.stringify(handler(url)), { status: 200 });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  fetched.length = 0;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

const signal = new AbortController().signal;
const span = (url: URL) => Date.parse(url.searchParams.get("until")!) - Date.parse(url.searchParams.get("since")!);

describe("windowBounds", () => {
  it("ends at now rounded down to the minute and starts one preset earlier", () => {
    expect(windowBounds("1h", NOW)).toEqual({
      since: "2026-10-14T13:00:00.000Z",
      until: "2026-10-14T14:00:00.000Z",
    });
    expect(Date.parse(windowBounds("7d", NOW).until) - Date.parse(windowBounds("7d", NOW).since)).toBe(
      7 * 86_400_000,
    );
  });
});

describe("the endpoint series read", () => {
  it.each([
    ["1h", 3_600_000],
    ["24h", 86_400_000],
    ["7d", 604_800_000],
  ] as const)("asks for %s with since and until", async (window, ms) => {
    let seen: URL | null = null;
    respond((url) => {
      seen = url;
      return { items: [], total: 0, limit: 200, offset: 0 };
    });
    await homeReads.wafEndpointSeries("e1", window).fetch(signal);
    expect(seen!.pathname).toBe("/behavior/waf/endpoints/e1/metrics");
    expect(span(seen!)).toBe(ms);
  });

  it("names the preset in its identity, not the instants, so a refresh keeps the chart", () => {
    expect(homeReads.wafEndpointSeries("e1", "7d").key).toBe(
      "/behavior/waf/endpoints/e1/metrics?window=7d",
    );
  });

  it("pages until it holds every bucket, and says so when it stopped early", async () => {
    const bucket = (i: number) => ({ bucket_start: `b${i}` });
    respond((url) => {
      const offset = Number(url.searchParams.get("offset"));
      const n = Math.min(200, 288 - offset);
      return { items: Array.from({ length: n }, (_, i) => bucket(offset + i)), total: 288, limit: 200, offset };
    });
    const got = await homeReads.wafEndpointSeries("e1", "24h").fetch(signal);
    expect(got.buckets).toHaveLength(288);
    expect(got.truncated).toBe(false);
    expect(fetched.map((u) => new URL(u, "http://x").searchParams.get("offset"))).toEqual(["0", "200"]);

    fetched.length = 0;
    respond((url) => {
      const offset = Number(url.searchParams.get("offset"));
      return { items: Array.from({ length: 200 }, (_, i) => bucket(offset + i)), total: 9000, limit: 200, offset };
    });
    const cut = await homeReads.wafEndpointSeries("e1", "7d").fetch(signal);
    expect(fetched).toHaveLength(12);
    expect(cut.buckets).toHaveLength(2400);
    expect(cut.total).toBe(9000);
    expect(cut.truncated).toBe(true);
  });
});

describe("the source series read", () => {
  it("asks for the window with the route's largest page, and flags a full page", async () => {
    let seen: URL | null = null;
    respond((url) => {
      seen = url;
      return Array.from({ length: 5000 }, (_, i) => ({ bucket_start: `b${i}` }));
    });
    const got = await homeReads.sourceSeries("s1", "7d").fetch(signal);
    expect(seen!.pathname).toBe("/behavior/sources/s1/metrics");
    expect(span(seen!)).toBe(604_800_000);
    expect(seen!.searchParams.get("limit")).toBe("5000");
    expect(got.truncated).toBe(true);
  });

  it("is not truncated when the page has room left", async () => {
    respond(() => [{ bucket_start: "b0" }]);
    const got = await homeReads.sourceSeries("s1", "1h").fetch(signal);
    expect(got).toMatchObject({ total: 1, truncated: false });
  });
});

describe("the baseline reads", () => {
  it("asks for one metric only", async () => {
    respond(() => ({ items: [], total: 0, limit: 200, offset: 0 }));
    await homeReads.wafEndpointCells("e1").fetch(signal);
    expect(fetched[0]).toBe("/behavior/waf/endpoints/e1/baselines?metric=request_count&limit=200&offset=0");
    expect(homeReads.sourceCells("s1").key).toBe("/behavior/sources/s1/baselines?metric=average_eps");
  });
});

describe("the option reads", () => {
  it("reads one WAF source's endpoints, at the API's page size", () => {
    expect(homeReads.wafEndpointsOf("src1").key).toBe("/behavior/waf/sources/src1/endpoints?limit=200");
  });
});
