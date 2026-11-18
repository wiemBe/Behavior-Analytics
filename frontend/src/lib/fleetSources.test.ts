import { describe, expect, it } from "vitest";

import { fleetSourcesCsvUrl } from "@/lib/api";
import {
  changed,
  clearedFilters,
  filterCount,
  parseSourceListQuery,
  type RawQuery,
  sortedBy,
  toApiParams,
  toSearch,
} from "@/lib/fleetSources";

describe("the URL is the view", () => {
  it("round-trips search, facets, sort, page size and offset", () => {
    const q = parseSourceListQuery({
      q: " fw ",
      class: ["WAF", "IPS"],
      type_id: ["12", "7"],
      group: ["3", "9"],
      flow: "SILENT",
      eps: ["BELOW_EXPECTED"],
      profile: ["WAF"],
      sort: "eps",
      order: "desc",
      limit: "100",
      offset: "200",
    });
    expect(q.q).toBe("fw");
    expect(q.class).toEqual(["WAF", "IPS"]);
    expect(q.type_id).toEqual([12, 7]);
    expect(q.group).toEqual([3, 9]);
    expect(q.limit).toBe(100);
    const raw: RawQuery = {};
    for (const [k, v] of new URLSearchParams(toSearch(q))) {
      const prev = raw[k];
      raw[k] = prev === undefined ? v : [...(Array.isArray(prev) ? prev : [prev]), v];
    }
    expect(parseSourceListQuery(raw)).toEqual(q);
    const again = new URLSearchParams(toSearch(q));
    expect(again.getAll("class")).toEqual(["WAF", "IPS"]);
    expect(again.get("offset")).toBe("200");
  });

  it("drops values that are not on the allow-lists", () => {
    const q = parseSourceListQuery({
      class: ["NOPE"],
      type_id: ["x", "-1"],
      flow: "bad",
      sort: "drop table",
      limit: "9999",
      offset: "-5",
    });
    expect(q.class).toEqual([]);
    expect(q.type_id).toEqual([]);
    expect(q.flow).toEqual([]);
    expect(q.sort).toBe("name");
    expect(q.limit).toBe(50);
    expect(q.offset).toBe(0);
    expect(toSearch(q)).toBe("");
  });

  it("starts a changed view on page one and flips a sorted column", () => {
    const q = parseSourceListQuery({ offset: "100" });
    expect(changed(q, { q: "a" }).offset).toBe(0);
    expect(sortedBy(q, "type")).toMatchObject({ sort: "type", order: "asc", offset: 0 });
    expect(sortedBy(sortedBy(q, "type"), "type").order).toBe("desc");
  });

  it("counts and clears the filters but keeps sort and page size", () => {
    const q = parseSourceListQuery({ q: "x", class: "WAF", flow: ["SILENT"], sort: "eps", limit: "25" });
    expect(filterCount(q)).toBe(3);
    const c = clearedFilters(q);
    expect(filterCount(c)).toBe(0);
    expect(c.sort).toBe("eps");
    expect(c.limit).toBe(25);
  });

  it("builds the CSV link from the filters and sort, without paging", () => {
    const q = parseSourceListQuery({ q: "fw", class: ["WAF", "IPS"], sort: "eps", order: "desc", offset: "50" });
    const url = fleetSourcesCsvUrl(toApiParams(q));
    expect(url).toContain("/fleet/sources.csv?");
    const params = new URL(url, "http://x").searchParams;
    expect(params.get("q")).toBe("fw");
    expect(params.getAll("class")).toEqual(["WAF", "IPS"]);
    expect(params.get("sort")).toBe("eps");
    expect(params.has("offset")).toBe(false);
    expect(params.has("limit")).toBe(false);
  });
});
