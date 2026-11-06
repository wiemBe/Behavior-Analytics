import { describe, expect, it } from "vitest";

import {
  ariaSort,
  formatRate,
  paginationParams,
  parseTrafficQuery,
  sortHref,
  trafficHref,
  unattributedShare,
} from "./traffic";

describe("parseTrafficQuery", () => {
  it("defaults to the WAF view, 1 hour, rate descending, 10 rows", () => {
    expect(parseTrafficQuery({})).toEqual({
      view: "waf",
      window: "1h",
      sort: "rate",
      order: "desc",
      limit: 10,
      offset: 0,
      source: "",
    });
  });

  it("reads every parameter and gives text sorts their ascending default", () => {
    const q = parseTrafficQuery({
      view: "ips",
      window: "28d",
      sort: "ip",
      limit: "50",
      offset: "100",
      source: "abc-123",
    });
    expect(q).toMatchObject({ view: "ips", window: "28d", sort: "ip", order: "asc", limit: 50, offset: 100, source: "abc-123" });
  });

  it("drops what it does not know instead of forwarding it", () => {
    const q = parseTrafficQuery({
      view: "dns",
      window: "forever",
      sort: "nonsense",
      limit: "7",
      offset: "-4",
      source: "../../etc",
    });
    expect(q.view).toBe("waf");
    expect(q.window).toBe("1h");
    expect(q.limit).toBe(10);
    expect(q.offset).toBe(0);
    expect(q.source).toBe("");
  });

  it("rejects a WAF-only sort on the IPS view", () => {
    expect(parseTrafficQuery({ view: "ips", sort: "host" }).sort).toBe("rate");
    expect(parseTrafficQuery({ view: "waf", sort: "ip" }).sort).toBe("rate");
  });
});

describe("links", () => {
  const base = parseTrafficQuery({});

  it("leaves defaults out of the URL", () => {
    expect(trafficHref(base)).toBe("/behavior/traffic");
    expect(trafficHref(base, { window: "7d", limit: 25, offset: 25 })).toBe(
      "/behavior/traffic?window=7d&limit=25&offset=25",
    );
  });

  it("round-trips through the parser", () => {
    const q = parseTrafficQuery({ view: "ips", window: "5m", sort: "peak", order: "asc", limit: "100", offset: "200", source: "s-1" });
    const href = trafficHref(q);
    const params = Object.fromEntries(new URL(href, "http://x").searchParams);
    expect(parseTrafficQuery(params)).toEqual(q);
  });

  it("sorting a new column starts at its default order and returns to page one", () => {
    const q = parseTrafficQuery({ offset: "20" });
    expect(sortHref(q, "host")).toBe("/behavior/traffic?sort=host");
    expect(sortHref(q, "peak")).toBe("/behavior/traffic?sort=peak");
  });

  it("sorting the current column flips its order", () => {
    expect(sortHref(base, "rate")).toBe("/behavior/traffic?order=asc");
    const asc = parseTrafficQuery({ sort: "host" });
    expect(sortHref(asc, "host")).toBe("/behavior/traffic?sort=host&order=desc");
  });

  it("names the sorted column in aria-sort", () => {
    expect(ariaSort(base, "rate")).toBe("descending");
    expect(ariaSort(base, "peak")).toBe("none");
    expect(ariaSort(parseTrafficQuery({ sort: "host" }), "host")).toBe("ascending");
  });

  it("hands Pagination every parameter but the offset", () => {
    const q = parseTrafficQuery({ view: "ips", window: "24h", limit: "25", offset: "25", source: "s" });
    expect(paginationParams(q)).toEqual({
      view: "ips",
      source: "s",
      window: "24h",
      sort: undefined,
      order: undefined,
      limit: "25",
    });
  });
});

describe("figures", () => {
  it("formats a rate by its magnitude and never turns a missing one into 0", () => {
    expect(formatRate(41.23)).toBe("41.2");
    expect(formatRate(1204.4)).toBe("1,204");
    expect(formatRate(0.0833)).toBe("0.083");
    expect(formatRate(0)).toBe("0");
    expect(formatRate(null)).toBe("—");
    expect(formatRate(undefined)).toBe("—");
  });

  it("computes the unattributed share only from known figures", () => {
    expect(unattributedShare(25, 1000)).toBeCloseTo(0.025);
    expect(unattributedShare(null, 1000)).toBeNull();
    expect(unattributedShare(25, null)).toBeNull();
    expect(unattributedShare(0, 0)).toBeNull();
  });
});

describe("All rows", () => {
  it("reads limit=all and limit=1000 as the largest page, with no offset", () => {
    expect(parseTrafficQuery({ limit: "all", offset: "40" })).toMatchObject({ limit: 1000, offset: 0 });
    expect(parseTrafficQuery({ limit: "1000" }).limit).toBe(1000);
    expect(parseTrafficQuery({ limit: "999" }).limit).toBe(10);
  });

  it("writes it as limit=all and keeps it through a sort link", () => {
    const q = parseTrafficQuery({ limit: "all" });
    expect(trafficHref(q)).toBe("/behavior/traffic?limit=all");
    expect(sortHref(q, "host")).toBe("/behavior/traffic?sort=host&limit=all");
    expect(paginationParams(q).limit).toBe("all");
  });
});
