import { describe, expect, it } from "vitest";

import { ipsDetail, ipsDetection, ipsListItem, ipsSourceDetail } from "./fixtures";
import { detectorSpec, expectsNone, ipsFigures } from "./ipsDetectors";
import { formatFigure, unitText } from "./ipsFormat";

describe("each detector's unit and figures", () => {
  it.each(["IPS_PORT_RATE_SPIKE", "IPS_DESTINATION_RATE_SPIKE", "IPS_SIGNATURE_BURST"])(
    "%s counts events per bucket",
    (type) => {
      const f = ipsFigures(ipsDetail({ anomaly_type: type }), ipsDetection());
      expect(f).toMatchObject({ unit: "events", observed: 940, expected: 120 });
      expect(detectorSpec(type)?.scope).toBe("entity");
    },
  );

  it("counts distinct destination ports for fan-out, from the ports fields", () => {
    const d = ipsDetection({
      observed_events: null,
      expected_events: null,
      observed_distinct_ports: 87,
      expected_distinct_ports: 9,
    });
    const f = ipsFigures(ipsSourceDetail({ anomaly_type: "IPS_PORT_FANOUT_SPIKE" }), d);
    expect(f).toMatchObject({ unit: "ports", observed: 87, expected: 9 });
  });

  it.each(["IPS_SOURCE_CONCENTRATION_SPIKE", "IPS_BLOCK_RATIO_SHIFT"])(
    "%s is a share with its numerator and denominator",
    (type) => {
      const a = ipsSourceDetail({ anomaly_type: type });
      const f = ipsFigures(a, a.detection);
      expect(f).toMatchObject({
        unit: "share",
        observed: 0.62,
        expected: 0.18,
        numerator: 620,
        denominator: 1000,
      });
    },
  );

  it("expects none for the new-port detector, whatever the block says", () => {
    const d = ipsDetection({ expected_events: 5 });
    const f = ipsFigures(ipsDetail({ anomaly_type: "IPS_NEW_DESTINATION_PORT" }), d);
    expect(f.unit).toBe("since");
    expect(f.expected).toBeNull();
    expect(expectsNone("IPS_NEW_DESTINATION_PORT")).toBe(true);
    expect(expectsNone("IPS_PORT_RATE_SPIKE")).toBe(false);
    expect(detectorSpec("IPS_NEW_DESTINATION_PORT")?.scope).toBe("entity");
  });

  it("keeps a missing value null, never 0, and does not fall back to the row when a block exists", () => {
    const d = ipsDetection({ observed_events: null, expected_events: null });
    const f = ipsFigures(ipsDetail(), d);
    expect(f.observed).toBeNull();
    expect(f.expected).toBeNull();
  });

  it("reads the row's own values only when no detection was recorded", () => {
    const f = ipsFigures(ipsListItem(), null);
    expect(f).toMatchObject({ unit: "events", observed: 940, expected: 120 });
  });

  it("gives an unknown type no unit and no figures, never events", () => {
    const f = ipsFigures(ipsListItem({ anomaly_type: "IPS_FUTURE" }), null);
    expect(f.unit).toBeNull();
    expect(f.observed).toBeNull();
    expect(detectorSpec("IPS_FUTURE")).toBeNull();
    expect(formatFigure(null, 5)).toBe("—");
  });
});

describe("units in words", () => {
  it("never says EPS", () => {
    for (const u of ["events", "ports", "share"] as const) {
      expect(unitText(u, "5 min")).not.toMatch(/EPS/i);
    }
    expect(unitText("events", "5 min")).toBe("events / 5 min");
    expect(unitText("events", null)).toBe("events / bucket");
    expect(unitText("ports", null)).toBe("distinct ports");
    // The new-port count is cumulative: it never takes the per-bucket unit.
    expect(unitText("since", "5 min")).toBe("events since it first appeared");
  });
});
