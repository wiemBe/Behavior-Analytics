import { describe, expect, it } from "vitest";

import type { EpsSeriesPoint, FleetEpsSeries } from "@/lib/api";

import { epsChartModel, epsSummaryText, gapText } from "./epsChart";

const STEP = 300;
const T0 = Date.parse("2026-10-14T12:00:00Z");
const iso = (i: number) => new Date(T0 + i * STEP * 1000).toISOString();

function pt(i: number, eps: number | null, expected: number | null = null): EpsSeriesPoint {
  return {
    step_start: iso(i),
    events: null,
    eps,
    expected_eps: expected,
    cells_complete: eps === null ? 3 : 4,
    cells_expected: 4,
    sources_expected: 4,
  };
}

function series(points: EpsSeriesPoint[]): FleetEpsSeries {
  return {
    window: "1h",
    group_by: "none",
    window_start: iso(0),
    window_end: iso(points.length),
    bucket_seconds: 300,
    step_seconds: STEP,
    series: [{ key: "all", label: "All", sources_expected: 4, points }],
    coverage: { steps_total: points.length, steps_measured: points.filter((p) => p.eps !== null).length },
  };
}

describe("epsChartModel", () => {
  it("turns each run of unmeasured steps into one gap interval", () => {
    const m = epsChartModel(
      series([pt(0, 5), pt(1, null), pt(2, null), pt(3, 6), pt(4, null)]),
    );
    expect(m.gaps).toEqual([
      { start: T0 + 1 * STEP * 1000, end: T0 + 3 * STEP * 1000 },
      // a trailing gap runs to the end of the last step
      { start: T0 + 4 * STEP * 1000, end: T0 + 5 * STEP * 1000 },
    ]);
  });

  it("keeps a null step null, never zero", () => {
    const m = epsChartModel(series([pt(0, 5), pt(1, null), pt(2, 0)]));
    expect(m.steps.map((s) => s.eps)).toEqual([5, null, 0]);
  });

  it("marks a lone measured step between gaps", () => {
    const m = epsChartModel(series([pt(0, null), pt(1, 4), pt(2, null), pt(3, 5), pt(4, 6)]));
    expect(m.lone).toEqual([1]);
  });

  it("is PARTIAL with counts when some steps are null", () => {
    const m = epsChartModel(series([pt(0, 5), pt(1, null), pt(2, 5), pt(3, 5)]));
    expect(m.measured).toBe(3);
    expect(m.qualifiers).toEqual([
      expect.objectContaining({ kind: "PARTIAL", have: 3, of: 4, text: "3 of 4 steps collected" }),
    ]);
  });

  it("is NOT_COLLECTED, not partial and not zero, when no step is measured", () => {
    const m = epsChartModel(series([pt(0, null), pt(1, null)]));
    expect(m.measured).toBe(0);
    expect(m.peak).toBeNull();
    expect(m.qualifiers.map((q) => q.kind)).toEqual(["NOT_COLLECTED"]);
  });

  it("is NOT_COLLECTED for an empty window", () => {
    expect(epsChartModel(series([])).qualifiers.map((q) => q.kind)).toEqual(["NOT_COLLECTED"]);
  });

  it("counts measured steps from the steps, not from the coverage field", () => {
    const s = series([pt(0, null), pt(1, null)]);
    s.coverage = { steps_total: 2, steps_measured: 2 };
    expect(epsChartModel(s).measured).toBe(0);
  });

  it("has an expected line only where some step carries one", () => {
    expect(epsChartModel(series([pt(0, 5), pt(1, 5)])).hasExpected).toBe(false);
    expect(epsChartModel(series([pt(0, 5, 4), pt(1, 5)])).hasExpected).toBe(true);
  });

  it("has no qualifier when every step is measured", () => {
    expect(epsChartModel(series([pt(0, 5), pt(1, 6)])).qualifiers).toEqual([]);
  });
});

describe("gapText", () => {
  it("says n of m source-intervals measured", () => {
    expect(gapText({ cellsComplete: 3, cellsExpected: 4 })).toBe(
      "3 of 4 source-intervals measured",
    );
  });
});

describe("epsSummaryText", () => {
  it("says what the dashed line is, and that there is no range", () => {
    const s = series([pt(0, 5, 4), pt(1, 6, 4)]);
    const text = epsSummaryText(s, epsChartModel(s));
    expect(text).toMatch(/sum of per-source expected/);
    expect(text).toMatch(/not a range/);
    expect(text).toMatch(/UTC/);
  });

  it("says no line is drawn when nothing was measured", () => {
    const s = series([pt(0, null)]);
    expect(epsSummaryText(s, epsChartModel(s))).toMatch(/no line is drawn/);
  });

  it("says there is no expected line when no baseline is reliable", () => {
    const s = series([pt(0, 5)]);
    expect(epsSummaryText(s, epsChartModel(s))).toMatch(/No expected line/);
  });
});
