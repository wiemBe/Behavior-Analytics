import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { CHART_TOKENS, readChartPalette } from "./chartPalette";

const tokensCss = readFileSync(resolve(__dirname, "../app/tokens.css"), "utf8");

describe("chart palette", () => {
  it("only names tokens that tokens.css declares", () => {
    // A rename in tokens.css would otherwise leave the chart silently drawing
    // in the library default colours.
    for (const name of Object.values(CHART_TOKENS)) {
      expect(tokensCss, `${name} is not declared in tokens.css`).toMatch(
        new RegExp(`^\\s*${name}:`, "m"),
      );
    }
  });

  it("does not colour the expected range with a status hue", () => {
    // Green, amber and red are status colours (tokens.css, rule 1). The
    // expected range is not a status.
    const decl = (name: string) =>
      tokensCss.match(new RegExp(`^\\s*${name}:\\s*([^;]+);`, "m"))?.[1] ?? "";
    for (const name of [CHART_TOKENS.expected, CHART_TOKENS.band]) {
      expect(decl(name)).not.toMatch(/--ok|--warn|--crit/);
    }
  });

  it("reads each token from the computed style of the root", () => {
    const getPropertyValue = vi.fn((name: string) => ` value-of(${name}) `);
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue,
    } as unknown as CSSStyleDeclaration);

    const palette = readChartPalette();

    expect(palette.observed).toBe("value-of(--chart-observed)");
    expect(palette.band).toBe("value-of(--chart-band)");
    expect(getPropertyValue).toHaveBeenCalledTimes(Object.keys(CHART_TOKENS).length);
  });

  it("returns an empty string, not a throw, for an undefined token", () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: () => "",
    } as unknown as CSSStyleDeclaration);
    expect(readChartPalette().observed).toBe("");
  });
});
