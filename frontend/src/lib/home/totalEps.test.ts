import { describe, expect, it } from "vitest";

import { fleet, fleetClass, noData } from "./fixtures";
import { totalEps } from "./totalEps";

describe("totalEps", () => {
  it("sums the class means and names the top class and its share", () => {
    const r = totalEps(
      fleet([
        fleetClass("FIREWALL", { mean_eps: 30 }),
        fleetClass("LINUX", { mean_eps: 10 }),
      ]),
    );
    expect(r.meanEps).toBe(40);
    expect(r.classesMeasured).toBe(2);
    expect(r.topClass).toEqual({ sourceClass: "FIREWALL", label: "Firewall", eps: 30, share: 0.75 });
    expect(r.qualifiers).toEqual([]);
  });

  it("leaves a null mean out of the sum and counts it, never adding zero", () => {
    const r = totalEps(fleet([fleetClass("FIREWALL", { mean_eps: 30 }), noData("DNS")]));
    expect(r.meanEps).toBe(30);
    expect(r.classesMeasured).toBe(1);
    expect(r.classesWithoutData).toBe(1);
  });

  it("does not count a class with no sources as missing data", () => {
    const empty = fleetClass("VPN", {
      sources: { enabled: 0, disabled: 0 },
      mean_eps: null,
      events: null,
      buckets: { expected: 0, complete: 0, complete_from_history: 0, partial: 0, missing: 0 },
    });
    expect(totalEps(fleet([fleetClass("DNS", { mean_eps: 1 }), empty])).classesWithoutData).toBe(0);
  });

  it("is INSUFFICIENT_DATA with a null value when every mean is null", () => {
    const r = totalEps(fleet([noData("DNS"), noData("VPN")]));
    expect(r.meanEps).toBeNull();
    expect(r.topClass).toBeNull();
    expect(r.qualifiers.map((q) => q.kind)).toContain("INSUFFICIENT_DATA");
  });

  it("is INSUFFICIENT_DATA for an empty class list", () => {
    const r = totalEps(fleet([]));
    expect(r.meanEps).toBeNull();
    expect(r.qualifiers.map((q) => q.kind)).toEqual(["INSUFFICIENT_DATA"]);
  });

  it("keeps a measured zero as zero, with no share of nothing", () => {
    const r = totalEps(fleet([fleetClass("DNS", { mean_eps: 0 })]));
    expect(r.meanEps).toBe(0);
    expect(r.topClass?.share).toBeNull();
  });

  it("breaks a tie on the class label", () => {
    const r = totalEps(
      fleet([fleetClass("LINUX", { mean_eps: 5 }), fleetClass("DNS", { mean_eps: 5 })]),
    );
    expect(r.topClass?.sourceClass).toBe("DNS");
  });

  it("is PARTIAL with the interval counts when buckets are partial or missing", () => {
    const r = totalEps(
      fleet([
        fleetClass("DNS", {
          buckets: {
            expected: 48,
            complete: 40,
            complete_from_history: 0,
            partial: 5,
            missing: 3,
          },
        }),
        fleetClass("LINUX", {
          buckets: {
            expected: 48,
            complete: 48,
            complete_from_history: 0,
            partial: 0,
            missing: 0,
          },
        }),
      ]),
    );
    const q = r.qualifiers.find((x) => x.kind === "PARTIAL");
    expect(q).toMatchObject({ have: 88, of: 96, text: "88 of 96 intervals collected" });
  });

  describe("expected", () => {
    it("is null with no coverage and no percentage when no class has a baseline", () => {
      const r = totalEps(fleet([fleetClass("DNS", { mean_eps: 10 })]));
      expect(r.expectedEps).toBeNull();
      expect(r.expectedCoverage).toBeNull();
      expect(r.vsExpectedPct).toBeNull();
    });

    it("sums only baselined classes and writes the coverage out", () => {
      const r = totalEps(
        fleet([
          fleetClass("DNS", {
            mean_eps: 12,
            expected_eps: 10,
            baseline_sources: 2,
            monitored_sources: 2,
          }),
          fleetClass("LINUX", { mean_eps: 50, monitored_sources: 3 }),
        ]),
      );
      expect(r.expectedEps).toBe(10);
      expect(r.expectedCoverage).toMatchObject({
        baselinedSources: 2,
        monitoredSources: 5,
        partialFleet: true,
        text: "baselines cover 2 of 5 sources",
      });
      // Like for like: the DNS mean against the DNS expectation, not the fleet mean.
      expect(r.comparableMeanEps).toBe(12);
      expect(r.vsExpectedPct).toBeCloseTo(20);
    });

    it("is not partial when every monitored source has a baseline", () => {
      const r = totalEps(
        fleet([
          fleetClass("DNS", { expected_eps: 10, baseline_sources: 2, monitored_sources: 2 }),
        ]),
      );
      expect(r.expectedCoverage?.partialFleet).toBe(false);
    });

    it("gives no percentage when expected is zero", () => {
      const r = totalEps(
        fleet([fleetClass("DNS", { expected_eps: 0, baseline_sources: 1, monitored_sources: 1 })]),
      );
      expect(r.expectedEps).toBe(0);
      expect(r.vsExpectedPct).toBeNull();
    });

    it("gives no percentage when the baselined classes have no mean", () => {
      const r = totalEps(
        fleet([
          { ...noData("DNS"), expected_eps: 4, baseline_sources: 1 },
          fleetClass("LINUX", { mean_eps: 9 }),
        ]),
      );
      expect(r.expectedEps).toBe(4);
      expect(r.comparableMeanEps).toBeNull();
      expect(r.vsExpectedPct).toBeNull();
    });
  });
});
