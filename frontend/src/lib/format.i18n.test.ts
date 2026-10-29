import { describe, expect, it } from "vitest";

import { formatCount, formatPercent, formatShare } from "./format";

describe("localized number formats", () => {
  it("keeps the established English forms", () => {
    expect(formatCount(1204)).toBe("1,204");
    expect(formatShare(0.318)).toBe("31.8\u00A0%");
    expect(formatShare(0.0009)).toBe("<0.1\u00A0%");
    expect(formatPercent(0.125, "LOWER_BOUND")).toBe("≥ 12.5\u00A0%");
  });

  it("uses Turkish grouping, decimal and percent placement", () => {
    expect(formatCount(1204, "tr")).toBe("1.204");
    expect(formatCount(-1204, "tr")).toBe("-1.204");
    expect(formatShare(0.318, "tr")).toBe("%31,8");
    expect(formatShare(0.0009, "tr")).toBe("<%0,1");
    expect(formatPercent(0.125, "LOWER_BOUND", 1, "tr")).toBe("≥ %12,5");
  });

  it("pins zero, requested precision and missing values", () => {
    expect(formatShare(0, "tr")).toBe("%0,0");
    expect(formatPercent(-0.318, null, 0, "tr")).toBe("-%32");
    expect(formatPercent(0.318, null, 2, "tr")).toBe("%31,80");
    expect(formatCount(null, "tr")).toBe("—");
    expect(formatPercent(Number.POSITIVE_INFINITY, null, 2, "tr")).toBe("—");
  });
});
