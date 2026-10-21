import { describe, expect, it } from "vitest";

import { fleet, fleetClass, behavior } from "./fixtures";
import { deviationGapText, epsShares, topSources } from "./topSources";

const ts = (id: string, events: number, name = id) => ({ id, name, events });

describe("topSources", () => {
  it("takes the global top five from the union of each class's list", () => {
    const rows = topSources(
      fleet([
        fleetClass("FIREWALL", { top_sources: [ts("a", 900), ts("b", 500), ts("c", 100)] }),
        fleetClass("DNS", { top_sources: [ts("d", 800), ts("e", 700), ts("f", 600), ts("g", 50)] }),
      ]),
      [],
    );
    expect(rows.map((r) => r.id)).toEqual(["a", "d", "e", "f", "b"]);
  });

  it("computes EPS as events over the window's seconds", () => {
    const rows = topSources(
      fleet([fleetClass("DNS", { top_sources: [ts("a", 86_400)] })]),
      [],
    );
    expect(rows[0].eps).toBe(1);
    expect(rows[0].epsIsLowerBound).toBe(false);
  });

  it("marks EPS as a lower bound when the class window is incomplete", () => {
    const rows = topSources(
      fleet([
        fleetClass("DNS", {
          buckets: {
            expected: 48,
            complete: 40,
            complete_from_history: 0,
            partial: 8,
            missing: 0,
          },
          top_sources: [ts("a", 100)],
        }),
      ]),
      [],
    );
    expect(rows[0].epsIsLowerBound).toBe(true);
  });

  it("gives no EPS rather than dividing by an unusable window", () => {
    const rows = topSources(
      fleet([fleetClass("DNS", { top_sources: [ts("a", 100)] })], {
        window_start: "2026-10-14T14:00:00Z",
        window_end: "2026-10-14T14:00:00Z",
      }),
      [],
    );
    expect(rows[0].eps).toBeNull();
  });

  it("breaks a tie on events by name, then id, and lists a source once", () => {
    const rows = topSources(
      fleet([
        fleetClass("DNS", { top_sources: [ts("z", 10, "same"), ts("y", 10, "same"), ts("a", 10, "alpha")] }),
        fleetClass("VPN", { top_sources: [ts("a", 10, "alpha")] }),
      ]),
      [],
    );
    expect(rows.map((r) => r.id)).toEqual(["a", "y", "z"]);
  });

  it("returns fewer than five when fewer exist", () => {
    expect(topSources(fleet([fleetClass("DNS", { top_sources: [ts("a", 1)] })]), [])).toHaveLength(1);
    expect(topSources(fleet([fleetClass("DNS")]), [])).toEqual([]);
  });

  describe("deviation and state", () => {
    const f = fleet([fleetClass("DNS", { top_sources: [ts("a", 100)] })]);

    it("joins the deviation and the state word from the behavior list", () => {
      const [r] = topSources(f, [
        behavior({ log_source_id: "a", deviation_ratio: 1.8, state: "OPEN" }),
      ]);
      expect(r).toMatchObject({ deviation: 1.8, deviationGap: null, state: "OPEN" });
      expect(r.deviationBasis).toBe("latest bucket");
    });

    it("keeps a measured deviation of zero as zero", () => {
      const [r] = topSources(f, [behavior({ log_source_id: "a", deviation_ratio: 0 })]);
      expect(r.deviation).toBe(0);
    });

    it("is null, never 0, with no expected value", () => {
      const [r] = topSources(f, [
        behavior({ log_source_id: "a", expected_eps: null, deviation_ratio: 1.5 }),
      ]);
      expect(r.deviation).toBeNull();
      expect(r.deviationGap).toBe("no_expectation");
    });

    it("is null when the ratio is null even with an expected value", () => {
      const [r] = topSources(f, [
        behavior({ log_source_id: "a", expected_eps: 0, deviation_ratio: null }),
      ]);
      expect(r.deviation).toBeNull();
    });

    it("is null when the latest bucket is not COMPLETE, or its completeness is absent", () => {
      for (const c of ["PARTIAL", "MISSING", null, undefined] as const) {
        const [r] = topSources(f, [behavior({ log_source_id: "a", last_bucket_completeness: c })]);
        expect(r.deviation).toBeNull();
        expect(r.deviationGap).toBe("bucket_not_complete");
        expect(r.state).toBe("NORMAL");
      }
    });

    it("is null with no state when the source is not in the list", () => {
      const [r] = topSources(f, [behavior({ log_source_id: "other" })]);
      expect(r).toMatchObject({ deviation: null, deviationGap: "not_in_list", state: null });
    });

    it("is null when the behavior list is unavailable", () => {
      const [r] = topSources(f, null);
      expect(r).toMatchObject({ deviation: null, deviationGap: "no_behavior_list", state: null });
      expect(deviationGapText("no_behavior_list")).toBe("Source states unavailable");
    });
  });
});

describe("epsShares", () => {
  const row = (eps: number | null) =>
    ({ id: "x", name: "x", eps }) as unknown as import("./topSources").TopSourceRow;

  it("scales to the largest EPS and leaves an unmeasured row without a share", () => {
    expect(epsShares([row(10), row(5), row(null)])).toEqual([1, 0.5, null]);
  });

  it("gives no share to anyone when nothing is above zero", () => {
    expect(epsShares([row(0), row(null)])).toEqual([null, null]);
  });
});
