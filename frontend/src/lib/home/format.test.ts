import { describe, expect, it } from "vitest";

import { signedPercent } from "./format";

describe("signedPercent", () => {
  it("uses a true minus for a negative value", () => {
    expect(signedPercent(-3.94)).toBe("−3.9 %");
    expect(signedPercent(-3.94)).not.toContain("-");
  });
  it("signs a positive value and leaves zero bare", () => {
    expect(signedPercent(3.2)).toBe("+3.2 %");
    expect(signedPercent(0)).toBe("0.0 %");
  });
});
