import { describe, expect, it } from "vitest";

import { epsByClass, MAX_ENTRIES, REST_KEY } from "./epsByClass";
import { fleet, fleetClass, noData } from "./fixtures";
import type { SourceClass } from "@/lib/api";

const mk = (pairs: [SourceClass, number][]) =>
  fleet(pairs.map(([c, eps]) => fleetClass(c, { mean_eps: eps })));

describe("epsByClass", () => {
  it("lists up to five classes unfolded, largest first, with shares", () => {
    const r = epsByClass(mk([["DNS", 10], ["WAF", 30], ["LINUX", 60]]));
    expect(r.entries.map((e) => [e.key, e.eps, e.share])).toEqual([
      ["LINUX", 60, 0.6],
      ["WAF", 30, 0.3],
      ["DNS", 10, 0.1],
    ]);
    expect(r.totalEps).toBe(100);
    expect(r.restText).toBeNull();
  });

  it("shows exactly five classes without folding any", () => {
    const r = epsByClass(mk([["DNS", 1], ["WAF", 2], ["LINUX", 3], ["VPN", 4], ["PROXY", 5]]));
    expect(r.entries).toHaveLength(MAX_ENTRIES);
    expect(r.entries.some((e) => e.key === REST_KEY)).toBe(false);
  });

  it("folds everything past the top four into one aggregate and names its contents", () => {
    const r = epsByClass(
      mk([["DNS", 1], ["WAF", 2], ["LINUX", 30], ["VPN", 40], ["PROXY", 50], ["EMAIL", 3]]),
    );
    expect(r.entries).toHaveLength(5);
    expect(r.entries.map((e) => e.key)).toEqual(["PROXY", "VPN", "LINUX", "EMAIL", REST_KEY]);
    const rest = r.entries[4];
    expect(rest.eps).toBe(3);
    expect(r.restContains).toEqual(["WAF", "DNS"]);
    expect(r.restText).toBe("Other classes: WAF, DNS");
    // The share is of the whole, so the five shares add to 1.
    expect(r.entries.reduce((s, e) => s + (e.share ?? 0), 0)).toBeCloseTo(1);
  });

  it("never confuses the aggregate with the real class OTHER", () => {
    const r = epsByClass(
      mk([["OTHER", 90], ["DNS", 1], ["WAF", 2], ["LINUX", 3], ["VPN", 4], ["PROXY", 5]]),
    );
    expect(r.entries[0]).toMatchObject({ key: "OTHER", label: "Other" });
    expect(r.entries[4]).toMatchObject({ key: REST_KEY, label: "Other classes" });
  });

  it("says when UNCLASSIFIED fell into the aggregate", () => {
    const r = epsByClass(
      mk([["UNCLASSIFIED", 0.5], ["DNS", 1], ["WAF", 2], ["LINUX", 3], ["VPN", 4], ["PROXY", 5]]),
    );
    expect(r.unclassifiedInRest).toBe(true);
    expect(r.restContains).toContain("Unclassified");
  });

  it("names UNCLASSIFIED as its own entry when it is in the top", () => {
    const r = epsByClass(mk([["UNCLASSIFIED", 70], ["DNS", 1]]));
    expect(r.entries[0]).toMatchObject({ key: "UNCLASSIFIED", label: "Unclassified" });
    expect(r.unclassifiedInRest).toBe(false);
  });

  it("leaves null classes out and counts them, never as zero", () => {
    const r = epsByClass(fleet([fleetClass("DNS", { mean_eps: 4 }), noData("VPN"), noData("WAF")]));
    expect(r.entries.map((e) => e.key)).toEqual(["DNS"]);
    expect(r.classesWithoutData).toBe(2);
    expect(r.totalEps).toBe(4);
  });

  it("is INSUFFICIENT_DATA with no entries when no class has a mean", () => {
    const r = epsByClass(fleet([noData("VPN")]));
    expect(r.entries).toEqual([]);
    expect(r.totalEps).toBeNull();
    expect(r.qualifiers.map((q) => q.kind)).toContain("INSUFFICIENT_DATA");
  });

  it("gives no share when every mean is a measured zero", () => {
    const r = epsByClass(mk([["DNS", 0], ["WAF", 0]]));
    expect(r.entries.every((e) => e.share === null)).toBe(true);
  });

  it("breaks ties on the label", () => {
    const r = epsByClass(mk([["WAF", 5], ["DNS", 5]]));
    expect(r.entries.map((e) => e.key)).toEqual(["DNS", "WAF"]);
  });

  it("carries a PARTIAL coverage line", () => {
    const r = epsByClass(
      fleet([fleetClass("DNS", { buckets: { expected: 48, complete: 30, partial: 10, missing: 8 } })]),
    );
    expect(r.qualifiers).toEqual([
      { kind: "PARTIAL", have: 30, of: 48, unit: "intervals", text: "30 of 48 intervals collected" },
    ]);
  });
});
