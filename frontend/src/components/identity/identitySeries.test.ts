import { describe, expect, it } from "vitest";

import { point } from "./fixtures";
import {
  buildIdentityOption,
  buildSeriesPlot,
  plotCounts,
  slotTooltip,
  type IdentityChartWords,
  type IdentityPalette,
} from "./identitySeries";

const START = Date.parse("2026-11-13T10:00:00Z");
const STEP = 300_000;
const at = (i: number) => new Date(START + i * STEP).toISOString();

const words: IdentityChartWords = {
  unit: "events per 5 min",
  axisTime: "Time (UTC)",
  notCollected: "Not collected",
  atFloor: "At its floor",
  count: (v) => `${v} events`,
  atLeast: (v) => `At least ${v} events`,
  noCount: "Not collected: no count",
  namesCut: "names cut",
};
const palette: IdentityPalette = {
  line: "#111", floor: "#999", grid: "#222", axis: "#333", label: "#444", gap: "#555",
  tooltipBg: "#666", tooltipBorder: "#777", tooltipText: "#888", font: "sans",
};

describe("buildSeriesPlot", () => {
  const plot = buildSeriesPlot(
    [
      point({ bucket_start: at(0), event_count: 0 }),
      point({ bucket_start: at(1), event_count: 50, measurement: "TRUNCATED" }),
      point({ bucket_start: at(3), measurement: "NOT_COLLECTED", event_count: null }),
    ],
    4625,
    START,
    START + 6 * STEP,
    300,
  );
  it("keeps a measured zero, a floor, and makes everything else null", () => {
    expect(plot.slots.map((s) => s.value)).toEqual([0, 50, null, null, null, null]);
    expect(plot.slots.map((s) => s.kind)).toEqual([
      "EXACT", "TRUNCATED", "NOT_COLLECTED", "NOT_COLLECTED", "NOT_COLLECTED", "NOT_COLLECTED",
    ]);
  });
  it("shades the contiguous uncollected slots, stored or missing, as spans", () => {
    expect(plot.uncollected).toEqual([{ start: START + 2 * STEP, end: START + 6 * STEP }]);
    expect(plotCounts(plot)).toEqual({ exact: 1, truncated: 1, notCollected: 4 });
  });
  it("ignores other event ids", () => {
    const other = buildSeriesPlot([point({ event_id: 4740, bucket_start: at(0) })], 4625, START, START + STEP, 300);
    expect(other.slots[0].value).toBeNull();
  });
});

describe("buildIdentityOption", () => {
  const plot = buildSeriesPlot(
    [point({ bucket_start: at(0), event_count: 4 }), point({ bucket_start: at(2), event_count: 9, measurement: "TRUNCATED" })],
    4625, START, START + 4 * STEP, 300,
  );
  const option = buildIdentityOption(plot, palette, words, true) as unknown as {
    yAxis: { name: string; min: number };
    series: Array<{ connectNulls: boolean; data: Array<{ value: [number, number | null]; symbol: string }>; markArea?: unknown }>;
  };
  it("names the unit on the axis and never connects a gap", () => {
    expect(option.yAxis.name).toBe("events per 5 min");
    expect(option.series).toHaveLength(1);
    expect(option.series[0].connectNulls).toBe(false);
  });
  it("draws a gap as null, a floor with a hollow marker, and shades the uncollected span", () => {
    expect(option.series[0].data.map((d) => d.value[1])).toEqual([4, null, 9, null]);
    expect(option.series[0].data[2].symbol).toBe("emptyCircle");
    expect(option.series[0].markArea).toBeDefined();
  });
  it("shades a run of floor slots instead of marking every point", () => {
    const run = buildSeriesPlot(
      [0, 1, 2, 3].map((i) => point({ bucket_start: at(i), event_count: 5000, measurement: "TRUNCATED" })),
      4625, START, START + 4 * STEP, 300,
    );
    expect(run.floor).toEqual([{ start: START, end: START + 4 * STEP }]);
    const o = buildIdentityOption(run, palette, words, true) as unknown as {
      series: Array<{ data: Array<{ symbol: string }>; markArea: { data: unknown[] } }>;
    };
    expect(o.series[0].data.every((d) => d.symbol === "none")).toBe(true);
    expect(o.series[0].markArea.data).toHaveLength(1);
  });
  it("draws no expected band", () => {
    expect(JSON.stringify(option)).not.toMatch(/Expected|stack/);
  });
});

describe("slotTooltip", () => {
  const plot = buildSeriesPlot(
    [
      point({ bucket_start: at(0), event_count: 4 }),
      point({ bucket_start: at(1), event_count: 9, measurement: "TRUNCATED", names_measurement: "TRUNCATED" }),
    ],
    4625, START, START + 3 * STEP, 300,
  );
  it("says events, at least, or not collected", () => {
    expect(slotTooltip(plot, 0, words)).toContain("4 events");
    expect(slotTooltip(plot, 1, words)).toContain("At least 9 events");
    expect(slotTooltip(plot, 1, words)).toContain("names cut");
    expect(slotTooltip(plot, 2, words)).toContain("Not collected: no count");
    expect(slotTooltip(plot, 2, words)).not.toMatch(/\b0 events/);
  });
  it("encodes markup in a word", () => {
    const html = slotTooltip(plot, 2, { ...words, noCount: "<img src=x onerror=1>" });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});
