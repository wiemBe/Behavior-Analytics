"use client";

// An endpoint's requests against its expected range, with endpoint coverage
// beneath on its own axis.
//
// Two stacked panels sharing one UTC time axis, not one chart with two y-axes:
// requests are a count per bucket and coverage is a percentage, and a dual-axis
// plot invites reading one against the other's scale. Each panel has its own
// titled axis with its unit.
//
// The honesty rules are Phase A's (`VolumeChart`), each with a test:
//   * a bucket that was not fully observed is a null on the line, never its
//     reported value, and `connectNulls` is false;
//   * an interval with no stored bucket is shaded as not collected, never zero;
//   * the expected range is one band (the detector's range at detection) with
//     the expected median as a single dashed line;
//   * no marker on every point; only a lone measured bucket between gaps is
//     marked, because a line cannot show one point;
//   * every colour comes from the tokens through `readChartPalette`.

import { LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import { HistoryKey, HistoryText } from "@/components/behavior/HistoryKey";
import {
  historyIntervals,
  historyMarkAreas,
  originByTime,
  originText,
  readHistoryShade,
  withoutLabels,
} from "@/components/behavior/historyOrigin";
import { TIME_AXIS_FORMATTER } from "@/components/home/chartOptions";
import type { WafMetricBucket } from "@/lib/api";
import { readChartPalette } from "@/lib/chartPalette";
import { formatDateTime } from "@/lib/health";

import { buildBaselineLookup, type BaselineSource } from "./wafBaseline";
import { bucketWidth } from "./wafFormat";
import {
  buildWafRatioSeries,
  buildWafSeries,
  isolatedPoints,
  kindMeaning,
  wafRatioUnobserved,
  wafUnobserved,
  type RatioSpec,
} from "./wafSeries";

import type { EChartsOption } from "echarts";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

type MarkAreaPair = [
  { xAxis: number; itemStyle?: { color: string }; name?: string },
  { xAxis: number },
];

export interface WafInterval {
  start: string;
  end: string | null;
}

export interface WafTransition {
  at: string;
  label: string;
}

export function WafRequestChart({
  buckets,
  expected,
  expectedLow,
  expectedHigh,
  anomalies = [],
  transitions = [],
  ariaLabel = "Requests against the expected range, and endpoint coverage",
  textSummary,
  ratio = null,
  baseline,
  compact = false,
  historyLabel = !compact,
}: {
  buckets: WafMetricBucket[];
  expected: number | null;
  expectedLow: number | null;
  expectedHigh: number | null;
  anomalies?: WafInterval[];
  transitions?: WafTransition[];
  ariaLabel?: string;
  /** The chart as prose, for readers who cannot see a canvas. */
  textSummary?: string;
  /**
   * A proportion detector's quantity. When set the primary panel plots that
   * proportion in %, and `expected`, `expectedLow` and `expectedHigh` are
   * fractions. The request count moves to the hover readout.
   */
  ratio?: RatioSpec | null;
  /**
   * The seasonal cells, looked up per bucket by UTC weekday and hour. When
   * given, the band and the expected median follow it, and a bucket with no
   * reliable cell has none. When absent (the cells could not be loaded) the
   * band is the flat range recorded at detection, and is labelled so.
   */
  baseline?: BaselineSource | null;
  /**
   * The dashboard widget's form: the requests panel alone, filling its container, with the
   * coverage panel and the long note left out (the note stays for assistive technology). The
   * honesty rules above are unchanged.
   */
  compact?: boolean;
  /** The history words inside the chart. Default: the full-size chart has them, the tile does not. */
  historyLabel?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const points = useMemo(
    () => (ratio ? buildWafRatioSeries(buckets, ratio) : buildWafSeries(buckets)),
    [buckets, ratio],
  );
  const unobserved = useMemo(
    () => (ratio ? wafRatioUnobserved(buckets, ratio) : wafUnobserved(buckets)),
    [buckets, ratio],
  );
  const history = useMemo(() => historyIntervals(buckets), [buckets]);
  const origins = useMemo(() => originByTime(buckets), [buckets]);
  const scale = ratio ? 100 : 1;
  const seasonal = baseline != null;
  const lookup = useMemo(
    () =>
      baseline ? buildBaselineLookup(baseline.cells, baseline.metric, baseline.normalizerVersion) : null,
    [baseline],
  );
  // Per bucket, from the baseline cell of that bucket's weekday and hour. A
  // gap point, and a bucket whose cell is missing or unreliable, has none.
  const cells = useMemo(
    () => (lookup ? points.map((p) => (p.kind === "GAP" ? null : lookup(p.t))) : []),
    [lookup, points],
  );
  const hasBand = seasonal
    ? cells.some((c) => c?.low != null && c.high != null)
    : expectedLow != null && expectedHigh != null;
  const hasMedian = seasonal && !ratio ? cells.some((c) => c != null) : expected != null;
  const hasLowerBound = !ratio && points.some((p) => p.lowerBound);
  const hasCoverage = points.some((p) => p.coverage != null);
  const width = bucketWidth(buckets[0]?.bucket_seconds);
  const countAxis = ratio
    ? `${ratio.label} (%)`
    : width
      ? `Requests per ${width} (count)`
      : "Requests per bucket (count)";
  const bandLabel = seasonal
    ? ratio
      ? "Seasonal p05–p95 of bucket proportions"
      : "Expected range"
    : "Expected range at detection";
  const medianLabel = ratio ? "Expected at detection" : "Expected median";

  const buildOption = useMemo(() => {
    if (points.length === 0) return null;

    return (): EChartsOption => {
      const c = readChartPalette();
      const flat = (value: number | null) =>
        value == null ? [] : points.map((p) => [p.t, value * scale] as [number, number]);
      const perBucket = (pick: (c: NonNullable<(typeof cells)[number]>) => number | null) =>
        points.map((p, i) => {
          const c = cells[i];
          const v = c ? pick(c) : null;
          return [p.t, v == null ? null : v * scale] as [number, number | null];
        });

      const gapAreas: MarkAreaPair[] = unobserved.map(
        (u): MarkAreaPair => [
          { xAxis: u.start, itemStyle: { color: c.gap }, name: u.kind },
          { xAxis: u.end },
        ],
      );
      const historyAreas = historyMarkAreas(history, readHistoryShade(), historyLabel);
      const anomalyAreas: MarkAreaPair[] = anomalies.map(
        (a): MarkAreaPair => [
          { xAxis: Date.parse(a.start), itemStyle: { color: c.anomaly }, name: "anomaly" },
          // Still running: shade to the last point rather than implying it closed.
          { xAxis: a.end ? Date.parse(a.end) : points[points.length - 1].t },
        ],
      );

      const reduceMotion =
        typeof window !== "undefined" &&
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      const lone = isolatedPoints(points);
      const text = { color: c.label, fontFamily: c.font };
      const axisCommon = { axisLine: { lineStyle: { color: c.axis } }, axisLabel: text };
      const timeAxisCommon = {
        ...axisCommon,
        axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
      };
      const quiet = { silent: true, label: { show: false } };

      return {
        animation: !reduceMotion,
        useUTC: true,
        textStyle: text,
        grid: compact
          ? [{ left: 56, right: 16, top: 28, bottom: 38 }]
          : [
              { left: 64, right: 24, top: 40, height: 190 },
              { left: 64, right: 24, top: 268, height: 56 },
            ],
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
            const p = first?.dataIndex != null ? points[first.dataIndex] : undefined;
            if (!p) return "";
            const unit = width ? `requests / ${width}` : "requests / bucket";
            const fmt = (v: number) => (ratio ? `${(v * scale).toFixed(1)}%` : v.toFixed(1));
            const suffix = ratio ? "" : ` ${unit}`;
            const cell = seasonal && points[first!.dataIndex!].kind !== "GAP" ? cells[first!.dataIndex!] : null;
            const requests =
              p.reported != null
                ? `${p.lowerBound ? "≥ " : ""}${p.reported} ${unit}`
                : "not recorded";
            const observed = ratio
              ? p.observed != null
                ? `${ratio.label}: ${(p.observed * 100).toFixed(1)}%`
                : `${ratio.label}: not measured (${kindMeaning(p.kind)})`
              : p.observed != null
                ? `Requests: ${p.lowerBound ? "≥ " : ""}${p.observed} ${unit}`
                : `Requests: not measured (${kindMeaning(p.kind)})`;
            const reported =
              !ratio && p.observed == null && p.reported != null
                ? `Reported by the partial collection: ${p.reported} ${unit}`
                : "";
            const requestLine = ratio ? `Requests: ${requests}` : "";
            let band = "";
            let median = "";
            if (seasonal) {
              band =
                cell?.low != null && cell.high != null
                  ? `${bandLabel}: ${fmt(cell.low)} – ${fmt(cell.high)}${suffix}`
                  : `${bandLabel}: no reliable baseline for this weekday and hour`;
              median = cell && !ratio ? `Expected median: ${fmt(cell.median)}${suffix}` : "";
            } else if (hasBand) {
              band = `${bandLabel}: ${fmt(expectedLow!)} – ${fmt(expectedHigh!)}${suffix}`;
            }
            const atDetection =
              expected != null && (!seasonal || ratio)
                ? `${medianLabel}: ${fmt(expected)}${suffix}`
                : "";
            const coverage =
              p.coverage != null
                ? `Endpoint coverage: ${(p.coverage * 100).toFixed(1)}%`
                : "Endpoint coverage: not recorded";
            return [
              formatDateTime(new Date(p.t).toISOString()),
              observed,
              reported,
              requestLine,
              median,
              atDetection,
              band,
              coverage,
              `Collection: ${kindMeaning(p.kind)}`,
              originText(origins.get(p.t)),
            ]
              .filter(Boolean)
              .join("<br/>");
          },
        },
        xAxis: compact
          ? [
              {
                type: "time",
                gridIndex: 0,
                name: "Time (UTC)",
                nameLocation: "middle",
                nameGap: 24,
                nameTextStyle: text,
                ...timeAxisCommon,
              },
            ]
          : [
              {
                type: "time",
                gridIndex: 0,
                ...axisCommon,
                axisLabel: { show: false },
              },
              {
                type: "time",
                gridIndex: 1,
                name: "Time (UTC)",
                nameLocation: "middle",
                nameGap: 30,
                nameTextStyle: text,
                ...timeAxisCommon,
              },
            ],
        yAxis: [
          {
            type: "value",
            gridIndex: 0,
            name: countAxis,
            nameTextStyle: { ...text, align: "left" },
            min: 0,
            splitLine: { lineStyle: { color: c.grid } },
            ...axisCommon,
          },
          ...(compact
            ? []
            : [
                {
                  type: "value" as const,
                  gridIndex: 1,
                  name: "Coverage (%)",
                  nameTextStyle: { ...text, align: "left" as const },
                  min: 0,
                  max: 100,
                  splitNumber: 2,
                  splitLine: { lineStyle: { color: c.grid } },
                  ...axisCommon,
                },
              ]),
        ],
        series: [
          {
            type: "line",
            name: "Requests",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: points.map((p) => [p.t, p.observed == null ? null : p.observed * scale] as [number, number | null]),
            // Bridging a null would draw traffic that was never observed.
            connectNulls: false,
            showSymbol: false,
            lineStyle: { color: c.observed, width: 1.5 },
            itemStyle: { color: c.observed },
            z: 3,
            markPoint: lone.length
              ? {
                  silent: true,
                  symbol: "circle",
                  symbolSize: 5,
                  itemStyle: { color: c.observed },
                  label: { show: false },
                  data: lone.map((p) => ({
                    name: "Measured interval between gaps",
                    coord: [p.t, (p.observed as number) * scale],
                  })),
                }
              : undefined,
            markArea:
              gapAreas.length + anomalyAreas.length + historyAreas.length
                ? { ...quiet, data: [...gapAreas, ...historyAreas, ...anomalyAreas] }
                : undefined,
            markLine: transitions.length
              ? {
                  ...quiet,
                  symbol: "none",
                  lineStyle: { color: c.marker, type: "dashed" },
                  data: transitions.map((t) => ({ xAxis: Date.parse(t.at), name: t.label })),
                }
              : undefined,
          },
          {
            type: "line",
            name: "Expected requests",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data:
              seasonal && !ratio ? perBucket((v) => v.median) : flat(expected),
            ...(seasonal && !ratio ? { step: "end" as const } : {}),
            connectNulls: false,
            showSymbol: false,
            silent: true,
            lineStyle: { color: c.expected, type: "dashed", width: 1 },
            itemStyle: { color: c.expected },
          },
          // The expected range as one band: an invisible floor at the lower
          // bound, and a filled series stacked on it as wide as the range.
          {
            type: "line",
            name: "Expected low",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: !hasBand ? [] : seasonal ? perBucket((v) => v.low) : flat(expectedLow),
            ...(seasonal ? { step: "end" as const } : {}),
            connectNulls: false,
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            lineStyle: { opacity: 0 },
          },
          {
            type: "line",
            name: "Expected range",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: !hasBand
              ? []
              : seasonal
                ? perBucket((v) => (v.low != null && v.high != null ? v.high - v.low : null))
                : flat(expectedHigh! - expectedLow!),
            ...(seasonal ? { step: "end" as const } : {}),
            connectNulls: false,
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            lineStyle: { opacity: 0 },
            areaStyle: { color: c.band },
          },
          ...(compact
            ? []
            : [
                {
                  type: "line" as const,
                  name: "Endpoint coverage",
                  xAxisIndex: 1,
                  yAxisIndex: 1,
                  data: points.map(
                    (p) => [p.t, p.coverage == null ? null : p.coverage * 100] as [number, number | null],
                  ),
                  connectNulls: false,
                  showSymbol: false,
                  lineStyle: { color: c.expected, width: 1.5 },
                  itemStyle: { color: c.expected },
                  markArea:
                    gapAreas.length + historyAreas.length
                      ? { ...quiet, data: [...gapAreas, ...withoutLabels(historyAreas)] }
                      : undefined,
                },
              ]),
        ],
      };
    };
  }, [
    points,
    unobserved,
    expected,
    expectedLow,
    expectedHigh,
    hasBand,
    anomalies,
    transitions,
    width,
    countAxis,
    ratio,
    scale,
    seasonal,
    cells,
    bandLabel,
    medianLabel,
    compact,
    history,
    origins,
    historyLabel,
  ]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !buildOption) return;

    const chart = echarts.init(el);
    chart.setOption(buildOption());

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);

    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [buildOption]);

  if (points.length === 0) {
    return (
      <div className="notice">
        No request buckets were stored for this endpoint in this window. This is an absence of
        collection, not an observation of zero requests.
      </div>
    );
  }

  return (
    <>
      <div
        ref={containerRef}
        className={compact ? "hc-chart cw-tall" : "chart chart-waf"}
        role="img"
        aria-label={ariaLabel}
      />
      {textSummary && <p className="sr-only">{textSummary}</p>}
      <HistoryText rows={buckets} />
      <ul className="chart-legend" aria-label="Chart key">
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          {ratio ? ratio.label : "Requests"}
        </li>
        {hasMedian && (
          <li>
            <span className="chart-key chart-key-expected" aria-hidden="true" />
            {medianLabel}
          </li>
        )}
        {hasBand && (
          <li>
            <span className="chart-key chart-key-band" aria-hidden="true" />
            {bandLabel}
          </li>
        )}
        {hasCoverage && !compact && (
          <li>
            <span className="chart-key chart-key-coverage" aria-hidden="true" />
            Endpoint coverage
          </li>
        )}
        {unobserved.length > 0 && (
          <li>
            <span className="chart-key chart-key-gap" aria-hidden="true" />
            Not fully collected
          </li>
        )}
        {anomalies.length > 0 && (
          <li>
            <span className="chart-key chart-key-anomaly" aria-hidden="true" />
            Anomalous interval
          </li>
        )}
        {transitions.length > 0 && (
          <li>
            <span className="chart-key chart-key-rule" aria-hidden="true" />
            Lifecycle transition
          </li>
        )}
        {history.length > 0 && <HistoryKey />}
      </ul>
      <p className={compact ? "sr-only" : "subtitle chart-note"}>
        {ratio
          ? "Gaps in the line are buckets whose breakdown was not collected, failed, hit its cap or had no requests to divide. They are not 0 %."
          : "Gaps in the line are buckets that were partial or not stored. They are not zero requests."}
        {hasLowerBound && " Some buckets are lower bounds (at least the plotted count)."}
        {seasonal &&
          " The band follows the baseline cell of each bucket's UTC weekday and hour, and is absent where that cell is missing or not yet reliable."}
      </p>
    </>
  );
}
