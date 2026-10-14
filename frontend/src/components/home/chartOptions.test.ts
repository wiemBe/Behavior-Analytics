import { describe, expect, it } from "vitest";

import { CHART_TOKENS } from "@/lib/chartPalette";
import { HOME_TOKENS, type HomePalette } from "@/lib/home/chartPalette";
import { epsChartModel } from "@/lib/home/epsChart";
import { epsByClass } from "@/lib/home/epsByClass";
import { anomaly, epsPoint, epsSeries, fleet, fleetClass, noData, page } from "@/lib/home/fixtures";
import { severityPerHour } from "@/lib/home/severityPerHour";
import type { SourceClass } from "@/lib/api";

import {
  buildClassOption,
  buildEpsOption,
  buildSeverityOption,
  epsTooltip,
  EXPECTED_NAME,
  MAX_SLICES,
  severityTooltip,
  SLICE_GAP,
  stackOrder,
} from "./chartOptions";

// Every colour is the name of its token, so a literal in an option shows up as a value
// that is not one of these.
const palette = Object.fromEntries(
  [...Object.keys(CHART_TOKENS), ...Object.keys(HOME_TOKENS)].map((k) => [k, `c-${k}`]),
) as HomePalette;

type Series = {
  name: string;
  type: string;
  data: unknown[];
  connectNulls?: boolean;
  showSymbol?: boolean;
  stack?: string;
  areaStyle?: unknown;
  lineStyle?: { type?: string };
  itemStyle?: { color?: string };
  markArea?: { data: unknown[]; itemStyle?: { color?: string } };
};
const seriesOf = (o: unknown) => (o as { series: Series[] }).series;

describe("buildEpsOption", () => {
  const model = epsChartModel(
    epsSeries([epsPoint(0, 5, 4), epsPoint(1, null, 4), epsPoint(2, 6, null), epsPoint(3, 7, 4)]),
  );
  const option = buildEpsOption(model, palette, false);
  const [observed, expected] = seriesOf(option);

  it("hands the unmeasured step over as null and does not connect across it", () => {
    expect(observed.data).toEqual([
      [model.steps[0].t, 5],
      [model.steps[1].t, null],
      [model.steps[2].t, 6],
      [model.steps[3].t, 7],
    ]);
    expect(observed.connectNulls).toBe(false);
  });

  it("shades the unmeasured interval in the gap colour", () => {
    expect(observed.markArea?.data).toHaveLength(1);
    expect(observed.markArea?.itemStyle?.color).toBe("c-gap");
  });

  it("draws no marker on every point", () => {
    for (const s of seriesOf(option)) expect(s.showSymbol).toBe(false);
  });

  it("draws the expected sum as one dashed, named line that breaks where it is null", () => {
    expect(expected.name).toBe(EXPECTED_NAME);
    expect(expected.lineStyle?.type).toBe("dashed");
    expect(expected.connectNulls).toBe(false);
    expect(expected.data[2]).toEqual([model.steps[2].t, null]);
  });

  it("has no expected series at all where no step carries one", () => {
    const bare = epsChartModel(epsSeries([epsPoint(0, 5), epsPoint(1, 6)]));
    expect(seriesOf(buildEpsOption(bare, palette, false)).map((s) => s.name)).toEqual([
      "Observed EPS",
    ]);
  });

  it("has no band: no stacked or filled series", () => {
    for (const s of seriesOf(option)) {
      expect(s.stack).toBeUndefined();
      expect(s.areaStyle).toBeUndefined();
    }
  });

  it("names its units: EPS on y, UTC on x", () => {
    const o = option as unknown as { yAxis: { name: string }; xAxis: { name: string }; useUTC: boolean };
    expect(o.yAxis.name).toBe("EPS");
    expect(o.xAxis.name).toBe("UTC");
    expect(o.useUTC).toBe(true);
  });

  it("takes every colour from the palette, never a literal", () => {
    expect(JSON.stringify(option)).not.toMatch(/#[0-9a-f]{3,8}|rgba?\(/i);
  });

  it("marks a lone measured step, because a line cannot show one point", () => {
    const m = epsChartModel(epsSeries([epsPoint(0, null), epsPoint(1, 4), epsPoint(2, null)]));
    const o = seriesOf(buildEpsOption(m, palette, false))[0] as Series & {
      markPoint?: { data: unknown[] };
    };
    expect(o.markPoint?.data).toHaveLength(1);
  });

  it("switches animation off for reduced motion", () => {
    expect((buildEpsOption(model, palette, true) as { animation: boolean }).animation).toBe(false);
  });
});

describe("epsTooltip", () => {
  const model = epsChartModel(epsSeries([epsPoint(0, 5, 4), epsPoint(1, null, 4)]));

  it("says a gap was not measured and how much of it was", () => {
    const t = epsTooltip(model, 1);
    expect(t).toMatch(/not measured/);
    expect(t).toMatch(/3 of 4 source-intervals measured/);
    expect(t).not.toMatch(/Observed: 0/);
  });

  it("gives the value with its unit for a measured step", () => {
    expect(epsTooltip(model, 0)).toMatch(/Observed: 5\.00 EPS/);
    expect(epsTooltip(model, 0)).toMatch(new RegExp(`${EXPECTED_NAME}: 4\\.00 EPS`));
  });

  it("is empty for an index outside the series", () => {
    expect(epsTooltip(model, 9)).toBe("");
  });
});

describe("buildClassOption", () => {
  const seven: SourceClass[] = ["FIREWALL", "WAF", "LINUX", "PROXY", "WINDOWS", "DNS", "DATABASE"];
  const entries = (cs = seven) =>
    epsByClass(fleet(cs.map((c, i) => fleetClass(c, { mean_eps: 100 - i * 10 })))).entries;

  it("never has more than five data items", () => {
    const data = seriesOf(buildClassOption(entries(), palette, false))[0].data;
    expect(data).toHaveLength(MAX_SLICES);
  });

  it("caps what it is handed at five, so a caller's mistake cannot draw a sixth slice", () => {
    const raw = Array.from({ length: 7 }, (_, i) => ({
      key: "FIREWALL" as const,
      label: `c${i}`,
      eps: 10 - i,
      share: 0.1,
    }));
    expect(seriesOf(buildClassOption(raw, palette, false))[0].data).toHaveLength(MAX_SLICES);
  });

  it("cuts a 2 px gap in the surface colour between slices", () => {
    const s = seriesOf(buildClassOption(entries(), palette, false))[0] as unknown as {
      itemStyle: { borderWidth: number; borderColor: string };
    };
    expect(SLICE_GAP).toBe(2);
    expect(s.itemStyle).toEqual({ borderWidth: 2, borderColor: "c-surface" });
  });

  it("colours the slices with the categorical tokens and the aggregate with the other token", () => {
    const data = seriesOf(buildClassOption(entries(), palette, false))[0].data as Array<{
      itemStyle: { color: string };
    }>;
    expect(data.map((d) => d.itemStyle.color)).toEqual([
      "c-cat1",
      "c-cat2",
      "c-cat3",
      "c-cat4",
      "c-other",
    ]);
  });

  it("holds a class with no mean nowhere: not as a zero slice either", () => {
    const m = epsByClass(fleet([fleetClass("FIREWALL", { mean_eps: 10 }), noData("WAF")]));
    const data = seriesOf(buildClassOption(m.entries, palette, false))[0].data as Array<{
      name: string;
      value: number;
    }>;
    expect(data.map((d) => d.name)).toEqual(["Firewall"]);
  });

  it("takes every colour from the palette", () => {
    expect(JSON.stringify(buildClassOption(entries(), palette, false))).not.toMatch(/#[0-9a-f]{3,8}/i);
  });
});

describe("buildSeverityOption", () => {
  const range = { since: "2026-10-14T08:00:00Z", until: "2026-10-14T12:00:00Z" };
  const at = (severity: string, iso = "2026-10-14T09:10:00Z") =>
    anomaly({ severity, detected_at: iso });
  // Deliberately out of rank order.
  const model = severityPerHour(
    page([at("LOW"), at("CRITICAL"), at("INFO"), at("HIGH"), at("MEDIUM"), at("HIGH")]),
    range,
  );

  it("stacks the severities in the fixed rank order, whatever order they arrived in", () => {
    const names = seriesOf(buildSeverityOption(model, palette, false)).map((s) => s.name);
    expect(names).toEqual(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]);
    expect(stackOrder(["INFO", "ZED", "CRITICAL", "ABC"])).toEqual(["CRITICAL", "INFO", "ABC", "ZED"]);
  });

  it("colours each severity with its --sev token, in one stack", () => {
    const series = seriesOf(buildSeverityOption(model, palette, false));
    expect(series.map((s) => s.itemStyle?.color)).toEqual([
      "c-critical",
      "c-high",
      "c-medium",
      "c-low",
      "c-info",
    ]);
    expect(new Set(series.map((s) => s.stack)).size).toBe(1);
  });

  it("gives one column per hour and the counts as numbers", () => {
    const series = seriesOf(buildSeverityOption(model, palette, false));
    expect(series[1].data).toEqual([0, 2, 0, 0]);
  });

  it("states a lower bound on the axis and in the tooltip when the list was capped", () => {
    const capped = severityPerHour({ ...page([at("HIGH")]), total: 345 }, range);
    const o = buildSeverityOption(capped, palette, false) as unknown as { yAxis: { name: string } };
    expect(o.yAxis.name).toMatch(/at least/);
    expect(severityTooltip(capped, 1)).toMatch(/At least 1/);
  });

  it("says anomalies per hour, and nothing about a bound, for a complete list", () => {
    const o = buildSeverityOption(model, palette, false) as unknown as { yAxis: { name: string } };
    expect(o.yAxis.name).toBe("anomalies per hour");
    expect(severityTooltip(model, 1)).not.toMatch(/At least/);
  });

  it("takes every colour from the palette", () => {
    expect(JSON.stringify(buildSeverityOption(model, palette, false))).not.toMatch(/#[0-9a-f]{3,8}/i);
  });
});
