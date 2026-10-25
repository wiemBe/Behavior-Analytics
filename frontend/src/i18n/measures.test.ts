import { describe, expect, it } from "vitest";

import { createLocaleTranslator } from "./fallback";
import {
  ageText,
  durationText,
  formatEps,
  formatRatioLocale,
  formatSignedPercent,
} from "./measures";

const units = (locale: "en" | "tr") => {
  const say = createLocaleTranslator(locale);
  return (key: string, values?: Record<string, string | number>) => say(`units.${key}`, values);
};
const format = (locale: "en" | "tr") => {
  const say = createLocaleTranslator(locale);
  return (key: string, values?: Record<string, string | number>) => say(`format.${key}`, values);
};

describe("figures with their unit", () => {
  it("writes EPS to two decimals with the separators of the language", () => {
    expect(formatEps(1234.5, "en")).toBe("1,234.50");
    expect(formatEps(1234.5, "tr")).toBe("1.234,50");
    expect(formatEps(0, "tr")).toBe("0,00");
    expect(formatEps(null, "tr")).toBe("—");
    expect(formatEps(Number.NaN, "en")).toBe("—");
  });

  it("writes a ratio with a trailing x and a missing one as the dash", () => {
    expect(formatRatioLocale(1.256, "en")).toBe("1.26x");
    expect(formatRatioLocale(1.256, "tr")).toBe("1,26x");
    expect(formatRatioLocale(null, "tr")).toBe("—");
  });

  it("writes a signed percentage with a true minus; Turkish puts the sign first", () => {
    expect(formatSignedPercent(3.24, "en")).toBe("+3.2 %");
    expect(formatSignedPercent(-3.94, "en")).toBe("−3.9 %");
    expect(formatSignedPercent(3.24, "tr")).toBe("+%3,2");
    expect(formatSignedPercent(-3.94, "tr")).toBe("−%3,9");
    expect(formatSignedPercent(0, "tr")).toBe("%0,0");
  });

  it("writes a duration with every non-zero part, in the units of the language", () => {
    expect(durationText(units("en"), 3 * 3600 + 20 * 60)).toBe("3 h 20 min");
    expect(durationText(units("tr"), 3 * 3600 + 20 * 60)).toBe("3\u00a0sa 20\u00a0dk");
    expect(durationText(units("tr"), 2 * 86400 + 4 * 3600)).toBe("2\u00a0gün 4\u00a0sa");
    expect(durationText(units("tr"), 45)).toBe("45\u00a0sn");
    expect(durationText(units("tr"), 0)).toBe("0\u00a0sn");
    expect(durationText(units("tr"), null)).toBe("—");
    expect(durationText(units("tr"), -1)).toBe("—");
  });

  it("writes a relative age with the largest unit, and a missing one as the dash", () => {
    expect(ageText(format("en"), 30)).toBe("under 1 min ago");
    expect(ageText(format("tr"), 30)).toBe("1\u00a0dk'dan az önce");
    expect(ageText(format("tr"), 12 * 60)).toBe("12\u00a0dk önce");
    expect(ageText(format("tr"), 3 * 3600)).toBe("3\u00a0sa önce");
    expect(ageText(format("tr"), 2 * 86400)).toBe("2\u00a0gün önce");
    expect(ageText(format("tr"), null)).toBe("—");
  });
});
