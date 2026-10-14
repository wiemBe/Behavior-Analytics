// The ECharts options of the three Home charts, as pure functions of a model and a
// palette. No colour is written here: every one arrives in `HomePalette`, read from
// the tokens. The honesty rules of each chart live in these builders so a test can
// read them off the option without a canvas.
//
//   * EPS line: `connectNulls` is false and an unmeasured step is shaded; there is no
//     marker on every point; the expected series is one dashed line, and there is no
//     band series at all.
//   * Class donut: at most five slices, a 2 px gap cut in the surface colour.
//   * Severity columns: one stacked series per severity, in the fixed rank order.

import { SEVERITY_ORDER } from "@/lib/home/anomalies";
import { SEVERITY_TOKEN, sliceColors, type HomePalette } from "@/lib/home/chartPalette";
import { gapText, type EpsChartModel } from "@/lib/home/epsChart";
import type { ClassEps } from "@/lib/home/epsByClass";
import type { SeverityPerHour } from "@/lib/home/severityPerHour";
import { formatDateTime } from "@/lib/health";
import { NOT_MEASURED } from "@/lib/behavior";
import { formatEpsValue } from "@/lib/fleet";

import type { EChartsOption } from "echarts";

export const EXPECTED_NAME = "Sum of per-source expected";
export const OBSERVED_NAME = "Observed EPS";
export const SLICE_GAP = 2;
export const MAX_SLICES = 5;

/** The CSS guard cannot reach a canvas, so the chart is told directly. */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function tooltipBase(p: HomePalette) {
  return {
    backgroundColor: p.tooltipBg,
    borderColor: p.tooltipBorder,
    textStyle: { color: p.tooltipText, fontFamily: p.font },
    extraCssText: "box-shadow: none;",
  };
}

const n = (v: number) => v.toLocaleString("en-US");
const eps = (v: number | null | undefined) => formatEpsValue(v) ?? NOT_MEASURED;

// --- fleet EPS line -----------------------------------------------------------

/** The tooltip for one step: the value, or why there is none. */
export function epsTooltip(model: EpsChartModel, index: number): string {
  const s = model.steps[index];
  if (!s) return "";
  const lines = [formatDateTime(new Date(s.t).toISOString())];
  lines.push(
    s.eps !== null
      ? `Observed: ${eps(s.eps)} EPS`
      : `Observed: not measured (${gapText(s)})`,
  );
  if (s.expected !== null) lines.push(`${EXPECTED_NAME}: ${eps(s.expected)} EPS`);
  return lines.join("<br/>");
}

export function buildEpsOption(
  model: EpsChartModel,
  p: HomePalette,
  reduceMotion = prefersReducedMotion(),
): EChartsOption {
  const text = { color: p.label, fontFamily: p.font };
  const observed = model.steps.map((s) => [s.t, s.eps] as [number, number | null]);
  const expected = model.steps.map((s) => [s.t, s.expected] as [number, number | null]);
  return {
    animation: !reduceMotion,
    useUTC: true,
    grid: { left: 8, right: 44, top: 30, bottom: 20, containLabel: true },
    textStyle: text,
    tooltip: {
      trigger: "axis",
      ...tooltipBase(p),
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const first = list[0] as { dataIndex?: number } | undefined;
        return first?.dataIndex != null ? epsTooltip(model, first.dataIndex) : "";
      },
    },
    xAxis: {
      type: "time",
      name: "UTC",
      nameLocation: "end",
      nameTextStyle: { ...text, align: "left", verticalAlign: "top", padding: [10, 0, 0, 0] },
      axisLine: { lineStyle: { color: p.axis } },
      // A day boundary reads "14 Oct", not a bare "14".
      axisLabel: {
        ...text,
        formatter: {
          year: "{yyyy}",
          month: "{MMM}",
          day: "{d} {MMM}",
          hour: "{HH}:{mm}",
          minute: "{HH}:{mm}",
          second: "{HH}:{mm}:{ss}",
          millisecond: "{HH}:{mm}:{ss}",
        },
      },
    },
    yAxis: {
      type: "value",
      name: "EPS",
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      splitNumber: 3,
      splitLine: { lineStyle: { color: p.grid } },
      axisLine: { show: false },
      axisLabel: { ...text, formatter: (v: number) => n(v) },
    },
    series: [
      {
        type: "line",
        name: OBSERVED_NAME,
        data: observed,
        // An unmeasured step is a break in the line, never a bridge and never a zero.
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: p.observed, width: 1.5 },
        itemStyle: { color: p.observed },
        z: 3,
        markPoint: model.lone.length
          ? {
              silent: true,
              symbol: "circle",
              symbolSize: 5,
              itemStyle: { color: p.observed },
              label: { show: false },
              data: model.lone.map((i) => ({
                name: "Measured step between gaps",
                coord: [model.steps[i].t, model.steps[i].eps as number],
              })),
            }
          : undefined,
        markArea: model.gaps.length
          ? {
              silent: true,
              label: { show: false },
              itemStyle: { color: p.gap },
              data: model.gaps.map((g) => [{ xAxis: g.start }, { xAxis: g.end }]),
            }
          : undefined,
      },
      // One dashed line, only where a step carries a sum of expected. No range series.
      ...(model.hasExpected
        ? [
            {
              type: "line" as const,
              name: EXPECTED_NAME,
              data: expected,
              connectNulls: false,
              showSymbol: false,
              silent: true,
              lineStyle: { color: p.expected, type: "dashed" as const, width: 1 },
              itemStyle: { color: p.expected },
            },
          ]
        : []),
    ],
  };
}

// --- EPS by class donut -------------------------------------------------------

export function buildClassOption(
  entries: ClassEps[],
  p: HomePalette,
  reduceMotion = prefersReducedMotion(),
): EChartsOption {
  const colors = sliceColors(p);
  const shown = entries.slice(0, MAX_SLICES);
  return {
    animation: !reduceMotion,
    tooltip: {
      trigger: "item",
      ...tooltipBase(p),
      formatter: (params: unknown) => {
        const d = params as { name?: string; value?: number; percent?: number };
        const share = d.percent != null ? ` (${d.percent.toFixed(0)} %)` : "";
        return `${d.name}: ${eps(d.value)} EPS${share}`;
      },
    },
    series: [
      {
        type: "pie",
        radius: [p.donutHoleRadius, "96%"],
        center: ["50%", "50%"],
        startAngle: 90,
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: false },
        itemStyle: { borderColor: p.surface, borderWidth: SLICE_GAP },
        data: shown.map((e, i) => ({
          name: e.label,
          value: e.eps,
          itemStyle: { color: colors[Math.min(i, colors.length - 1)] },
        })),
      },
    ],
  };
}

// --- anomaly severity columns -------------------------------------------------

/** Severities in the fixed stack order: the known ones by rank, then any unknown by name. */
export function stackOrder(severities: string[]): string[] {
  const known = (SEVERITY_ORDER as readonly string[]).filter((s) => severities.includes(s));
  const rest = severities.filter((s) => !(SEVERITY_ORDER as readonly string[]).includes(s)).sort();
  return [...known, ...rest];
}

const severityColor = (sev: string, p: HomePalette) =>
  p[SEVERITY_TOKEN[sev as keyof typeof SEVERITY_TOKEN] ?? "info"];

export function severityTooltip(r: SeverityPerHour, index: number): string {
  const h = r.hours[index];
  if (!h) return "";
  const start = formatDateTime(h.start);
  const lines = stackOrder(r.severities)
    .filter((s) => (h.counts[s] ?? 0) > 0)
    .map((s) => `${s.charAt(0)}${s.slice(1).toLowerCase()}: ${n(h.counts[s])}`);
  const total = r.lowerBound
    ? `At least ${n(h.total)} in this hour (the list was capped)`
    : `${n(h.total)} in this hour`;
  return [start, ...lines, total].join("<br/>");
}

export function buildSeverityOption(
  r: SeverityPerHour,
  p: HomePalette,
  reduceMotion = prefersReducedMotion(),
): EChartsOption {
  const text = { color: p.label, fontFamily: p.font };
  return {
    animation: !reduceMotion,
    useUTC: true,
    grid: { left: 8, right: 44, top: 30, bottom: 20, containLabel: true },
    textStyle: text,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      ...tooltipBase(p),
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const first = list[0] as { dataIndex?: number } | undefined;
        return first?.dataIndex != null ? severityTooltip(r, first.dataIndex) : "";
      },
    },
    xAxis: {
      type: "category",
      name: "UTC",
      nameLocation: "end",
      nameTextStyle: { ...text, align: "left", verticalAlign: "top", padding: [10, 0, 0, 0] },
      data: r.hours.map((h) => `${h.start.slice(11, 13)}:00`),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: p.axis } },
      // Four labels across 24 hours remain legible in the narrowest Home column.
      axisLabel: { ...text, interval: 5, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      // A capped list gives lower bounds, and the axis says so.
      name: r.lowerBound ? "anomalies per hour, at least" : "anomalies per hour",
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      minInterval: 1,
      splitNumber: 3,
      splitLine: { lineStyle: { color: p.grid } },
      axisLine: { show: false },
      axisLabel: text,
    },
    series: stackOrder(r.severities).map((sev) => ({
      type: "bar" as const,
      name: sev,
      stack: "severity",
      barCategoryGap: "25%",
      data: r.hours.map((h) => h.counts[sev] ?? 0),
      itemStyle: { color: severityColor(sev, p) },
      emphasis: { disabled: true },
    })),
  };
}
