import { describe, expect, it } from "vitest";

import { formatPointChange } from "./ipsFormat";

describe("formatPointChange", () => {
  it("joins the number and its unit with U+00A0", () => {
    expect(formatPointChange(44)).toBe("+44.0 pp");
    expect(formatPointChange(-2.5)).toBe("-2.5 pp");
  });

  it("is an em dash when the change is missing, never 0.0 pp", () => {
    expect(formatPointChange(null)).toBe("—");
  });
});
