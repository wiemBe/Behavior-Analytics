import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { answer, MIXED, partialLowerBound } from "./fixtures";
import { buildMapOption, MAP_TOKENS, rampColors, readMapPalette, type MapPalette } from "./mapOption";

const tokensCss = readFileSync(resolve(__dirname, "../../app/tokens.css"), "utf8");
const declared = (name: string) =>
  tokensCss.match(new RegExp(`^\\s*${name}:\\s*([^;]+);`, "m"))?.[1]?.trim() ?? "";

// Stand-ins, each different, so a fill that takes the wrong one fails by name.
const palette: MapPalette = {
  noData: "#no-data",
  ramp0: "#ramp-0",
  ramp1: "#ramp-1",
  ramp2: "#ramp-2",
  ramp3: "#ramp-3",
  border: "#border",
  rim: "#rim",
  tooltipBg: "#tbg",
  tooltipBorder: "#tborder",
  tooltipText: "#ttext",
  font: "Inter",
};
const text = {
  noData: "No data",
  events: (count: number, atLeast: boolean) => `${atLeast ? "At least " : ""}${count} events`,
  share: (share: number) => `${(share * 100).toFixed(1)} % of the events with a country`,
  name: (code: string) => `name-of-${code}`,
};
const drawable = new Set(MIXED.map(([code]) => code).filter((code) => code !== "SG"));

type Series = {
  itemStyle: { areaColor: string };
  emphasis: { itemStyle: { areaColor: string; borderColor: string } };
  data: Array<{ name: string; value: number; itemStyle: { areaColor: string } }>;
  nameProperty: string;
};
const build = (data = answer(), reduceMotion = false) =>
  buildMapOption({ data, drawable, palette, text, reduceMotion });
const series = (option: ReturnType<typeof build>) => (option.series as Series[])[0];
const formatter = (option: ReturnType<typeof build>) =>
  (option.tooltip as { formatter: (p: unknown) => string }).formatter;

describe("the map tokens", () => {
  it("names only tokens that tokens.css declares", () => {
    for (const name of Object.values(MAP_TOKENS)) {
      expect(tokensCss, `${name} is not declared in tokens.css`).toMatch(
        new RegExp(`^\\s*${name}:`, "m"),
      );
    }
  });

  it("keeps the no-data fill apart from every stop of the ramp", () => {
    // A country with no row must not look like the lowest class: that would draw a zero where
    // nothing was measured.
    const noData = declared(MAP_TOKENS.noData);
    expect(noData).not.toBe("");
    for (const token of [MAP_TOKENS.ramp0, MAP_TOKENS.ramp1, MAP_TOKENS.ramp2, MAP_TOKENS.ramp3]) {
      expect(declared(token), token).not.toBe("");
      expect(declared(token), token).not.toBe(noData);
    }
  });

  it("uses no status hue for the ramp", () => {
    for (const token of [MAP_TOKENS.ramp0, MAP_TOKENS.ramp1, MAP_TOKENS.ramp2, MAP_TOKENS.ramp3]) {
      expect(declared(token)).not.toMatch(/--ok|--warn|--crit|--sev-/);
    }
  });

  it("reads each token from the computed style of the root", () => {
    const getPropertyValue = vi.fn((name: string) => ` value-of(${name}) `);
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue,
    } as unknown as CSSStyleDeclaration);
    const read = readMapPalette();
    expect(read.noData).toBe("value-of(--map-0)");
    expect(read.ramp3).toBe("value-of(--map-4)");
    expect(getPropertyValue).toHaveBeenCalledTimes(Object.keys(MAP_TOKENS).length);
  });
});

describe("buildMapOption", () => {
  it("draws every polygon without a datum in the no-data fill", () => {
    const s = series(build());
    expect(s.itemStyle.areaColor).toBe(palette.noData);
    expect(s.itemStyle.areaColor).not.toBe(rampColors(palette)[0]);
  });

  it("keeps the no-data fill when the pointer is over a country with no row", () => {
    const s = series(build());
    expect(s.emphasis.itemStyle.areaColor).toBe(palette.noData);
  });

  it("matches the polygons by ISO code, not by the geometry file's name", () => {
    expect(series(build()).nameProperty).toBe("iso_a2");
  });

  it("fills a country with a row by its class, never in the no-data fill", () => {
    const s = series(build());
    const us = s.data.find((d) => d.name === "US")!;
    const is = s.data.find((d) => d.name === "IS")!;
    expect(us.itemStyle.areaColor).toBe(palette.ramp3);
    expect(is.itemStyle.areaColor).toBe(palette.ramp0);
    for (const d of s.data) expect(d.itemStyle.areaColor).not.toBe(palette.noData);
  });

  it("holds the fill of a country on hover and only adds a rim", () => {
    const s = series(build());
    for (const d of s.data as Array<(typeof s.data)[number] & { emphasis: Series["emphasis"] }>) {
      expect(d.emphasis.itemStyle.areaColor).toBe(d.itemStyle.areaColor);
      expect(d.emphasis.itemStyle.borderColor).toBe(palette.rim);
    }
  });

  it("gives no datum to a country that has no row, or no polygon", () => {
    const names = series(build()).data.map((d) => d.name);
    expect(names).not.toContain("ZW");
    expect(names).not.toContain("SG");
  });

  it("uses no visual map: the legend is HTML beside the map", () => {
    expect((build() as { visualMap?: unknown }).visualMap).toBeUndefined();
  });

  it("switches animation off for a reader who asked for reduced motion", () => {
    expect(build(answer(), true).animation).toBe(false);
    expect(build(answer(), false).animation).toBe(true);
  });

  describe("the tooltip", () => {
    it("names the country, its events and its share", () => {
      const html = formatter(build())({ name: "DE" });
      expect(html).toContain("<strong>name-of-DE</strong>");
      expect(html).toContain("402300 events");
      expect(html).not.toContain("At least");
      expect(html).toContain("% of the events with a country");
    });

    it("says at least when the measurement is a lower bound", () => {
      const html = formatter(build(partialLowerBound()))({ name: "DE" });
      expect(html).toContain("At least 402300 events");
    });

    it("says no data for a country with no row, with no count and no share", () => {
      const html = formatter(build())({ name: "ZW" });
      expect(html).toContain("name-of-ZW");
      expect(html).toContain("No data");
      expect(html).not.toMatch(/events|%/);
    });

    it("escapes a name before it reaches the HTML", () => {
      const html = buildMapOption({
        data: answer(),
        drawable,
        palette,
        text: { ...text, name: () => "<img src=x onerror=alert(1)>" },
        reduceMotion: false,
      });
      expect((html.tooltip as { formatter: (p: unknown) => string }).formatter({ name: "DE" })).not.toContain(
        "<img",
      );
    });

    it("is empty for a hover with no name", () => {
      expect(formatter(build())({})).toBe("");
    });
  });
});
