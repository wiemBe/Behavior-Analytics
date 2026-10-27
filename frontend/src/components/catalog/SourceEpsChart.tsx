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
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { HistoryKey, HistoryText } from "@/components/behavior/HistoryKey";
import {
  historyIntervals,
  historyMarkAreas,
  originByTime,
  originText,
  readHistoryShade,
  type HistoryInterval,
} from "@/components/behavior/historyOrigin";
import { TIME_AXIS_FORMATTER } from "@/components/home/chartOptions";
import { useEChart } from "@/components/home/useEChart";
import { formatEps } from "@/i18n/measures";
import { grouped } from "@/lib/figureText";
import type { MetricBucket } from "@/lib/api";
import { useFleetText, type FleetText } from "@/lib/fleetText";
import type { HomePalette } from "@/lib/home/chartPalette";
import {
  buildSeries,
  isolatedPoints,
  kindMeaning,
  unobservedIntervals,
} from "@/lib/timeseries";
import type { BaselineLookup, SeasonalValue } from "@/components/waf/wafBaseline";
import { utcTimeHtml } from "@/components/ui/UtcTime";

import { englishCatalogText } from "./catalogText";


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
  /** Spans of buckets read from QRadar's past: shaded, never judged. */
  history: HistoryInterval[];
  /** Words inside the chart: only where the chart is large enough to keep them off the line. */
  labelled: boolean;
  origins: ReturnType<typeof originByTime>;
}

export function sourceEpsModel(
  buckets: MetricBucket[],
  lookup: BaselineLookup | null,
  labelled = true,
): SourceEpsModel {
  const points = buildSeries(buckets);
  const cells = points.map((p) => (lookup && p.kind !== "GAP" ? lookup(p.t) : null));
  return {
    points,
    gaps: unobservedIntervals(buckets),
    cells,
    hasBand: cells.some((c) => c?.low != null && c.high != null),
    hasMedian: cells.some((c) => c != null),
    history: historyIntervals(buckets),
    labelled,
    origins: originByTime(buckets),
  };
}

export function buildSourceEpsOption(
  m: SourceEpsModel,
  c: HomePalette,
  words: FleetText = englishCatalogText,
): EChartsOption {
  const { points, cells } = m;
  const { say, locale } = words;
  const reduceMotion =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const text = { color: c.label, fontFamily: c.font };
  const axisCommon = { axisLine: { lineStyle: { color: c.axis } }, axisLabel: text };
  const timeAxisCommon = {
    ...axisCommon,
    axisLabel: { ...text, formatter: TIME_AXIS_FORMATTER },
  };
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
  const historyAreas = historyMarkAreas(m.history, readHistoryShade(), m.labelled, say("behavior.history.text"));
  const lone = isolatedPoints(points);
  const fmt = (v: number) => (locale === "en" ? v.toFixed(2) : formatEps(v, locale));

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
        const unit = UNIT;
        return [
          utcTimeHtml(new Date(p.t).toISOString()),
          p.observed != null
            ? say("catalog.chart.observedTip", { value: fmt(p.observed), unit })
            : say("catalog.chart.observedNone", { kind: kindMeaning(p.kind, { t: say, locale }) }),
          p.observed == null && p.reported != null
            ? say("catalog.chart.reported", { value: fmt(p.reported), unit })
            : "",
          cell ? say("catalog.chart.median", { value: fmt(cell.median), unit }) : "",
          cell?.low != null && cell.high != null
            ? say("catalog.chart.range", { low: fmt(cell.low), high: fmt(cell.high), unit })
            : say("catalog.chart.noBaseline"),
          say("catalog.chart.collection", { kind: kindMeaning(p.kind, { t: say, locale }) }),
          originText(m.origins.get(p.t), words),
        ]
          .filter(Boolean)
          .join("<br/>");
      },
    },
    xAxis: {
      type: "time",
      name: say("catalog.chart.xAxis"),
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: text,
      ...timeAxisCommon,
    },
    yAxis: {
      type: "value",
      name: say("catalog.chart.yAxis"),
      nameTextStyle: { ...text, align: "left" },
      min: 0,
      splitLine: { lineStyle: { color: c.grid } },
      ...axisCommon,
      axisLabel:
        locale === "tr" ? { ...text, formatter: (value: number) => grouped(value, locale) } : text,
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
        markArea:
          gapAreas.length + historyAreas.length
            ? { silent: true, label: { show: false }, data: [...gapAreas, ...historyAreas] }
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
  labelled = false,
}: {
  buckets: MetricBucket[];
  /** The seasonal cells; null when they could not be read (no band is drawn). */
  lookup: BaselineLookup | null;
  ariaLabel: string;
  textSummary: string;
  /** The history words inside the chart (the expanded widget); the tile leaves them to the key. */
  labelled?: boolean;
}) {
  const t = useTranslations("catalog.chart");
  const { say, locale } = useFleetText();
  const model = useMemo(() => sourceEpsModel(buckets, lookup, labelled), [buckets, lookup, labelled]);
  const build = useMemo(
    () =>
      model.points.length > 0
        ? (p: HomePalette) => buildSourceEpsOption(model, p, { say, locale })
        : null,
    [model, say, locale],
  );
  const ref = useEChart(build);
  return (
    <>
      <div ref={ref} className="hc-chart cw-tall" role="img" aria-label={ariaLabel} />
      <p className="sr-only">{textSummary}</p>
      <HistoryText rows={buckets} />
      <ul className="hc-key-list" aria-label={t("key")}>
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          {t("observed")}
        </li>
        {model.hasMedian && (
          <li>
            <span className="chart-key chart-key-expected" aria-hidden="true" />
            {t("expectedMedian")}
          </li>
        )}
        {model.hasBand && (
          <li title={t("expectedRangeTitle")}>
            <span className="chart-key chart-key-band" aria-hidden="true" />
            {t("expectedRange")}
          </li>
        )}
        {model.gaps.length > 0 && (
          <li title={t("notMeasuredTitle")}>
            <span className="chart-key chart-key-gap" aria-hidden="true" />
            {t("notMeasured")}
          </li>
        )}
        {model.history.length > 0 && <HistoryKey />}
      </ul>
    </>
  );
}
