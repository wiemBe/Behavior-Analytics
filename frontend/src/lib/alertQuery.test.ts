import { describe, expect, it } from "vitest";

import {
  PAGE_SIZE,
  Q_MAX,
  alertFilterParams,
  alertHref,
  alertListParams,
  isAlertFiltered,
  parseAlertQuery,
  sortPatch,
} from "./alertQuery";

const NOW = Date.parse("2026-11-05T19:05:00Z");
const SRC_A = "6f1c0a52-3b8e-4a64-9d3d-0f9a7b2c1e11";
const SRC_B = "00000000-0000-4000-8000-000000000002";

describe("the URL and the alert view are the same thing", () => {
  it("round-trips every filter, the chip, the sort and the page", () => {
    const href =
      `/alerts?status=OPEN&severity=CRITICAL&severity=HIGH&family=waf` +
      `&log_source_id=${SRC_A}&log_source_id=${SRC_B}&q=block+ratio&range=24h` +
      `&sort=severity&order=asc&offset=100`;
    const url = new URL(href, "http://x");
    const params: Record<string, string | string[]> = {};
    for (const key of new Set(url.searchParams.keys())) {
      const all = url.searchParams.getAll(key);
      params[key] = all.length > 1 ? all : all[0];
    }
    const query = parseAlertQuery(params, NOW);
    expect(query.ignored).toEqual([]);
    expect(query).toMatchObject({
      status: "OPEN",
      severity: ["CRITICAL", "HIGH"],
      family: "waf",
      logSourceIds: [SRC_A, SRC_B],
      q: "block ratio",
      sort: "severity",
      order: "asc",
      offset: 100,
    });
    expect(query.range.chip).toBe("24h");
    // Writing it back gives the address it came from.
    expect(alertHref(query, { offset: 100 })).toBe(href);
  });

  it("leaves defaults and empty values out of the address", () => {
    const query = parseAlertQuery({ status: "", q: "   ", sort: "created_at", order: "desc" }, NOW);
    expect(query.ignored).toEqual([]);
    expect(alertHref(query)).toBe("/alerts");
    expect(isAlertFiltered(query)).toBe(false);
  });

  it("starts again at page one when a filter changes", () => {
    const query = parseAlertQuery({ status: "OPEN", offset: "200" }, NOW);
    expect(alertHref(query, { family: "ips" })).toBe("/alerts?status=OPEN&family=ips");
    expect(alertHref(query, { offset: 200 })).toBe("/alerts?status=OPEN&offset=200");
  });

  it("writes severities in severity order however they arrived", () => {
    const query = parseAlertQuery({ severity: ["LOW", "CRITICAL"] }, NOW);
    expect(query.severity).toEqual(["CRITICAL", "LOW"]);
  });
});

describe("the range inside the alert view", () => {
  it("keeps a chip relative: range=24h, not the instants", () => {
    const query = parseAlertQuery({ range: "24h" }, NOW);
    expect(alertHref(query)).toBe("/alerts?range=24h");
    expect(alertFilterParams(query).since).toBe("2026-11-04T19:05:00.000Z");
    expect(alertFilterParams(query).until).toBeUndefined();
  });

  it("writes a Custom window as absolute UTC", () => {
    const query = parseAlertQuery({ since: "2026-11-05T00:00", until: "2026-11-05T12:00" }, NOW);
    expect(alertHref(query)).toBe(
      "/alerts?since=2026-11-05T00%3A00%3A00Z&until=2026-11-05T12%3A00%3A00Z",
    );
  });
});

describe("values dropped from the address", () => {
  it.each([
    [{ status: "BOGUS" }, "status"],
    [{ severity: ["HIGH", "BOGUS"] }, "severity"],
    [{ family: "ips,waf" }, "family"],
    [{ log_source_id: "not-a-uuid" }, "logSource"],
    [{ sort: "colour" }, "sort"],
    [{ order: "sideways" }, "order"],
    [{ range: "2d" }, "unknownRange"],
    [{ since: "yesterday" }, "badSince"],
  ])("drops %j and names it %s", (params, issue) => {
    const query = parseAlertQuery(params, NOW);
    expect(query.ignored).toContain(issue);
    // What was valid beside it is kept, and nothing invalid is sent on.
    const sent = alertListParams(query);
    expect(JSON.stringify(sent)).not.toMatch(/BOGUS|not-a-uuid|colour|sideways|yesterday/);
  });

  it("keeps the valid severity beside an unknown one", () => {
    expect(parseAlertQuery({ severity: ["BOGUS", "HIGH"] }, NOW).severity).toEqual(["HIGH"]);
  });

  it("lets a UUID through in any case, once, and caps how many sources a link may name", () => {
    expect(
      parseAlertQuery({ log_source_id: [SRC_A.toUpperCase(), SRC_A] }, NOW).logSourceIds,
    ).toEqual([SRC_A]);
    const many = Array.from({ length: 40 }, (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`);
    const query = parseAlertQuery({ log_source_id: many }, NOW);
    expect(query.logSourceIds).toHaveLength(25);
    expect(query.ignored).toContain("logSource");
  });

  it("treats a negative or non-numeric offset as the first page", () => {
    expect(parseAlertQuery({ offset: "-5" }, NOW).offset).toBe(0);
    expect(parseAlertQuery({ offset: "abc" }, NOW).offset).toBe(0);
  });
});

describe("the search text", () => {
  it("is trimmed, and a blank search sends nothing", () => {
    const query = parseAlertQuery({ q: "  edge waf  " }, NOW);
    expect(query.q).toBe("edge waf");
    expect(alertFilterParams(parseAlertQuery({ q: "   " }, NOW)).q).toBeUndefined();
  });

  it("is accepted at exactly the backend's bound and dropped, not cut, above it", () => {
    expect(parseAlertQuery({ q: "x".repeat(Q_MAX) }, NOW).q).toHaveLength(Q_MAX);
    const over = parseAlertQuery({ q: "x".repeat(Q_MAX + 1) }, NOW);
    expect(over.q).toBe("");
    expect(over.ignored).toContain("q");
  });
});

describe("the request", () => {
  it("sends the same filters to the list and the counts, and the defaults unsent", () => {
    const query = parseAlertQuery({ severity: "HIGH", range: "1h" }, NOW);
    const list = alertListParams(query);
    expect(list).toMatchObject({ severity: ["HIGH"], limit: PAGE_SIZE, offset: 0 });
    expect(list.sort).toBeUndefined();
    expect(list.order).toBeUndefined();
    expect(alertFilterParams(query)).toEqual({
      status: undefined,
      severity: ["HIGH"],
      log_source_id: undefined,
      family: undefined,
      q: undefined,
      since: "2026-11-05T18:05:00.000Z",
      until: undefined,
    });
  });

  it("sends a non-default sort", () => {
    expect(alertListParams(parseAlertQuery({ sort: "severity", order: "asc" }, NOW))).toMatchObject({
      sort: "severity",
      order: "asc",
    });
  });

  it("starts a column at descending and reverses it on the second click", () => {
    const fresh = parseAlertQuery({}, NOW);
    expect(sortPatch(fresh, "severity")).toEqual({ sort: "severity", order: "desc" });
    const sorted = parseAlertQuery({ sort: "severity", order: "desc" }, NOW);
    expect(sortPatch(sorted, "severity")).toEqual({ sort: "severity", order: "asc" });
    // The default sort is the opened time, newest first: its first click reverses it.
    expect(sortPatch(fresh, "created_at")).toEqual({ sort: "created_at", order: "asc" });
  });
});
