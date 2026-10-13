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

import type { WafMetricBucket } from "@/lib/api";
import { readChartPalette } from "@/lib/chartPalette";
import { formatDateTime } from "@/lib/health";

import { bucketWidth } from "./wafFormat";
import { buildWafSeries, isolatedPoints, kindMeaning, wafUnobserved } from "./wafSeries";

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
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const points = useMemo(() => buildWafSeries(buckets), [buckets]);
  const unobserved = useMemo(() => wafUnobserved(buckets), [buckets]);
  const hasBand = expectedLow != null && expectedHigh != null;
  const hasLowerBound = points.some((p) => p.lowerBound);
  const hasCoverage = points.some((p) => p.coverage != null);
  const width = bucketWidth(buckets[0]?.bucket_seconds);
  const countAxis = width ? `Requests per ${width} (count)` : "Requests per bucket (count)";

  const buildOption = useMemo(() => {
    if (points.length === 0) return null;

    return (): EChartsOption => {
      const c = readChartPalette();
      const flat = (value: number | null) =>
        value == null ? [] : points.map((p) => [p.t, value] as [number, number]);

      const gapAreas: MarkAreaPair[] = unobserved.map(
        (u): MarkAreaPair => [
          { xAxis: u.start, itemStyle: { color: c.gap }, name: u.kind },
          { xAxis: u.end },
        ],
      );
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
      const quiet = { silent: true, label: { show: false } };

      return {
        animation: !reduceMotion,
        useUTC: true,
        textStyle: text,
        grid: [
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
            const observed =
              p.observed != null
                ? `Requests: ${p.lowerBound ? "≥ " : ""}${p.observed} ${unit}`
                : `Requests: not measured (${kindMeaning(p.kind)})`;
            const reported =
              p.observed == null && p.reported != null
                ? `Reported by the partial collection: ${p.reported} ${unit}`
                : "";
            const band = hasBand
              ? `Expected range at detection: ${expectedLow.toFixed(1)} – ${expectedHigh.toFixed(1)} ${unit}`
              : "";
            const coverage =
              p.coverage != null
                ? `Endpoint coverage: ${(p.coverage * 100).toFixed(1)}%`
                : "Endpoint coverage: not recorded";
            return [
              formatDateTime(new Date(p.t).toISOString()),
              observed,
              reported,
              expected != null ? `Expected: ${expected.toFixed(1)} ${unit}` : "",
              band,
              coverage,
              `Collection: ${kindMeaning(p.kind)}`,
            ]
              .filter(Boolean)
              .join("<br/>");
          },
        },
        xAxis: [
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
            ...axisCommon,
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
          {
            type: "value",
            gridIndex: 1,
            name: "Coverage (%)",
            nameTextStyle: { ...text, align: "left" },
            min: 0,
            max: 100,
            splitNumber: 2,
            splitLine: { lineStyle: { color: c.grid } },
            ...axisCommon,
          },
        ],
        series: [
          {
            type: "line",
            name: "Requests",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: points.map((p) => [p.t, p.observed] as [number, number | null]),
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
                    coord: [p.t, p.observed as number],
                  })),
                }
              : undefined,
            markArea:
              gapAreas.length + anomalyAreas.length
                ? { ...quiet, data: [...gapAreas, ...anomalyAreas] }
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
            data: flat(expected),
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
            data: hasBand ? flat(expectedLow) : [],
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
            data: hasBand ? flat(expectedHigh - expectedLow) : [],
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            lineStyle: { opacity: 0 },
            areaStyle: { color: c.band },
          },
          {
            type: "line",
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
            markArea: gapAreas.length ? { ...quiet, data: gapAreas } : undefined,
          },
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
      <div ref={containerRef} className="chart chart-waf" role="img" aria-label={ariaLabel} />
      {textSummary && <p className="sr-only">{textSummary}</p>}
      <ul className="chart-legend" aria-label="Chart key">
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          Requests
        </li>
        {expected != null && (
          <li>
            <span className="chart-key chart-key-expected" aria-hidden="true" />
            Expected median
          </li>
        )}
        {hasBand && (
          <li>
            <span className="chart-key chart-key-band" aria-hidden="true" />
            Expected range at detection
          </li>
        )}
        {hasCoverage && (
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
      </ul>
      <p className="subtitle chart-note">
        Gaps in the line are buckets that were partial or not stored. They are not zero requests.
        {hasLowerBound && " Some buckets are lower bounds (at least the plotted count)."}
      </p>
    </>
  );
}
