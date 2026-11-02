import { describe, expect, it } from "vitest";

import { answer, countries, MIXED } from "./fixtures";
import {
  classOf,
  countryName,
  mapData,
  parseDirection,
  RAMP_TOKENS,
  roundDown,
  rowsOf,
  scaleClasses,
  undrawn,
} from "./geoModel";

describe("roundDown", () => {
  it.each([
    [1, 1],
    [4, 2],
    [5, 5],
    [19, 10],
    [20, 20],
    [499, 200],
    [500, 500],
    [1_234_567, 1_000_000],
    [7_100, 5_000],
  ])("%s -> %s", (value, expected) => {
    expect(roundDown(value)).toBe(expected);
  });

  it("never returns less than 1, so a class never starts at zero", () => {
    expect(roundDown(0)).toBe(1);
    expect(roundDown(0.4)).toBe(1);
    expect(roundDown(Number.NaN)).toBe(1);
  });
});

describe("scaleClasses", () => {
  it("has no scale for no counts, and ignores zero and negative ones", () => {
    expect(scaleClasses([])).toEqual([]);
    expect(scaleClasses([0, -5, Number.NaN])).toEqual([]);
  });

  it("puts round bounds on a log axis between the smallest and the largest count", () => {
    const classes = scaleClasses([1_000_000, 120_000, 9_000, 800, 50]);
    expect(classes.map((c) => c.from)).toEqual([50, 500, 5_000, 50_000]);
    expect(classes.map((c) => c.to)).toEqual([500, 5_000, 50_000, null]);
    expect(classes.map((c) => c.ramp)).toEqual([0, 1, 2, 3]);
  });

  it("covers every count, and a larger count is never in a lower class", () => {
    const counts = MIXED.map(([, events]) => events);
    const classes = scaleClasses(counts);
    let last = -1;
    for (const n of [...counts].sort((a, b) => a - b)) {
      const klass = classOf(n, classes);
      expect(klass, String(n)).not.toBeNull();
      expect(klass!.ramp).toBeGreaterThanOrEqual(last);
      last = klass!.ramp;
    }
  });

  it("drops a bound that collapses, so a narrow spread has fewer classes, not duplicate ones", () => {
    const classes = scaleClasses([100, 120, 140]);
    expect(new Set(classes.map((c) => c.from)).size).toBe(classes.length);
    expect(classes.length).toBeLessThan(4);
  });

  it("draws one class mid-ramp, because a single class orders nothing", () => {
    for (const counts of [[7], [300, 300, 300]]) {
      const classes = scaleClasses(counts);
      expect(classes).toHaveLength(1);
      expect(classes[0].ramp).toBe(2);
      expect(classes[0].to).toBeNull();
    }
  });

  it("uses the brightest fill only for the top class when there are two", () => {
    const classes = scaleClasses([10, 60]);
    expect(classes.map((c) => c.ramp)).toEqual([0, 3]);
  });
});

describe("classOf", () => {
  const classes = scaleClasses([50, 1_000_000]);

  it("is null for a count that is not positive: no measurement is never the lowest class", () => {
    expect(classOf(0, classes)).toBeNull();
    expect(classOf(-1, classes)).toBeNull();
    expect(classOf(Number.NaN, classes)).toBeNull();
    expect(classOf(5, [])).toBeNull();
  });

  it("puts a bound in the class that starts there", () => {
    const [first, second] = classes;
    expect(classOf(second.from, classes)).toBe(second);
    expect(classOf(second.from - 1, classes)).toBe(first);
  });

  it("puts a count below the first start in the first class", () => {
    expect(classOf(1, classes)).toBe(classes[0]);
  });
});

describe("mapData", () => {
  const drawable = new Set(MIXED.map(([code]) => code).filter((code) => code !== "SG"));

  it("gives a datum to a country with a row, and none to a country without one", () => {
    const data = mapData(answer(), drawable);
    const names = data.map((d) => d.name);
    expect(names).toContain("US");
    // FR is in the answer, but ZW is not: it must stay out of the data so the no-data fill draws it.
    expect(names).not.toContain("ZW");
    expect(data).toHaveLength(MIXED.length - 1);
  });

  it("leaves a country the outlines cannot draw out of the data", () => {
    expect(mapData(answer(), drawable).map((d) => d.name)).not.toContain("SG");
  });

  it("never gives a datum a ramp position below the first class or above the last", () => {
    for (const d of mapData(answer(), drawable)) {
      expect(d.ramp).toBeGreaterThanOrEqual(0);
      expect(d.ramp).toBeLessThan(RAMP_TOKENS.length);
    }
  });

  it("draws the largest country in the brightest class and the smallest in the dimmest", () => {
    const data = mapData(answer(), drawable);
    expect(data.find((d) => d.name === "US")!.ramp).toBe(3);
    expect(data.find((d) => d.name === "IS")!.ramp).toBe(0);
  });

  it("is empty for an answer without countries", () => {
    expect(mapData(answer({ countries: [] }), drawable)).toEqual([]);
  });
});

describe("undrawn", () => {
  it("lists the countries of the answer that have no polygon", () => {
    const list = undrawn(countries([["US", 5], ["SG", 3], ["MT", 1]]), new Set(["US"]));
    expect(list.map((c) => c.country_code)).toEqual(["SG", "MT"]);
  });

  it("is empty when every country can be drawn", () => {
    expect(undrawn(countries([["US", 5]]), new Set(["US"]))).toEqual([]);
  });
});

describe("countryName", () => {
  it("names a country in the interface locale", () => {
    expect(countryName("DE", "en")).toBe("Germany");
    expect(countryName("DE", "tr")).toBe("Almanya");
    expect(countryName("US", "tr")).toBe("Amerika Birleşik Devletleri");
  });

  it("falls back to the code for a code the runtime cannot name", () => {
    expect(countryName("ZZZ", "en")).toBe("ZZZ");
    expect(countryName("", "en")).toBe("");
  });
});

describe("rowsOf", () => {
  it("keeps the API's order and names, and carries the share as given", () => {
    const rows = rowsOf(answer(), "en");
    expect(rows.map((r) => r.code)).toEqual(MIXED.map(([code]) => code));
    expect(rows[0]).toMatchObject({ code: "US", name: "United States", events: 1_240_000 });
    expect(rows.reduce((sum, r) => sum + r.share, 0)).toBeCloseTo(1, 9);
  });
});

describe("parseDirection", () => {
  it("accepts the two directions and falls back to the source address", () => {
    expect(parseDirection("SOURCE")).toBe("SOURCE");
    expect(parseDirection("DESTINATION")).toBe("DESTINATION");
    expect(parseDirection("destination")).toBe("SOURCE");
    expect(parseDirection(undefined)).toBe("SOURCE");
    expect(parseDirection("1; DROP TABLE")).toBe("SOURCE");
  });
});
