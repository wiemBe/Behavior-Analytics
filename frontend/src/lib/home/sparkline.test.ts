import { describe, expect, it } from "vitest";

import { sparkline } from "./sparkline";

describe("sparkline", () => {
  it("draws one connected path when every step is measured", () => {
    const g = sparkline([1, 2, 3], 100, 10);
    expect(g.runs).toBe(1);
    expect(g.measured).toBe(3);
    expect(g.path.match(/M/g)).toHaveLength(1);
    expect(g.path).toBe("M0 6.33 L50 3.67 L100 1");
  });

  it("breaks the path at a null step and does not connect across it", () => {
    const g = sparkline([1, 2, null, 3, 4], 100, 10);
    expect(g.runs).toBe(2);
    expect(g.path.match(/M/g)).toHaveLength(2);
    expect(g.path).not.toContain("L50");
  });

  it("draws no point for a null step (not at the baseline, not anywhere)", () => {
    const g = sparkline([null, 4, null], 100, 10);
    // Only the middle step produces coordinates.
    expect(g.path.match(/-?\d+(\.\d+)? -?\d+(\.\d+)?/g)?.every((p) => p.startsWith("50 "))).toBe(
      true,
    );
    expect(g.measured).toBe(1);
  });

  it("keeps a lone measured step as its own subpath", () => {
    const g = sparkline([null, 5, null, 5, 5], 100, 10);
    expect(g.runs).toBe(2);
    expect(g.path.startsWith("M25 1 L25 1 M")).toBe(true);
  });

  it("draws nothing when no step is measured", () => {
    const g = sparkline([null, null, NaN], 100, 10);
    expect(g).toEqual({ path: "", runs: 0, measured: 0, max: null });
  });

  it("puts a measured zero on the baseline, distinct from a gap", () => {
    const g = sparkline([0, 0, null], 100, 10);
    expect(g.measured).toBe(2);
    expect(g.path).toBe("M0 9 L50 9");
  });
});
