// The Geography read: the URL carries the window the range names and the direction, and the
// identity of the read names the preset and the direction, not the instants.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { geoApi, homeReads } from "./api";

const NOW = Date.parse("2026-11-02T10:00:30Z");
const fetched: string[] = [];

function respond(body: unknown) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    fetched.push(String(input).replace(/^.*\/api\/v1/, ""));
    return new Response(JSON.stringify(body), { status: 200 });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  fetched.length = 0;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

const signal = new AbortController().signal;
const query = () => new URL(fetched[0], "http://x");

describe("the geography reads", () => {
  it.each([
    ["1h", 3_600_000],
    ["24h", 86_400_000],
    ["7d", 604_800_000],
  ] as const)("ask for %s with since, until and the direction", async (window, ms) => {
    respond({});
    await homeReads.geoCountries(window, "DESTINATION").fetch(signal);
    const url = query();
    expect(url.pathname).toBe("/geo/countries");
    expect(url.searchParams.get("direction")).toBe("DESTINATION");
    expect(url.searchParams.get("until")).toBe("2026-11-02T10:00:00.000Z");
    expect(Date.parse(url.searchParams.get("until")!) - Date.parse(url.searchParams.get("since")!)).toBe(ms);
  });

  it("names the preset and the direction in the identity, so a refresh keeps the map", () => {
    expect(homeReads.geoCountries("7d", "SOURCE").key).toBe("/geo/countries?window=7d&direction=SOURCE");
    expect(homeReads.geoCountries("7d", "DESTINATION").key).not.toBe(
      homeReads.geoCountries("7d", "SOURCE").key,
    );
  });

  it("reads the same path for the page", async () => {
    respond({});
    await geoApi.countries("24h", "SOURCE");
    expect(query().pathname).toBe("/geo/countries");
    expect(query().searchParams.get("direction")).toBe("SOURCE");
  });
});
