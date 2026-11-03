import { describe, expect, it } from "vitest";

import type { HomePalette } from "@/lib/home/chartPalette";

import { consoleHistory } from "./fixtures";
import { hostLines, latencyLine, reachabilitySegments } from "./qradarDetail";
import {
  buildLatencyOption,
  buildLinesOption,
  buildReachabilityOption,
  byteAxis,
  formatAxisValue,
  lineColours,
  type TrendPalette,
} from "./trendOptions";

const PALETTE = new Proxy({} as Record<string, string>, {
  get: (_target, key) => `colour-${String(key)}`,
}) as unknown as HomePalette & TrendPalette;
const RANGE = { since: Date.parse("2026-11-01T10:00:00Z"), until: Date.parse("2026-11-02T10:00:00Z") };
const WORDS = {
  xAxis: "Time (UTC)",
  yAxis: "Used (%)",
  tipValue: (host: string, value: number, mount: string | null) => `${host} ${value} ${mount ?? ""}`,
};

const group = () => hostLines(consoleHistory(), (h) => h.disk_fullest_fraction, 100, 1350);

describe("the line charts", () => {
  it("draw a line that breaks at a gap, with no marker on any point", () => {
    const option = buildLinesOption(group().lines, "percent", WORDS, PALETTE, RANGE, 100) as {
      series: Array<Record<string, unknown>>;
    };
    expect(option.series).toHaveLength(1);
    expect(option.series[0].connectNulls).toBe(false);
    expect(option.series[0].showSymbol).toBe(false);
    expect(option.series[0].type).toBe("line");
  });

  it("name the unit on the value axis and the zone on the time axis", () => {
    const option = buildLinesOption(group().lines, "percent", WORDS, PALETTE, RANGE, 100) as {
      xAxis: { name: string; type: string; min: number; max: number };
      yAxis: { name: string; min: number; max: number };
    };
    expect(option.yAxis.name).toBe("Used (%)");
    expect(option.xAxis.name).toBe("Time (UTC)");
    expect(option.xAxis.type).toBe("time");
    expect([option.xAxis.min, option.xAxis.max]).toEqual([RANGE.since, RANGE.until]);
    expect(option.yAxis.min).toBe(0);
    expect(option.yAxis.max).toBe(100);
  });

  it("take every series colour from the palette, one categorical colour per host in token order", () => {
    const lines = Array.from({ length: 3 }, (_, at) => ({ ...group().lines[0], host: `h${at}` }));
    const option = buildLinesOption(lines, "count", WORDS, PALETTE, RANGE) as {
      series: Array<{ lineStyle: { color: string } }>;
    };
    expect(option.series.map((s) => s.lineStyle.color)).toEqual(lineColours(PALETTE).slice(0, 3));
    expect(lineColours(PALETTE)).toEqual([
      "colour-cat1",
      "colour-cat2",
      "colour-cat3",
      "colour-cat4",
      "colour-cat5",
    ]);
  });

  it("use no status hue for a host series", () => {
    expect(lineColours(PALETTE).join()).not.toMatch(/crit|warn|ok/);
  });

  it("scale the bytes axis with units", () => {
    expect(formatAxisValue("bytes", 3 * 1024 ** 3)).toBe("3.00 GiB");
    expect(formatAxisValue("count", 12_345)).toBe("12,345");
    expect(formatAxisValue("percent", 50)).toBe("50");
  });

  it("put the bytes axis in one binary unit with round ticks", () => {
    const GiB = 1024 ** 3;
    const axis = byteAxis(3.4 * GiB);
    expect(axis.unit).toBe("GiB");
    expect(axis.interval).toBe(GiB);
    expect(axis.max).toBe(4 * GiB);
    const ticks = Array.from({ length: 5 }, (_, i) => axis.label(i * axis.interval));
    expect(ticks).toEqual(["0\u00A0GiB", "1\u00A0GiB", "2\u00A0GiB", "3\u00A0GiB", "4\u00A0GiB"]);
    // A smaller series takes a smaller unit, and the ticks are 1, 2 or 5 times a power of ten.
    const small = byteAxis(900 * 1024 ** 2);
    expect(small.unit).toBe("MiB");
    expect(small.interval / 1024 ** 2).toBe(500);
    expect(small.max / 1024 ** 2).toBe(1000);
    // No measured byte at all still gives a drawable axis, never NaN.
    expect(byteAxis(0).max).toBeGreaterThan(0);
    expect(Number.isFinite(byteAxis(Number.NaN).interval)).toBe(true);
  });

  it("hand the bytes axis its interval and top to the chart", () => {
    const spill = hostLines(consoleHistory(), (h) => h.throttle_spill_bytes, 1, 1350);
    const option = buildLinesOption(spill.lines, "bytes", WORDS, PALETTE, RANGE) as {
      yAxis: { interval: number; max: number; axisLabel: { formatter: (v: number) => string } };
    };
    expect(option.yAxis.interval).toBeGreaterThan(0);
    expect(option.yAxis.max % option.yAxis.interval).toBe(0);
    expect(option.yAxis.axisLabel.formatter(0)).toMatch(/^0\u00A0(B|KiB|MiB|GiB)$/);
  });

  it("show the tooltip's mount with the value, and nothing for a break", () => {
    const g = group();
    const option = buildLinesOption(g.lines, "percent", WORDS, PALETTE, RANGE, 100) as {
      tooltip: { formatter: (p: unknown) => string };
    };
    const [t, v] = g.lines[0].data[0] as [number, number];
    const html = option.tooltip.formatter([{ seriesName: "qr-console", value: [t, v] }]);
    expect(html).toContain("qr-console 40 /store");
    expect(html).toContain("<time");
    expect(option.tooltip.formatter([{ seriesName: "qr-console", value: [t, null] }])).toBe("");
  });
});

describe("the reachability band", () => {
  it("is a band of spans coloured up, down and unknown, not a line", () => {
    const history = consoleHistory();
    const segments = reachabilitySegments(history.reachability.points, 900, 1350);
    const option = buildReachabilityOption(
      segments,
      { xAxis: "Time (UTC)", up: "Reachable", down: "Not reachable", unknown: "Not reported" },
      PALETTE,
      RANGE,
    ) as { series: Array<{ type: string; data: Array<[number, number, string]> }> };
    expect(option.series[0].type).toBe("custom");
    expect(option.series[0].data.map((d) => d[2])).toEqual(["up", "up", "down", "up"]);
  });

  it("words each span in the tooltip", () => {
    const segments = reachabilitySegments(consoleHistory().reachability.points, 900, 1350);
    const option = buildReachabilityOption(
      segments,
      { xAxis: "Time (UTC)", up: "Reachable", down: "Not reachable", unknown: "Not reported" },
      PALETTE,
      RANGE,
    ) as { tooltip: { formatter: (p: unknown) => string } };
    const [start, end] = [segments[2].start, segments[2].end];
    expect(option.tooltip.formatter({ value: [start, end, "down"] })).toContain("Not reachable");
  });
});

describe("the latency chart", () => {
  it("is a line in ms that breaks where the console did not answer", () => {
    const line = latencyLine(consoleHistory().reachability.points, 1350);
    const option = buildLatencyOption(
      line,
      { xAxis: "Time (UTC)", yAxis: "API latency (ms)", tipValue: (_h, v) => `${v} ms` },
      PALETTE,
      RANGE,
    ) as { yAxis: { name: string }; series: Array<{ connectNulls: boolean; showSymbol: boolean; data: unknown[] }> };
    expect(option.yAxis.name).toBe("API latency (ms)");
    expect(option.series[0].connectNulls).toBe(false);
    expect(option.series[0].showSymbol).toBe(false);
    expect(line.map(([, v]) => v)).toEqual([180, 210, null, 190]);
  });
});
