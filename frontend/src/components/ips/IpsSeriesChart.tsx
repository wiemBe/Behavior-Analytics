"use client";

// An IPS subject's measured series against its expected range.
//
// One panel on a UTC time axis, with the unit of what it measures in the axis
// title: events per bucket for an entity, distinct destination ports for
// fan-out, a share in % for concentration and block ratio. The honesty rules are
// the WAF chart's, each with a test:
//   * a value that was not measured is a null on the line, never its reported
//     value, and `connectNulls` is false;
//   * an interval with no stored bucket is shaded as not stored, never zero;
//   * a bucket measured only as a floor is plotted, flagged and shaded;
//   * a maintenance bucket is outlined: the engine does not judge it;
//   * the expected range is one band (the bucket's seasonal cell) with the
//     expectation as a single dashed line; a bucket with no reliable cell has
//     neither, and a missing band is never drawn as a zero-height one;
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

import { readChartPalette } from "@/lib/chartPalette";
import { formatDateTime } from "@/lib/health";
import { bucketWidth } from "@/components/waf/wafFormat";

import type { IpsUnit } from "./ipsDetectors";
import { buildIpsBaselineLookup, type IpsBaselineSource } from "./ipsBaseline";
import {
  ipsMarkedIntervals,
  isolatedPoints,
  kindMeaning,
  type IpsPoint,
} from "./ipsSeries";

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
  {
    xAxis: number;
    itemStyle?: { color: string; borderColor?: string; borderType?: "dashed"; borderWidth?: number };
    name?: string;
  },
  { xAxis: number },
];

export interface IpsInterval {
  start: string;
  end: string | null;
}

export interface IpsTransition {
  at: string;
  label: string;
}

/** What a dimension measurement means for the reader of one bucket. */
export function measurementMeaning(measurement: string | null): string {
  switch (measurement) {
    case "EXACT":
      return "exact";
    case "LOWER_BOUND":
      return "a lower bound: the dimension's search hit its cap";
    case "NOT_COLLECTED":
      return "not collected";
    case "FAILED":
      return "the search failed";
    case "REJECTED":
      return "the search was rejected";
    case null:
      return "the bucket record is missing, so exactness is unknown";
    default:
      return measurement;
  }
}

export function IpsSeriesChart({
  points,
  rows,
  unit,
  quantity,
  bucketSeconds,
  baseline,
  expected,
  expectedLow,
  expectedHigh,
  anomalies = [],
  transitions = [],
  ariaLabel,
  textSummary,
}: {
  points: IpsPoint[];
  /** The stored bucket rows behind `points`, for the marked intervals. */
  rows: Array<{ bucket_start: string; bucket_seconds: number }>;
  unit: IpsUnit;
  /** The measured quantity in words, for the key and the tooltip. */
  quantity: string;
  bucketSeconds: number | null;
  /**
   * The seasonal cells, looked up per bucket by UTC weekday and hour. When given,
   * the band and the expectation follow it, and a bucket with no reliable cell
   * has none. When absent (no cells could be read) the band is the flat range
   * recorded at detection, and is labelled so.
   */
  baseline: IpsBaselineSource | null;
  expected: number | null;
  expectedLow: number | null;
  expectedHigh: number | null;
  anomalies?: IpsInterval[];
  transitions?: IpsTransition[];
  ariaLabel: string;
  /** The chart as prose, for readers who cannot see a canvas. */
  textSummary?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const scale = unit === "share" ? 100 : 1;
  const marks = useMemo(() => ipsMarkedIntervals(rows, points), [rows, points]);
  const seasonal = baseline != null;
  const lookup = useMemo(() => (baseline ? buildIpsBaselineLookup(baseline) : null), [baseline]);
  // Per bucket, from the cell of that bucket's weekday and hour. A gap point and a
  // bucket whose cell is missing or unreliable has none.
  const cells = useMemo(
    () => (lookup ? points.map((p) => (p.kind === "GAP" ? null : lookup(p.t))) : []),
    [lookup, points],
  );
  const hasBand = seasonal
    ? cells.some((c) => c?.low != null && c.high != null)
    : expectedLow != null && expectedHigh != null;
  const hasMedian = seasonal ? cells.some((c) => c?.median != null) : expected != null;
  const hasLowerBound = points.some((p) => p.lowerBound);
  const hasMaintenance = marks.some((m) => m.mark === "MAINTENANCE");
  const hasUnmeasured = marks.some((m) => m.mark !== "MAINTENANCE");
  const width = bucketWidth(bucketSeconds);
  const unitText =
    unit === "events"
      ? width
        ? `events / ${width}`
        : "events / bucket"
      : unit === "ports"
        ? "distinct ports"
        : "%";
  const axisTitle = unit === "share" ? `${quantity} (%)` : `${quantity} (${unitText})`;
  const bandLabel = seasonal ? "Expected range (seasonal p05–p95)" : "Expected range at detection";
  const medianLabel = seasonal ? "Expected (seasonal cell)" : "Expected at detection";

  const buildOption = useMemo(() => {
    if (points.length === 0) return null;

    return (): EChartsOption => {
      const c = readChartPalette();
      const flat = (value: number | null) =>
        value == null ? [] : points.map((p) => [p.t, value * scale] as [number, number]);
      const perBucket = (pick: (c: NonNullable<(typeof cells)[number]>) => number | null) =>
        points.map((p, i) => {
          const cell = cells[i];
          const v = cell ? pick(cell) : null;
          return [p.t, v == null ? null : v * scale] as [number, number | null];
        });

      const markAreas: MarkAreaPair[] = marks.map((m): MarkAreaPair => {
        const maintenance = m.mark === "MAINTENANCE";
        return [
          {
            xAxis: m.start,
            name: m.mark,
            itemStyle: maintenance
              ? { color: "transparent", borderColor: c.axis, borderType: "dashed", borderWidth: 1 }
              : { color: c.gap },
          },
          { xAxis: m.end },
        ];
      });
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
      const fmt = (v: number) => (unit === "share" ? `${(v * 100).toFixed(1)}%` : v.toFixed(1));
      const suffix = unit === "events" ? ` ${unitText}` : unit === "ports" ? " ports" : "";
      const whole = (v: number) =>
        unit === "share" ? `${(v * 100).toFixed(1)}%` : String(v);

      return {
        animation: !reduceMotion,
        useUTC: true,
        textStyle: text,
        grid: { left: 64, right: 24, top: 40, height: 220 },
        tooltip: {
          trigger: "axis",
          backgroundColor: c.tooltipBg,
          borderColor: c.tooltipBorder,
          textStyle: { color: c.tooltipText, fontFamily: c.font },
          extraCssText: "box-shadow: none;",
          formatter: (params: unknown) => {
            const list = Array.isArray(params) ? params : [params];
            const first = list[0] as { dataIndex?: number } | undefined;
            const i = first?.dataIndex;
            const p = i != null ? points[i] : undefined;
            if (!p || i == null) return "";
            if (p.kind === "GAP") {
              return [formatDateTime(new Date(p.t).toISOString()), kindMeaning("GAP")].join("<br/>");
            }
            const cell = seasonal ? cells[i] : null;
            const observed =
              p.observed != null
                ? `${quantity}: ${p.lowerBound ? "≥ " : ""}${whole(p.observed)}${suffix}`
                : `${quantity}: not measured`;
            const reported =
              p.observed == null && p.reported != null
                ? `Reported but not used: ${whole(p.reported)}${suffix}`
                : "";
            const counts =
              unit === "share" && p.numerator != null && p.denominator != null
                ? `${p.numerator} of ${p.denominator} events`
                : "";
            let median = "";
            let band = "";
            if (seasonal) {
              median =
                cell?.median != null ? `${medianLabel}: ${fmt(cell.median)}${suffix}` : "";
              band =
                cell?.low != null && cell.high != null
                  ? `${bandLabel}: ${fmt(cell.low)} – ${fmt(cell.high)}${suffix}`
                  : `${bandLabel}: no reliable baseline for this weekday and hour`;
            } else {
              median = expected != null ? `${medianLabel}: ${fmt(expected)}${suffix}` : "";
              band =
                expectedLow != null && expectedHigh != null
                  ? `${bandLabel}: ${fmt(expectedLow)} – ${fmt(expectedHigh)}${suffix}`
                  : "";
            }
            return [
              formatDateTime(new Date(p.t).toISOString()),
              observed,
              reported,
              counts,
              median,
              band,
              `Measurement: ${measurementMeaning(p.measurement)}`,
              p.maintenance ? "In maintenance: not judged by the engine" : "",
              p.admissionTruncated ? "Entity admission was truncated in this bucket" : "",
            ]
              .filter(Boolean)
              .join("<br/>");
          },
        },
        xAxis: {
          type: "time",
          name: "Time (UTC)",
          nameLocation: "middle",
          nameGap: 30,
          nameTextStyle: text,
          ...axisCommon,
        },
        yAxis: {
          type: "value",
          name: axisTitle,
          nameTextStyle: { ...text, align: "left" },
          min: 0,
          splitLine: { lineStyle: { color: c.grid } },
          ...axisCommon,
        },
        series: [
          {
            type: "line",
            name: quantity,
            data: points.map(
              (p) => [p.t, p.observed == null ? null : p.observed * scale] as [number, number | null],
            ),
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
              markAreas.length + anomalyAreas.length
                ? { ...quiet, data: [...markAreas, ...anomalyAreas] }
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
            name: medianLabel,
            data: seasonal ? perBucket((v) => v.median) : flat(expected),
            ...(seasonal ? { step: "end" as const } : {}),
            connectNulls: false,
            showSymbol: false,
            silent: true,
            lineStyle: { color: c.expected, type: "dashed", width: 1 },
            itemStyle: { color: c.expected },
          },
          // The expected range as one band: an invisible floor at the lower bound,
          // and a filled series stacked on it as wide as the range.
          {
            type: "line",
            name: "Expected low",
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
            name: bandLabel,
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
        ],
      };
    };
  }, [
    points,
    marks,
    expected,
    expectedLow,
    expectedHigh,
    hasBand,
    anomalies,
    transitions,
    scale,
    seasonal,
    cells,
    unit,
    unitText,
    axisTitle,
    quantity,
    bandLabel,
    medianLabel,
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

  if (points.length === 0) return null;

  return (
    <>
      <div ref={containerRef} className="chart chart-ips" role="img" aria-label={ariaLabel} />
      {textSummary && <p className="sr-only">{textSummary}</p>}
      <ul className="chart-legend" aria-label="Chart key">
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          {quantity}
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
        {hasUnmeasured && (
          <li>
            <span className="chart-key chart-key-gap" aria-hidden="true" />
            Not stored, not measured or not exact
          </li>
        )}
        {hasMaintenance && (
          <li>
            <span className="chart-key chart-key-maintenance" aria-hidden="true" />
            Maintenance
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
        Gaps in the line are buckets that were not stored or not measured. They are not{" "}
        {unit === "share" ? "0 %" : unit === "ports" ? "zero ports" : "zero events"}.
        {hasLowerBound && " Some buckets are lower bounds (at least the plotted value)."}
        {seasonal &&
          " The band follows the baseline cell of each bucket's UTC weekday and hour, and is absent where that cell is missing or not yet reliable."}
      </p>
    </>
  );
}
