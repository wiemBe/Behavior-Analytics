"use client";

// Observed volume against its expected baseline band, with the anomaly
// interval and lifecycle transitions overlaid.
//
// ECharts is imported here and nowhere else on these routes: it touches
// `document` and `ResizeObserver` on construction, so it must stay out of any
// server component. The pages fetch their data server-side and pass it in.
//
// The chart's honesty rules, all of which have a test:
//   * A bucket that was not fully observed is a null, never its reported value.
//   * `connectNulls` is false, so an uncollected interval stays a visible gap
//     rather than a straight line asserting interpolatable traffic.
//   * Unobserved intervals are shaded so a gap cannot be mistaken for silence.
//
// Drawing rules: the observed series is a plain line with no marker on every
// point (only a lone measured bucket between gaps is marked, because a line
// cannot show one point); the expected range is one neutral band with the
// expected median as a single dashed line; every colour comes from the design
// tokens through `readChartPalette`, never from a literal here.

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

import type { MetricBucket } from "@/lib/api";
import { readChartPalette } from "@/lib/chartPalette";
import { formatDateTime } from "@/lib/health";
import {
  buildSeries,
  isolatedPoints,
  kindMeaning,
  unobservedIntervals,
} from "@/lib/timeseries";

import type { EChartsOption } from "echarts";

// Modular registration rather than the full `echarts` bundle: this needs one
// chart type and five components, and the full build costs ~340 kB per route.
// There is no legend component: the legend is markup below, so it can show
// exactly the marks this chart drew, including the shaded intervals.
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

export interface AnomalyInterval {
  start: string;
  end: string | null;
  label?: string;
}

export interface TransitionMarker {
  at: string;
  label: string;
}

/** The unit every value on this chart is in. */
const UNIT = "EPS";

export function VolumeChart({
  buckets,
  expected,
  expectedLow,
  expectedHigh,
  anomalies = [],
  transitions = [],
  ariaLabel = "Observed volume against expected baseline",
  textSummary,
}: {
  buckets: MetricBucket[];
  expected: number | null;
  expectedLow: number | null;
  expectedHigh: number | null;
  anomalies?: AnomalyInterval[];
  transitions?: TransitionMarker[];
  ariaLabel?: string;
  /** A prose description of what the chart shows, for readers who cannot see
   *  it. An aria-label alone says only that a chart is present. */
  textSummary?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const points = useMemo(() => buildSeries(buckets), [buckets]);
  const unobserved = useMemo(() => unobservedIntervals(buckets), [buckets]);
  const hasBand = expectedLow != null && expectedHigh != null;

  // Built from the tokens, so it can only run in the browser. Everything that
  // needs a colour lives in here.
  const buildOption = useMemo(() => {
    if (points.length === 0) return null;

    return (): EChartsOption => {
      const c = readChartPalette();

      const flat = (value: number | null) =>
        value == null ? [] : points.map((p) => [p.t, value] as [number, number]);

      // Shaded regions for anything not fully observed, plus the anomaly
      // interval itself. Rendered behind the line, in different colours.
      // ECharts types a 2D mark area as a pair, so the tuple shape is explicit.
      const areas: MarkAreaPair[] = [
        ...unobserved.map(
          (u): MarkAreaPair => [
            { xAxis: u.start, itemStyle: { color: c.gap }, name: u.kind },
            { xAxis: u.end },
          ],
        ),
        ...anomalies.map(
          (a): MarkAreaPair => [
            {
              xAxis: Date.parse(a.start),
              itemStyle: { color: c.anomaly },
              name: a.label ?? "anomaly",
            },
            // An anomaly with no end is still running; the shading therefore
            // extends to the last observed point rather than stopping short and
            // implying the incident closed.
            { xAxis: a.end ? Date.parse(a.end) : points[points.length - 1].t },
          ],
        ),
      ];

      // Honour the OS-level preference: the CSS guard cannot reach a canvas
      // renderer, so the chart has to be told directly.
      const reduceMotion =
        typeof window !== "undefined" &&
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      const lone = isolatedPoints(points);
      const text = { color: c.label, fontFamily: c.font };

      return {
        animation: !reduceMotion,
        // The time axis is in UTC, and its title says so.
        useUTC: true,
        grid: { left: 56, right: 24, top: 28, bottom: 52 },
        textStyle: text,
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
            const observed =
              p.observed != null
                ? `Observed: ${p.observed.toFixed(2)} ${UNIT}`
                : `Observed: not measured (${kindMeaning(p.kind)})`;
            const reported =
              p.observed == null && p.reported != null
                ? `Reported by the partial collection: ${p.reported.toFixed(2)} ${UNIT}`
                : "";
            const band = hasBand
              ? `Expected range: ${expectedLow.toFixed(2)} – ${expectedHigh.toFixed(2)} ${UNIT}`
              : "";
            // Whether this instant falls inside a known anomalous interval, so
            // the tooltip states the incident rather than only the number.
            const inAnomaly = anomalies.find((a) => {
              const start = Date.parse(a.start);
              const end = a.end ? Date.parse(a.end) : Number.POSITIVE_INFINITY;
              return p.t >= start && p.t <= end;
            });
            return [
              formatDateTime(new Date(p.t).toISOString()),
              observed,
              reported,
              expected != null ? `Expected: ${expected.toFixed(2)} ${UNIT}` : "",
              band,
              `Collection: ${kindMeaning(p.kind)}`,
              inAnomaly ? `Anomalous interval: ${inAnomaly.label ?? "anomaly"}` : "",
            ]
              .filter(Boolean)
              .join("<br/>");
          },
        },
        xAxis: {
          type: "time",
          name: "Time (UTC)",
          nameLocation: "middle",
          nameGap: 32,
          nameTextStyle: text,
          axisLine: { lineStyle: { color: c.axis } },
          axisLabel: text,
        },
        yAxis: {
          type: "value",
          name: "Events per second (EPS)",
          nameTextStyle: { ...text, align: "left" },
          min: 0,
          splitLine: { lineStyle: { color: c.grid } },
          axisLine: { lineStyle: { color: c.axis } },
          axisLabel: text,
        },
        series: [
          {
            type: "line",
            name: "Observed EPS",
            data: points.map((p) => [p.t, p.observed] as [number, number | null]),
            // The single most important option on this chart. Bridging a null
            // would draw traffic that was never observed.
            connectNulls: false,
            // No marker on every point: a dense series reads as a string of
            // beads. The hovered point is still emphasised by the tooltip.
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
            markArea: areas.length
              ? {
                  silent: true,
                  // Labels off. With several shaded intervals in one window they
                  // overprint each other into an illegible smear; the shading is
                  // explained in the legend below the chart instead.
                  label: { show: false },
                  data: areas,
                }
              : undefined,
            markLine: transitions.length
              ? {
                  silent: true,
                  symbol: "none",
                  // The markers stay; their labels do not. Four transitions
                  // minutes apart render their text on top of one another, and
                  // the Lifecycle tab states the same sequence legibly.
                  label: { show: false },
                  lineStyle: { color: c.marker, type: "dashed" },
                  data: transitions.map((t) => ({
                    xAxis: Date.parse(t.at),
                    name: t.label,
                  })),
                }
              : undefined,
          },
          // The expected median: one subtle line.
          {
            type: "line",
            name: "Expected EPS",
            data: flat(expected),
            showSymbol: false,
            silent: true,
            lineStyle: { color: c.expected, type: "dashed", width: 1 },
            itemStyle: { color: c.expected },
          },
          // The expected range as one band: an invisible floor at the lower
          // bound, and a filled series stacked on it whose height is the width
          // of the range.
          {
            type: "line",
            name: "Expected low",
            data: hasBand ? flat(expectedLow) : [],
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            lineStyle: { opacity: 0 },
          },
          {
            type: "line",
            name: "Expected range",
            data: hasBand ? flat(expectedHigh - expectedLow) : [],
            stack: "expected-range",
            showSymbol: false,
            silent: true,
            lineStyle: { opacity: 0 },
            areaStyle: { color: c.band },
          },
        ],
      };
    };
  }, [points, unobserved, expected, expectedLow, expectedHigh, hasBand, anomalies, transitions]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !buildOption) return;

    const chart = echarts.init(el);
    chart.setOption(buildOption());

    // ECharts does not track element size on its own; without this the chart
    // keeps its first-paint width when the layout changes.
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
        No metric buckets were collected for this window. This is an absence of
        collection, not an observation of zero traffic.
      </div>
    );
  }

  return (
    <>
      <div ref={containerRef} className="chart" role="img" aria-label={ariaLabel} />
      {/* The chart's content as prose. A canvas is opaque to a screen reader,
          so without this the series, the band and the gaps are unreachable. */}
      {textSummary && <p className="sr-only">{textSummary}</p>}
      {/* A key for the marks that were actually drawn, and nothing else. */}
      <ul className="chart-legend" aria-label="Chart key">
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          Observed {UNIT}
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
            Expected range
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
        Gaps in the observed line are intervals that were not fully collected.
        They are not zero traffic.
      </p>
    </>
  );
}
