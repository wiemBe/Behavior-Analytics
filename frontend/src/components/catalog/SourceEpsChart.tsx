"use client";

// One log source's EPS against its seasonal band (UI-3.5b), drawn from the Phase A source
// page's pieces: `lib/timeseries` decides what is a measurement (a PARTIAL or MISSING bucket is
// a null, an interval with no stored row is a gap), and the line never bridges a null. The band
// is the p05-p95 of the cell of each bucket's UTC weekday and hour, the dashed line its median;
// both are absent where that cell is missing or not yet reliable. No marker on every point:
// only a measured bucket between gaps is marked, because a line cannot show one point.

import { LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkAreaComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useMemo } from "react";

import { useEChart } from "@/components/home/useEChart";
import type { MetricBucket } from "@/lib/api";
import { formatDateTime } from "@/lib/health";
import type { HomePalette } from "@/lib/home/chartPalette";
import {
  buildSeries,
  isolatedPoints,
  kindMeaning,
  unobservedIntervals,
} from "@/lib/timeseries";
import type { BaselineLookup, SeasonalValue } from "@/components/waf/wafBaseline";

import type { EChartsOption } from "echarts";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

const UNIT = "EPS";

type MarkAreaPair = [{ xAxis: number; itemStyle?: { color: string }; name?: string }, { xAxis: number }];

export interface SourceEpsModel {
  points: ReturnType<typeof buildSeries>;
  gaps: ReturnType<typeof unobservedIntervals>;
  cells: Array<SeasonalValue | null>;
  hasBand: boolean;
  hasMedian: boolean;
}

export function sourceEpsModel(buckets: MetricBucket[], lookup: BaselineLookup | null): SourceEpsModel {
  const points = buildSeries(buckets);
  const cells = points.map((p) => (lookup && p.kind !== "GAP" ? lookup(p.t) : null));
  return {
    points,
    gaps: unobservedIntervals(buckets),
    cells,
    hasBand: cells.some((c) => c?.low != null && c.high != null),
    hasMedian: cells.some((c) => c != null),
  };
}

export function buildSourceEpsOption(m: SourceEpsModel, c: HomePalette): EChartsOption {
  const { points, cells } = m;
  const reduceMotion =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const text = { color: c.label, fontFamily: c.font };
  const axisCommon = { axisLine: { lineStyle: { color: c.axis } }, axisLabel: text };
  const perBucket = (pick: (v: SeasonalValue) => number | null) =>
    points.map((p, i) => {
      const cell = cells[i];
      return [p.t, cell ? pick(cell) : null] as [number, number | null];
    });
  const gapAreas = m.gaps.map(
    (g): MarkAreaPair => [
      { xAxis: g.start, itemStyle: { color: c.gap }, name: g.kind },
      { xAxis: g.end },
    ],
  );
  const lone = isolatedPoints(points);
  const fmt = (v: number) => v.toFixed(2);

  return {
    animation: !reduceMotion,
    useUTC: true,
    textStyle: text,
    grid: { left: 56, right: 16, top: 28, bottom: 38 },
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
        const cell = cells[i];
        return [
          formatDateTime(new Date(p.t).toISOString()),
          p.observed != null
            ? `Observed: ${fmt(p.observed)} ${UNIT}`
            : `Observed: not measured (${kindMeaning(p.kind)})`,
          p.observed == null && p.reported != null
            ? `Reported by the partial collection: ${fmt(p.reported)} ${UNIT}`
            : "",
          cell ? `Expected median: ${fmt(cell.median)} ${UNIT}` : "",
          cell?.low != null && cell.high != null
            ? `Expected range: ${fmt(cell.low)} – ${fmt(cell.high)} ${UNIT}`
            : "Expected range: no reliable baseline for this weekday and hour",
          `Collection: ${kindMeaning(p.kind)}`,
        ]
          .filter(Boolean)
          .join("<br/>");
      },
    },
    xAxis: {
      type: "time",
      name: "Time (UTC)",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: text,
      ...axisCommon,
    },
    yAxis: {
      type: "value",
      name: "Events per second (EPS)",
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      splitLine: { lineStyle: { color: c.grid } },
      ...axisCommon,
    },
    series: [
      {
        type: "line",
        name: "Observed EPS",
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
        markArea: gapAreas.length
          ? { silent: true, label: { show: false }, data: gapAreas }
          : undefined,
      },
      {
        type: "line",
        name: "Expected EPS",
        data: perBucket((v) => v.median),
        step: "end",
        connectNulls: false,
        showSymbol: false,
        silent: true,
        lineStyle: { color: c.expected, type: "dashed", width: 1 },
        itemStyle: { color: c.expected },
      },
      // The expected range as one band: an invisible floor and a filled series on it.
      {
        type: "line",
        name: "Expected low",
        data: m.hasBand ? perBucket((v) => v.low) : [],
        step: "end",
        connectNulls: false,
        stack: "expected-range",
        showSymbol: false,
        silent: true,
        lineStyle: { opacity: 0 },
      },
      {
        type: "line",
        name: "Expected range",
        data: m.hasBand
          ? perBucket((v) => (v.low != null && v.high != null ? v.high - v.low : null))
          : [],
        step: "end",
        connectNulls: false,
        stack: "expected-range",
        showSymbol: false,
        silent: true,
        lineStyle: { opacity: 0 },
        areaStyle: { color: c.band },
      },
    ],
  };
}

export function SourceEpsChart({
  buckets,
  lookup,
  ariaLabel,
  textSummary,
}: {
  buckets: MetricBucket[];
  /** The seasonal cells; null when they could not be read (no band is drawn). */
  lookup: BaselineLookup | null;
  ariaLabel: string;
  textSummary: string;
}) {
  const model = useMemo(() => sourceEpsModel(buckets, lookup), [buckets, lookup]);
  const build = useMemo(
    () => (model.points.length > 0 ? (p: HomePalette) => buildSourceEpsOption(model, p) : null),
    [model],
  );
  const ref = useEChart(build);
  return (
    <>
      <div ref={ref} className="hc-chart cw-tall" role="img" aria-label={ariaLabel} />
      <p className="sr-only">{textSummary}</p>
      <ul className="hc-key-list" aria-label="Chart key">
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          Observed
        </li>
        {model.hasMedian && (
          <li>
            <span className="chart-key chart-key-expected" aria-hidden="true" />
            Expected median
          </li>
        )}
        {model.hasBand && (
          <li title="The seasonal p05 to p95 of this source's EPS for the bucket's weekday and hour (UTC).">
            <span className="chart-key chart-key-band" aria-hidden="true" />
            Expected range
          </li>
        )}
        {model.gaps.length > 0 && (
          <li title="Intervals that were partial or not stored. They are not zero traffic.">
            <span className="chart-key chart-key-gap" aria-hidden="true" />
            Not measured
          </li>
        )}
      </ul>
    </>
  );
}
