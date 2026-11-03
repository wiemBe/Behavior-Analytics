// The ECharts options of the QRadar trends, as pure functions of a model and a palette, so a test
// reads the honesty rules off the option without a canvas. No colour is written here: every one
// arrives in the palette, read from the tokens.
//
//   * A line breaks wherever windows are missing (`connectNulls` is false and the model holds a
//     null); no marker is drawn on any point.
//   * One categorical colour per host, in the fixed token order; the key beside the chart names
//     every line with its value, so no line relies on hue alone.
//   * Every value axis names its unit, and the time axis names UTC.
//   * Reachability is a band of spans (up, down), not a line: a sample is a state, not a number.

import { utcTimeHtml } from "@/components/ui/UtcTime";
import { formatCount } from "@/lib/format";
import type { HomePalette } from "@/lib/home/chartPalette";

import { formatBytes } from "./siemFormat";
import type { HostLine, Segment, SeriesPoint } from "./qradarDetail";

import type { CustomSeriesRenderItem, EChartsOption } from "echarts";

/** The tick convention of every time axis: a day boundary reads "14 Oct", other ticks `HH:MM`. */
const TIME_AXIS_FORMATTER = {
  year: "{yyyy}",
  month: "{MMM}",
  day: "{d} {MMM}",
  hour: "{HH}:{mm}",
  minute: "{HH}:{mm}",
  second: "{HH}:{mm}:{ss}",
  millisecond: "{HH}:{mm}:{ss}",
} as const;

/** The CSS guard cannot reach a canvas, so the chart is told directly. */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** The home palette plus the fifth categorical colour a five-host chart needs, and the up tone. */
export type TrendPalette = HomePalette & { cat5: string; ok: string };

export function withTrendColours(
  p: HomePalette,
  root: Element = document.documentElement,
): TrendPalette {
  const style = getComputedStyle(root);
  return {
    ...p,
    cat5: style.getPropertyValue("--chart-cat-5").trim(),
    ok: style.getPropertyValue("--ok").trim(),
  };
}

export function lineColours(p: TrendPalette): string[] {
  return [p.cat1, p.cat2, p.cat3, p.cat4, p.cat5];
}

export interface ChartWords {
  xAxis: string;
  /** The value axis name, with its unit. */
  yAxis: string;
  /** "{value}" of one point in the tooltip, for a host. */
  tipValue: (host: string, value: number, mount: string | null) => string;
}

export type ValueKind = "percent" | "count" | "bytes" | "ms";

export function formatAxisValue(kind: ValueKind, value: number): string {
  switch (kind) {
    case "percent":
      return `${formatCount(value)}`;
    case "bytes":
      return formatBytes(value);
    default:
      return formatCount(Math.round(value));
  }
}

const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;

export interface ByteAxis {
  unit: string;
  /** Distance between two ticks, in bytes: 1, 2 or 5 times a power of ten of the unit. */
  interval: number;
  /** The top of the axis, in bytes: a whole number of intervals. */
  max: number;
  label: (bytes: number) => string;
}

/**
 * A bytes axis in one binary unit, picked from the series maximum, with ticks at round values of
 * that unit (0, 1, 2, 3, 4 GiB) instead of whatever the chart library finds in raw bytes.
 */
export function byteAxis(peak: number): ByteAxis {
  const top = Number.isFinite(peak) && peak > 0 ? peak : 1;
  let at = 0;
  while (at < BYTE_UNITS.length - 1 && top / 1024 ** at >= 1024) at += 1;
  // A maximum below one unit of the next size up stays in the smaller unit ("900 MiB").
  const size = 1024 ** at;
  const inUnit = top / size;
  const rough = inUnit / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((n) => n * magnitude).find((n) => n >= rough) ?? 10 * magnitude;
  const ticks = Math.max(1, Math.ceil(inUnit / step - 1e-9));
  const unit = BYTE_UNITS[at];
  return {
    unit,
    interval: step * size,
    max: ticks * step * size,
    label: (bytes) => `${formatCount(Math.round((bytes / size) * 100) / 100)}\u00A0${unit}`,
  };
}

function base(p: TrendPalette) {
  const text = { color: p.label, fontFamily: p.font };
  return {
    text,
    tooltip: {
      backgroundColor: p.tooltipBg,
      borderColor: p.tooltipBorder,
      textStyle: { color: p.tooltipText, fontFamily: p.font },
      extraCssText: "box-shadow: none;",
    },
    timeAxis: {
      type: "time" as const,
      axisLine: { lineStyle: { color: p.axis } },
      axisTick: { show: false },
      axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
    },
  };
}

/** The per-host lines of one metric, on one value axis. */
export function buildLinesOption(
  lines: HostLine[],
  kind: ValueKind,
  words: ChartWords,
  p: TrendPalette,
  range: { since: number; until: number },
  yMax?: number,
): EChartsOption {
  const colours = lineColours(p);
  const { text, tooltip, timeAxis } = base(p);
  const bytes =
    kind === "bytes"
      ? byteAxis(
          Math.max(
            0,
            ...lines.flatMap((line) =>
              line.data.map((point) => point[1]).filter((v): v is number => v !== null),
            ),
          ),
        )
      : null;
  return {
    animation: !prefersReducedMotion(),
    useUTC: true,
    textStyle: text,
    grid: { left: 64, right: 16, top: 28, bottom: 40 },
    tooltip: {
      ...tooltip,
      trigger: "axis",
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as Array<{
          seriesName?: string;
          value?: [number, number | null];
        }>;
        const first = list.find((item) => item.value && item.value[1] !== null);
        if (!first?.value) return "";
        const when = utcTimeHtml(new Date(first.value[0]).toISOString());
        const rows = list
          .filter((item) => item.value && item.value[1] !== null)
          .map((item) => {
            const line = lines.find((l) => l.host === item.seriesName);
            return words.tipValue(
              item.seriesName ?? "",
              item.value?.[1] as number,
              line?.mounts.get(item.value?.[0] as number) ?? null,
            );
          });
        return [when, ...rows].join("<br/>");
      },
    },
    xAxis: {
      ...timeAxis,
      min: range.since,
      max: range.until,
      name: words.xAxis,
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: text,
    },
    yAxis: {
      type: "value",
      name: words.yAxis,
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      ...(yMax !== undefined ? { max: yMax } : {}),
      ...(bytes ? { max: bytes.max, interval: bytes.interval } : {}),
      splitLine: { lineStyle: { color: p.grid } },
      axisLine: { lineStyle: { color: p.axis } },
      axisLabel: {
        ...text,
        formatter: (value: number) => (bytes ? bytes.label(value) : formatAxisValue(kind, value)),
      },
    },
    series: lines.map((line, at) => ({
      type: "line" as const,
      name: line.host,
      data: line.data,
      // Bridging a null would draw a value that was never measured.
      connectNulls: false,
      showSymbol: false,
      lineStyle: { color: colours[at], width: 1.5 },
      itemStyle: { color: colours[at] },
    })),
  };
}

/** Reachability as spans of time: up, down, or (a sample that did not say) unknown. */
export function buildReachabilityOption(
  segments: Segment[],
  words: { xAxis: string; up: string; down: string; unknown: string },
  p: TrendPalette,
  range: { since: number; until: number },
): EChartsOption {
  const { text, tooltip, timeAxis } = base(p);
  const colour = { up: p.ok, down: p.crit, unknown: p.gap } as const;
  const label = { up: words.up, down: words.down, unknown: words.unknown } as const;
  return {
    animation: !prefersReducedMotion(),
    useUTC: true,
    textStyle: text,
    grid: { left: 64, right: 16, top: 4, bottom: 36 },
    tooltip: {
      ...tooltip,
      trigger: "item",
      formatter: (params: unknown) => {
        const value = (params as { value?: [number, number, string] }).value;
        if (!value) return "";
        return [
          utcTimeHtml(new Date(value[0]).toISOString()),
          label[value[2] as keyof typeof label] ?? "",
        ].join("<br/>");
      },
    },
    xAxis: {
      ...timeAxis,
      min: range.since,
      max: range.until,
      name: words.xAxis,
      nameLocation: "middle",
      nameGap: 22,
      nameTextStyle: text,
    },
    yAxis: { type: "value", min: 0, max: 1, show: false },
    series: [
      {
        type: "custom",
        name: "Reachability",
        silent: false,
        data: segments.map((s) => [s.start, s.end, s.state]),
        renderItem: ((_params, api) => {
          const start = api.coord([api.value(0), 1]);
          const end = api.coord([api.value(1), 0]);
          const state = String(api.value(2)) as keyof typeof colour;
          return {
            type: "rect",
            shape: {
              x: start[0],
              y: start[1],
              width: Math.max(1, end[0] - start[0]),
              height: end[1] - start[1],
            },
            style: { fill: colour[state] },
          };
        }) as CustomSeriesRenderItem,
      },
    ],
  } as EChartsOption;
}

/** The latency line, with the reachability gap shown as a break. */
export function buildLatencyOption(
  data: SeriesPoint[],
  words: ChartWords,
  p: TrendPalette,
  range: { since: number; until: number },
): EChartsOption {
  const { text, tooltip, timeAxis } = base(p);
  return {
    animation: !prefersReducedMotion(),
    useUTC: true,
    textStyle: text,
    grid: { left: 64, right: 16, top: 28, bottom: 40 },
    tooltip: {
      ...tooltip,
      trigger: "axis",
      formatter: (params: unknown) => {
        const first = (Array.isArray(params) ? params : [params])[0] as
          | { value?: [number, number | null] }
          | undefined;
        if (!first?.value || first.value[1] === null) return "";
        return [
          utcTimeHtml(new Date(first.value[0]).toISOString()),
          words.tipValue("", first.value[1], null),
        ].join("<br/>");
      },
    },
    xAxis: {
      ...timeAxis,
      min: range.since,
      max: range.until,
      name: words.xAxis,
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: text,
    },
    yAxis: {
      type: "value",
      name: words.yAxis,
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      splitLine: { lineStyle: { color: p.grid } },
      axisLine: { lineStyle: { color: p.axis } },
      axisLabel: { ...text, formatter: (value: number) => formatCount(value) },
    },
    series: [
      {
        type: "line",
        name: "API latency",
        data,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: p.observed, width: 1.5 },
        itemStyle: { color: p.observed },
      },
    ],
  };
}
