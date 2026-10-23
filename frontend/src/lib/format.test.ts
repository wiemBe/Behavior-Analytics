import { describe, expect, it } from "vitest";

import { formatCount, formatShare } from "./format";

describe("formatCount", () => {
  it("pins en-US grouping and keeps a measured zero", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(10234)).toBe("10,234");
    expect(formatCount(1234.5)).toBe("1,234.5");
  });

  it("renders a missing count as an em dash, never 0", () => {
    expect(formatCount(null)).toBe("—");
    expect(formatCount(undefined)).toBe("—");
    expect(formatCount(Number.NaN)).toBe("—");
    expect(formatCount(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatShare", () => {
  it("uses one decimal and a space before the percent sign", () => {
    expect(formatShare(0.123)).toBe("12.3\u00A0%");
    expect(formatShare(0.68)).toBe("68.0\u00A0%");
    expect(formatShare(0.62)).toBe("62.0\u00A0%");
    expect(formatShare(1)).toBe("100.0\u00A0%");
    expect(formatShare(0)).toBe("0.0\u00A0%");
  });

  it("does not draw a tiny measured share as 0.0 %", () => {
    expect(formatShare(0.0003)).toBe("<0.1\u00A0%");
  });

  it("renders a missing share as an em dash, never 0", () => {
    expect(formatShare(null)).toBe("—");
    expect(formatShare(undefined)).toBe("—");
    expect(formatShare(Number.NaN)).toBe("—");
  });

  it("joins the number and the sign with U+00A0, so a line cannot break between them", () => {
    expect(formatShare(0.875)).toBe("87.5\u00A0%");
    expect(formatShare(0.0003)).toBe("<0.1\u00A0%");
    expect(formatShare(0.875)).not.toContain(" ");
  });
});
