// The plot model of one catalogue event ID over a window, and its ECharts option.
//
// Honesty rules, each pinned by a test and readable off the option without a canvas:
//   * a slot with no whole count is `null`, never 0, and `connectNulls` is false;
//   * a slot the collector did not cover (NOT_COLLECTED, or no stored row) is shaded as not
//     collected, so a quiet-looking line cannot hide a window nothing was measured in;
//   * a truncated slot is plotted at its floor with a hollow marker and its tooltip says "at least";
//   * a measured 0 is data at 0;
//   * no expected range is drawn: the API serves no baseline for identity counts.

import { format } from "echarts/core";

import { TIME_AXIS_FORMATTER } from "@/components/home/chartOptions";
import { utcTimeHtml } from "@/components/ui/UtcTime";
import type { IdentitySeriesPoint } from "@/lib/api";
import { formatCount } from "@/lib/format";

import { pointMeasurement } from "./identityFormat";

import type { EChartsOption } from "echarts";

export type SlotKind = "EXACT" | "TRUNCATED" | "NOT_COLLECTED";

export interface Slot {
  t: number;
  value: number | null;
  kind: SlotKind;
  /** The name search of the slot was cut or did not run. */
  namesCut: boolean;
}

export interface Span {
  start: number;
  end: number;
}

export interface SeriesPlot {
  bucketMs: number;
  slots: Slot[];
  uncollected: Span[];
  /** Runs of slots plotted at their floor: the search was cut. */
  floor: Span[];
}

/** Contiguous runs of slots of one kind, each ending where the next slot would start. */
function runs(slots: Slot[], bucketMs: number, kind: SlotKind): Span[] {
  const out: Span[] = [];
  let open: number | null = null;
  slots.forEach((s, i) => {
    if (s.kind === kind) {
      if (open === null) open = s.t;
    } else if (open !== null) {
      out.push({ start: open, end: slots[i - 1].t + bucketMs });
      open = null;
    }
  });
  if (open !== null && slots.length > 0) out.push({ start: open, end: slots[slots.length - 1].t + bucketMs });
  return out;
}

export function buildSeriesPlot(
  series: IdentitySeriesPoint[],
  eventId: number,
  startMs: number,
  endMs: number,
  bucketSeconds: number,
): SeriesPlot {
  const bucketMs = bucketSeconds * 1000;
  const byTime = new Map<number, IdentitySeriesPoint>();
  for (const p of series) if (p.event_id === eventId) byTime.set(Date.parse(p.bucket_start), p);
  const slots: Slot[] = [];
  for (let t = startMs; t < endMs && bucketMs > 0; t += bucketMs) {
    const p = byTime.get(t);
    if (!p) {
      slots.push({ t, value: null, kind: "NOT_COLLECTED", namesCut: false });
      continue;
    }
    const kind = pointMeasurement(p);
    slots.push({
      t,
      value: kind === "NOT_COLLECTED" ? null : p.event_count,
      kind,
      namesCut: p.names_measurement !== "EXACT" || p.admission_truncated === true,
    });
  }
  const uncollected = runs(slots, bucketMs, "NOT_COLLECTED");
  const floor = runs(slots, bucketMs, "TRUNCATED");
  return { bucketMs, slots, uncollected, floor };
}

/** Slots by kind, for the text summary. */
export function plotCounts(plot: SeriesPlot): { exact: number; truncated: number; notCollected: number } {
  const n = { exact: 0, truncated: 0, notCollected: 0 };
  for (const s of plot.slots) {
    if (s.kind === "EXACT") n.exact += 1;
    else if (s.kind === "TRUNCATED") n.truncated += 1;
    else n.notCollected += 1;
  }
  return n;
}

export interface IdentityPalette {
  line: string;
  grid: string;
  axis: string;
  label: string;
  gap: string;
  /** The neutral tint of a run of slots at their floor. */
  floor: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  font: string;
}

export interface IdentityChartWords {
  unit: string;
  axisTime: string;
  notCollected: string;
  atFloor: string;
  /** Tooltip lines. */
  count: (value: string) => string;
  atLeast: (value: string) => string;
  noCount: string;
  namesCut: string;
}

const enc = (text: string) => format.encodeHTML(text);

export function slotTooltip(plot: SeriesPlot, index: number, words: IdentityChartWords): string {
  const slot = plot.slots[index];
  if (!slot) return "";
  const lines = [utcTimeHtml(new Date(slot.t).toISOString())];
  if (slot.kind === "EXACT" && slot.value !== null) {
    lines.push(enc(words.count(formatCount(slot.value, "en"))));
  } else if (slot.kind === "TRUNCATED" && slot.value !== null) {
    lines.push(enc(words.atLeast(formatCount(slot.value, "en"))));
  } else {
    lines.push(enc(words.noCount));
  }
  if (slot.namesCut && slot.kind !== "NOT_COLLECTED") lines.push(enc(words.namesCut));
  return lines.join("<br/>");
}

export function buildIdentityOption(
  plot: SeriesPlot,
  palette: IdentityPalette,
  words: IdentityChartWords,
  reduceMotion: boolean,
): EChartsOption {
  const c = palette;
  const text = { color: c.label, fontFamily: c.font };
  const axisCommon = { axisLine: { lineStyle: { color: c.axis } }, axisLabel: text };
  return {
    animation: !reduceMotion,
    useUTC: true,
    textStyle: text,
    grid: { left: 64, right: 24, top: 36, bottom: 44 },
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.tooltipBorder,
      textStyle: { color: c.tooltipText, fontFamily: c.font },
      extraCssText: "box-shadow: none;",
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const first = list[0] as { dataIndex?: number } | undefined;
        return first?.dataIndex == null ? "" : slotTooltip(plot, first.dataIndex, words);
      },
    },
    xAxis: {
      type: "time",
      name: words.axisTime,
      nameLocation: "middle",
      nameGap: 30,
      nameTextStyle: text,
      ...axisCommon,
      axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
    },
    yAxis: {
      type: "value",
      name: words.unit,
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      splitNumber: 3,
      splitLine: { lineStyle: { color: c.grid } },
      axisLine: { show: false },
      axisLabel: text,
    },
    series: [
      {
        type: "line",
        name: words.unit,
        data: plot.slots.map((s, i) => {
          const alone =
            s.value !== null &&
            (i === 0 || plot.slots[i - 1].value === null) &&
            (i === plot.slots.length - 1 || plot.slots[i + 1].value === null);
          return {
            value: [s.t, s.value] as [number, number | null],
            // A lone floor keeps a hollow marker; a run of them is shaded instead of marked every point.
            symbol: alone ? (s.kind === "TRUNCATED" ? "emptyCircle" : "circle") : "none",
            symbolSize: 6,
          };
        }),
        connectNulls: false,
        showSymbol: true,
        symbol: "none",
        lineStyle: { color: c.line, width: 1.5 },
        itemStyle: { color: c.line },
        markArea:
          plot.uncollected.length + plot.floor.length > 0
            ? {
                silent: true,
                label: { show: false },
                data: [
                  ...plot.floor.map((r) => [
                    { xAxis: r.start, itemStyle: { color: c.floor }, name: words.atFloor },
                    { xAxis: r.end },
                  ]),
                  ...plot.uncollected.map((r) => [
                    { xAxis: r.start, itemStyle: { color: c.gap }, name: words.notCollected },
                    { xAxis: r.end },
                  ]),
                ],
              }
            : undefined,
      },
    ],
  } as EChartsOption;
}
