// The ECharts option of the property-watch series chart, as a pure function of a plot model and
// a palette. No colour is written here: every one arrives in `PwPalette`, read from the tokens.
// The honesty rules live in this builder so a test can read them off the option without a canvas:
//
//   * `connectNulls` is false on every series: a gap in the data is a gap on the screen;
//   * a measured zero is data at 0, an unknown or uncollected slot is `null`;
//   * a lower-bound point carries a hollow marker, and its tooltip line reads "at least n";
//   * the expected range is one band, built only when a single value is plotted;
//   * incident spans and uncollected spans are shaded;
//   * series colours are the five categorical tokens, never a status hue.
//
// Value text, watch names and property names are attacker-controlled (a username from a log
// line) and ECharts renders a tooltip formatter's string as HTML. Every such string goes through
// `format.encodeHTML` before it is joined into the tooltip (T-117).

import { format } from "echarts/core";

import { TIME_AXIS_FORMATTER } from "@/components/home/chartOptions";
import type { PwBucket } from "@/lib/api";
import { utcTimeHtml } from "@/components/ui/UtcTime";

import { eventsUnit, formatCoveragePercent, formatEvents } from "./pwFormat";
import { isolated, kindWords, type PlotModel, type PlotPoint, type Span } from "./pwSeries";

import type { EChartsOption } from "echarts";

/** The colours the chart reads from the tokens. `cat` is `--chart-cat-1` to `--chart-cat-5`. */
export interface PwPalette {
  cat: string[];
  grid: string;
  axis: string;
  label: string;
  band: string;
  gap: string;
  anomaly: string;
  history: string;
  marker: string;
  expected: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  font: string;
}

/** The token names `readPwPalette` reads, listed so a test can check each is declared. */
export const PW_CATEGORY_TOKENS = [
  "--chart-cat-1",
  "--chart-cat-2",
  "--chart-cat-3",
  "--chart-cat-4",
  "--chart-cat-5",
] as const;

/** One bucket's coverage, as the second panel plots it. */
export interface CoveragePoint {
  t: number;
  /** Parsed share as a fraction, `null` when it is not known. */
  ratio: number | null;
  /** True when the grouped search was cut: the share is "at least". */
  lowerBound: boolean;
  total: number | null;
  parsed: number | null;
  unparsed: number | null;
}

export interface CoverageModel {
  points: CoveragePoint[];
  /** `pw_min_coverage` as a fraction, drawn as one labelled line; `null` when not known. */
  floor: number | null;
}

/** Per-slot coverage from the stored buckets; a slot with no bucket is a gap. */
export function buildCoverage(
  slots: number[],
  buckets: PwBucket[],
  floor: number | null,
): CoverageModel {
  const byTime = new Map<number, PwBucket>();
  for (const b of buckets) byTime.set(Date.parse(b.bucket_start), b);
  return {
    floor,
    points: slots.map((t): CoveragePoint => {
      const b = byTime.get(t);
      if (!b) return { t, ratio: null, lowerBound: false, total: null, parsed: null, unparsed: null };
      // A share needs a whole totals search and a non-empty scope: an empty scope has no share.
      const known =
        b.totals_complete && b.total_events !== null && b.total_events > 0 && b.parsed_events !== null;
      return {
        t,
        ratio: known ? Math.min(1, (b.parsed_events as number) / (b.total_events as number)) : null,
        lowerBound: b.values_measurement === "LOWER_BOUND",
        total: b.total_events,
        parsed: b.parsed_events,
        unparsed: b.unparsed_events,
      };
    }),
  };
}

const enc = (text: string) => format.encodeHTML(text);

function pointLine(name: string, p: PlotPoint, unit: string): string {
  const label = enc(name);
  const origin = p.origin === "HISTORY" ? "; read from QRadar history, not judged" : "";
  switch (p.kind) {
    case "EXACT":
    case "MEASURED_ZERO":
      return `${label}: ${formatEvents(p.value)} ${unit} (${kindWords(p.kind)}${origin})`;
    case "LOWER_BOUND":
      return `${label}: ${formatEvents(p.value, "LOWER_BOUND")} ${unit} (${kindWords(p.kind)}${origin})`;
    default:
      return `${label}: not plotted, ${kindWords(p.kind)}${origin}`;
  }
}

/**
 * The tooltip of one time slot. Every line that contains a value, a watch name or a property
 * name is encoded; the numbers and the fixed words are not attacker-controlled.
 */
export function pwTooltip(
  model: PlotModel,
  index: number,
  opts: { unit: string; coverage?: CoverageModel | null },
): string {
  const t = model.slots[index];
  if (t === undefined) return "";
  const lines = [utcTimeHtml(new Date(t).toISOString())];
  for (const line of model.lines) {
    const p = line.points[index];
    if (!p) continue;
    lines.push(pointLine(line.label, p, opts.unit));
  }
  const single = model.lines.length === 1 ? model.lines[0].points[index] : null;
  if (single) {
    if (single.low !== null && single.high !== null) {
      lines.push(
        `Expected range for this hour of the week: ${formatEvents(single.low)} to ${formatEvents(single.high)} ${opts.unit}`,
      );
      if (single.median !== null) lines.push(`Expected median: ${single.median.toFixed(1)} ${opts.unit}`);
    } else if (single.kind !== "NOT_COLLECTED") {
      lines.push(
        single.baseline === "LEARNING"
          ? "Expected range: learning, not yet reliable"
          : "Expected range: no baseline for this hour",
      );
    }
  }
  const cov = opts.coverage?.points[index];
  if (cov) {
    if (cov.ratio === null) {
      lines.push("Parsed share: not known for this bucket");
    } else {
      const share = formatCoveragePercent(cov.ratio);
      lines.push(`Parsed share: ${cov.lowerBound ? "at least " : ""}${share}`);
      if (cov.unparsed !== null) {
        lines.push(
          `Unparsed remainder: ${cov.lowerBound ? "at most " : ""}${formatEvents(cov.unparsed)} ${opts.unit}`,
        );
      }
    }
  }
  return lines.join("<br/>");
}

type MarkAreaPair = [
  { xAxis: number; yAxis?: number; itemStyle?: { color: string }; name?: string },
  { xAxis: number; yAxis?: number },
];

function areas(spans: Span[], color: string, name: string): MarkAreaPair[] {
  return spans.map((s): MarkAreaPair => [
    { xAxis: s.start, itemStyle: { color }, name },
    { xAxis: s.end },
  ]);
}

export interface PwOptionInput {
  model: PlotModel;
  palette: PwPalette;
  /** Incident spans; an open one ends at the last slot. */
  incidents?: Array<{ start: string; end: string | null }>;
  /**
   * The expected range the detector recorded at detection, drawn as one rectangle over the
   * incident's span (an open one ends at the last slot). Never three lines.
   */
  detectionBand?: DetectionBand | null;
  coverage?: CoverageModel | null;
  compact?: boolean;
  reduceMotion?: boolean;
}

export interface DetectionBand {
  start: string;
  end: string | null;
  low: number;
  high: number;
}

/** The option for the series chart, with the coverage panel beneath it on the same time axis. */
export function buildPwOption(input: PwOptionInput): EChartsOption {
  const { model, palette: c, coverage = null, compact = false } = input;
  const bucketSeconds = model.bucketMs / 1000;
  const unit = eventsUnit(bucketSeconds);
  const showCoverage = coverage !== null && !compact;
  // With no value plotted the parsed share still stands alone: a property that stopped parsing
  // must be visible as a coverage collapse, not as an empty chart.
  const mainPanel = model.lines.length > 0;
  const covIndex = mainPanel ? 1 : 0;
  const text = { color: c.label, fontFamily: c.font };
  const axisCommon = { axisLine: { lineStyle: { color: c.axis } }, axisLabel: text };
  const last = model.slots[model.slots.length - 1];

  const incidentSpans: Span[] = (input.incidents ?? []).map((i) => ({
    start: Date.parse(i.start),
    end: i.end ? Date.parse(i.end) : last,
  }));
  const gapAreas = areas(model.uncollected, c.gap, "not collected");
  const incidentAreas = areas(incidentSpans, c.anomaly, "incident");
  const historyAreas = areas(model.history, c.history, "read from QRadar history, not judged");
  const detected = input.detectionBand ?? null;
  const detectionAreas: MarkAreaPair[] =
    detected && model.lines.length > 0
      ? [
          [
            {
              xAxis: Date.parse(detected.start),
              yAxis: detected.low,
              itemStyle: { color: c.band },
              name: "expected range at detection",
            },
            { xAxis: detected.end ? Date.parse(detected.end) : last, yAxis: detected.high },
          ],
        ]
      : [];

  const single = model.lines.length === 1 ? model.lines[0] : null;
  const band = model.hasBand && single !== null;

  const lineSeries = model.lines.map((line, i) => ({
    type: "line" as const,
    name: line.label,
    xAxisIndex: 0,
    yAxisIndex: 0,
    // A gap is `null`; a measured zero is 0. A lower-bound point and a lone measured point
    // carry a marker, because a bound must not read as a plain count and a line cannot show one
    // point.
    data: line.points.map((p, k) => ({
      value: [p.t, p.value] as [number, number | null],
      symbol: p.kind === "LOWER_BOUND" ? "emptyCircle" : isolated(line.points, k) ? "circle" : "none",
      symbolSize: 6,
    })),
    connectNulls: false,
    showSymbol: true,
    symbol: "none",
    lineStyle: { color: c.cat[i % c.cat.length], width: 1.5 },
    itemStyle: { color: c.cat[i % c.cat.length] },
    z: 3,
    markArea:
      i === 0 && gapAreas.length + incidentAreas.length + historyAreas.length + detectionAreas.length > 0
        ? {
            silent: true,
            label: { show: false },
            data: [...historyAreas, ...gapAreas, ...incidentAreas, ...detectionAreas],
          }
        : undefined,
  }));

  const bandSeries =
    band && single
      ? [
          {
            type: "line" as const,
            name: "Expected low",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: single.points.map((p) => [p.t, p.low !== null && p.high !== null ? p.low : null]),
            connectNulls: false,
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            step: "end" as const,
            lineStyle: { opacity: 0 },
          },
          {
            type: "line" as const,
            name: "Expected range",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: single.points.map((p) => [
              p.t,
              p.low !== null && p.high !== null ? p.high - p.low : null,
            ]),
            connectNulls: false,
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            step: "end" as const,
            lineStyle: { opacity: 0 },
            areaStyle: { color: c.band },
          },
        ]
      : [];

  const coverageSeries =
    showCoverage && coverage
      ? [
          {
            type: "line" as const,
            name: "Parsed share",
            xAxisIndex: covIndex,
            yAxisIndex: covIndex,
            data: coverage.points.map((p) => [p.t, p.ratio === null ? null : p.ratio * 100]),
            connectNulls: false,
            showSymbol: false,
            lineStyle: { color: c.expected, width: 1.5 },
            itemStyle: { color: c.expected },
            markArea: gapAreas.length
              ? { silent: true, label: { show: false }, data: gapAreas }
              : undefined,
            markLine:
              coverage.floor === null
                ? undefined
                : {
                    silent: true,
                    symbol: "none",
                    lineStyle: { color: c.marker, type: "dashed" as const },
                    label: {
                      show: true,
                      position: "insideEndTop" as const,
                      formatter: `Floor ${Math.round(coverage.floor * 100)} %`,
                      color: c.label,
                      fontFamily: c.font,
                    },
                    data: [{ yAxis: coverage.floor * 100 }],
                  },
          },
        ]
      : [];

  return {
    animation: !input.reduceMotion,
    useUTC: true,
    textStyle: text,
    grid:
      showCoverage && mainPanel
        ? [
            { left: 64, right: 24, top: 36, height: 190 },
            { left: 64, right: 24, top: 262, height: 72 },
          ]
        : showCoverage
          ? [{ left: 64, right: 24, top: 36, height: 120 }]
          : [{ left: 64, right: 24, top: 36, bottom: 38 }],
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.tooltipBorder,
      textStyle: { color: c.tooltipText, fontFamily: c.font },
      extraCssText: "box-shadow: none;",
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params];
        const first = list[0] as { dataIndex?: number } | undefined;
        if (first?.dataIndex == null) return "";
        return pwTooltip(model, first.dataIndex, { unit, coverage: showCoverage ? coverage : null });
      },
    },
    xAxis: showCoverage && !mainPanel
      ? [
          {
            type: "time",
            gridIndex: 0,
            name: "Time (UTC)",
            nameLocation: "middle",
            nameGap: 30,
            nameTextStyle: text,
            ...axisCommon,
            axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
          },
        ]
      : showCoverage
      ? [
          { type: "time", gridIndex: 0, ...axisCommon, axisLabel: { show: false } },
          {
            type: "time",
            gridIndex: 1,
            name: "Time (UTC)",
            nameLocation: "middle",
            nameGap: 30,
            nameTextStyle: text,
            ...axisCommon,
            axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
          },
        ]
      : [
          {
            type: "time",
            gridIndex: 0,
            name: "Time (UTC)",
            nameLocation: "middle",
            nameGap: 24,
            nameTextStyle: text,
            ...axisCommon,
            axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
          },
        ],
    yAxis: [
      ...(mainPanel ? [{
        type: "value" as const,
        gridIndex: 0,
        name: unit,
        nameTextStyle: { ...text, align: "left" as const },
        min: 0,
        splitLine: { lineStyle: { color: c.grid } },
        ...axisCommon,
      }] : []),
      ...(showCoverage
        ? [
            {
              type: "value" as const,
              gridIndex: covIndex,
              name: "Parsed share (%)",
              nameTextStyle: { ...text, align: "left" as const },
              min: 0,
              max: 100,
              splitNumber: 2,
              splitLine: { lineStyle: { color: c.grid } },
              ...axisCommon,
            },
          ]
        : []),
    ],
    series: [...lineSeries, ...bandSeries, ...coverageSeries],
  };
}
