import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { bucket, HOSTILE, iso, point, series } from "./fixtures";
import {
  buildCoverage,
  buildPwOption,
  PW_CATEGORY_TOKENS,
  pwTooltip,
  type PwPalette,
} from "./pwChartOption";
import { buildPlot } from "./pwSeries";

const palette: PwPalette = {
  cat: ["#c1", "#c2", "#c3", "#c4", "#c5"],
  grid: "#g",
  axis: "#a",
  label: "#l",
  band: "#band",
  gap: "#gap",
  anomaly: "#anomaly",
  history: "#history",
  marker: "#m",
  expected: "#e",
  tooltipBg: "#tb",
  tooltipBorder: "#tbd",
  tooltipText: "#tt",
  font: "Inter",
};

const WINDOW = { windowStart: iso(0), windowEnd: iso(4), bucketSeconds: 300 };

interface Item {
  value: [number, number | null];
  symbol: string;
}
interface Ser {
  name: string;
  data: Array<Item | [number, number | null]>;
  connectNulls?: boolean;
  stack?: string;
  lineStyle?: { color?: string };
  markArea?: { data: unknown[] };
  markLine?: unknown;
  xAxisIndex: number;
}
const opt = (o: unknown) =>
  o as { series: Ser[]; yAxis: Array<{ name?: string }>; xAxis: Array<{ name?: string }>; useUTC: boolean; tooltip: { formatter: (p: unknown) => string } };

function build(
  list: ReturnType<typeof series>[],
  extra: Partial<Parameters<typeof buildPwOption>[0]> = {},
) {
  const model = buildPlot({ series: list, ...WINDOW });
  return { model, option: opt(buildPwOption({ model, palette, ...extra })) };
}

const one = [
  series("a", "alice", [
    point(0),
    point(1, { reading: "MEASURED_ZERO", events: 0 }),
    point(3, { events: 55, measurement: "LOWER_BOUND" }),
  ]),
];

describe("the option", () => {
  it("never connects across a gap, on any series", () => {
    const { option } = build(one, { coverage: buildCoverage([], [], 0.9) });
    for (const s of option.series) expect(s.connectNulls).toBe(false);
  });

  it("puts a null at an uncollected slot and 0 at a measured zero", () => {
    const { option } = build(one);
    const data = option.series[0].data as Item[];
    expect(data.map((d) => d.value[1])).toEqual([100, 0, null, 55, null]);
  });

  it("marks a lower-bound point with a hollow marker and no other point with one", () => {
    const { option } = build(one);
    const data = option.series[0].data as Item[];
    expect(data.map((d) => d.symbol)).toEqual(["none", "none", "none", "emptyCircle", "none"]);
  });

  it("names its axes with the unit and UTC", () => {
    const { option } = build(one);
    expect(option.yAxis[0].name).toBe("events per 5 min");
    expect(option.xAxis[0].name).toBe("Time (UTC)");
    expect(option.useUTC).toBe(true);
  });

  it("colours series with the categorical tokens in order, and nothing else", () => {
    const list = [0, 1, 2].map((i) => series(`i${i}`, `v${i}`, [point(0)]));
    const { option } = build(list);
    expect(option.series.map((s) => s.lineStyle?.color)).toEqual(["#c1", "#c2", "#c3"]);
  });

  it("draws the expected range as one band, and only for a single value", () => {
    const single = build(one).option.series.map((s) => s.name);
    expect(single).toContain("Expected range");
    expect(single.filter((n) => n.startsWith("Expected"))).toHaveLength(2);
    const several = build([...one, series("b", "bob", [point(0)])]).option.series.map((s) => s.name);
    expect(several.some((n) => n.startsWith("Expected"))).toBe(false);
  });

  it("shades uncollected spans and incident spans", () => {
    const { option } = build(one, { incidents: [{ start: iso(1), end: null }] });
    const areas = option.series[0].markArea!.data as Array<[{ name: string }, unknown]>;
    expect(areas.map((a) => a[0].name).sort()).toEqual(["incident", "not collected", "not collected"]);
  });

  it("shades history points neutrally and labels their origin in the tooltip", () => {
    const historical = [series("a", "alice", [point(0, { origin: "HISTORY" }), point(1)])];
    const { option } = build(historical);
    const areas = option.series[0].markArea!.data as Array<[{ name: string }, unknown]>;
    expect(areas.map((area) => area[0].name)).toContain("read from QRadar history, not judged");
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain(
      "read from QRadar history, not judged",
    );
  });

  it("adds the parsed-share panel on the same time axis, with one labelled floor line", () => {
    const model = buildPlot({ series: one, ...WINDOW });
    const coverage = buildCoverage(
      model.slots,
      [bucket(0), bucket(1, { total_events: null, parsed_events: null })],
      0.9,
    );
    const option = opt(buildPwOption({ model, palette, coverage }));
    const share = option.series.find((s) => s.name === "Parsed share")!;
    expect(share.xAxisIndex).toBe(1);
    expect((share.data as Array<[number, number | null]>).map((d) => d[1])).toEqual([96, null, null, null, null]);
    expect(share.markLine).toBeDefined();
    expect(option.yAxis[1].name).toContain("Parsed share");
  });

  it("drops the coverage panel in compact form", () => {
    const model = buildPlot({ series: one, ...WINDOW });
    const coverage = buildCoverage(model.slots, [bucket(0)], 0.9);
    const option = opt(buildPwOption({ model, palette, coverage, compact: true }));
    expect(option.series.some((s) => s.name === "Parsed share")).toBe(false);
  });
});

describe("coverage", () => {
  it("is unknown, not zero, when the totals are incomplete or empty", () => {
    const slots = [0, 1, 2].map((n) => Date.parse(iso(n)));
    const cov = buildCoverage(
      slots,
      [
        bucket(0, { totals_complete: false }),
        bucket(1, { total_events: 0, parsed_events: 0, unparsed_events: 0 }),
        bucket(2, { values_measurement: "LOWER_BOUND", parsed_events: 500, unparsed_events: 500 }),
      ],
      0.9,
    );
    expect(cov.points.map((p) => p.ratio)).toEqual([null, null, 0.5]);
    expect(cov.points[2].lowerBound).toBe(true);
  });
});

describe("the tooltip", () => {
  const tip = (list: ReturnType<typeof series>[], i: number, coverage = null as ReturnType<typeof buildCoverage> | null) => {
    const model = buildPlot({ series: list, ...WINDOW });
    return pwTooltip(model, i, { unit: "events per 5 min", coverage });
  };

  it("reads a lower bound as 'at least n', a measured zero as 0 and a gap in words", () => {
    expect(tip(one, 3)).toContain("at least 55 events per 5 min");
    expect(tip(one, 1)).toContain("alice: 0 events per 5 min (measured zero)");
    const gap = tip(one, 2);
    expect(gap).toContain("not plotted");
    expect(gap).toContain("bucket not collected");
    expect(gap).not.toMatch(/alice: 0/);
  });

  it("states the expected range for one value, and says when there is none", () => {
    expect(tip(one, 0)).toContain("Expected range for this hour of the week: 80 to 140");
    const learning = [series("a", "alice", [point(0, { baseline_state: "LEARNING" })])];
    expect(tip(learning, 0)).toContain("learning");
  });

  it("encodes a hostile value before it reaches the tooltip HTML (T-117)", () => {
    const hostile = [series("a", HOSTILE, [point(0)]), series("b", "bob", [point(0)])];
    const html = tip(hostile, 0);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).not.toContain('onerror="');
  });

  it("encodes a hostile value through the formatter ECharts calls", () => {
    const hostile = [series("a", HOSTILE, [point(0)])];
    const { option } = build(hostile);
    const html = option.tooltip.formatter([{ dataIndex: 0 }]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("tokens", () => {
  it("reads only categorical tokens, and each is declared in tokens.css", () => {
    const css = readFileSync(join(process.cwd(), "src/app/tokens.css"), "utf8");
    expect(PW_CATEGORY_TOKENS).toHaveLength(5);
    for (const token of PW_CATEGORY_TOKENS) {
      expect(token).toMatch(/^--chart-cat-[1-5]$/);
      expect(css).toContain(`${token}:`);
    }
  });
});

describe("the expected range at detection", () => {
  it("is one rectangle over the incident with the low and high edge, never three lines", () => {
    const model = buildPlot({
      series: [series("id-1", "svc-backup", [point(0), point(1), point(2)])],
      ...WINDOW,
    });
    const without = buildPwOption({ model, palette, incidents: [] });
    const withBand = buildPwOption({
      model,
      palette,
      incidents: [],
      detectionBand: { start: iso(0), end: iso(2), low: 80, high: 140 },
    });
    const areaOf = (option: ReturnType<typeof buildPwOption>) =>
      (option.series as Array<{ markArea?: { data: unknown[] } }>)[0].markArea?.data ?? [];
    const data = areaOf(withBand) as Array<Array<{ xAxis: number; yAxis: number }>>;
    expect(data).toHaveLength(areaOf(without).length + 1);
    const rect = data[data.length - 1];
    expect(rect[0]).toMatchObject({ xAxis: Date.parse(iso(0)), yAxis: 80 });
    expect(rect[1]).toMatchObject({ xAxis: Date.parse(iso(2)), yAxis: 140 });
    // No extra line series were added for the band.
    expect((withBand.series as unknown[]).length).toBe((without.series as unknown[]).length);
  });
});

describe("the coverage floor label", () => {
  it("is a whole percent with U+00A0 before the sign", () => {
    const model = buildPlot({ series: one, ...WINDOW });
    const coverage = buildCoverage(model.slots, [bucket(0)], 0.9);
    const option = opt(buildPwOption({ model, palette, coverage }));
    const share = option.series.find((s) => s.name === "Parsed share")!;
    const line = share.markLine as unknown as { label: { formatter: string } };
    expect(line.label.formatter).toBe("Floor 90\u00A0%");
  });
});
