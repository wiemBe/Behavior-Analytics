"use client";

// Result trend for one scheduled search, on the search page and in chart.search_results.
//
// ECharts is imported here and nowhere else on this route: it touches `document` and
// `ResizeObserver` on construction, so it must never be pulled into a server component.
// The page stays a server component and passes the search it already loaded.

import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";

import { ConfigNotice } from "@/components/catalog/ConfigNotice";
import { ENGLISH_TREND, trendCount, type TrendI18n } from "@/components/trendText";
import { runReadout } from "@/components/searches/authoring";
import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import {
  ApiError,
  actionErrorMessage,
  api,
  windowBounds,
  type LatestGroups,
  type ResultMetricPoint,
  type SearchExecution,
  type SearchResultTrend,
} from "@/lib/api";
import { readChartPalette } from "@/lib/chartPalette";
import { durationText } from "@/i18n/measures";
import {
  axisTimeLabel,
  formatRunDuration,
  isolatedIndices,
  resultCount,
  trendRows,
  truncatedRunTimes,
} from "@/lib/searchRuns";
import { useEffectiveRange, useExpanded } from "@/widgets/runtime";
import type { WidgetRange } from "@/widgets/types";
import { UtcTime, utcTimeHtml } from "@/components/ui/UtcTime";


import type { EChartsOption } from "echarts";

import "./../app/searches/[id]/search-detail.css";

// Modular registration rather than `import * as echarts from "echarts"`: the
// full bundle costs ~340 kB on this route, and the trend needs two chart types
// and four components.
echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

const FAILED_STATUSES = new Set(["FAILED", "TIMEOUT", "CANCELLED"]);

const SERIES_VIZ = new Set(["LINE", "BAR", "SINGLE_VALUE"]);
const TABLE_VIZ = new Set(["TABLE", "PIE", "HEATMAP"]);

export type TrendVisualization =
  | "LINE"
  | "BAR"
  | "SINGLE_VALUE"
  | "TABLE"
  | "PIE"
  | "HEATMAP";

/** Refused read of this widget (English; the body shows `trend.forbidden`). Names the permission and who grants it. */
export const SEARCH_RESULTS_FORBIDDEN =
  "Your role cannot read search results (permission read:searches). An administrator grants read:searches. Nothing was changed.";

export interface TrendSearch {
  last_run_at: string | null;
  next_run_at: string | null;
  consecutive_failures: number;
}

/** The search's own schedule interval, or null when the two timestamps cannot give one. */
export function scheduleIntervalMs(search: TrendSearch | null | undefined): number | null {
  if (!search?.last_run_at || !search.next_run_at) return null;
  const interval = Date.parse(search.next_run_at) - Date.parse(search.last_run_at);
  if (!Number.isFinite(interval) || interval <= 0) return null;
  return interval;
}

export interface Staleness {
  at: string;
  ageMs: number;
  intervalMs: number;
}

/**
 * Stale when the last successful run is older than twice the search's own interval.
 * A missing interval is not staleness and not health: the caller says nothing.
 */
export function staleness(
  search: TrendSearch | null | undefined,
  executions: Pick<SearchExecution, "status" | "completed_at" | "started_at">[],
  now: number,
): Staleness | null {
  const intervalMs = scheduleIntervalMs(search);
  if (intervalMs == null) return null;
  let best: { at: string; t: number } | null = null;
  for (const execution of executions) {
    if (execution.status !== "COMPLETED") continue;
    const at = execution.completed_at ?? execution.started_at;
    if (!at) continue;
    const t = Date.parse(at);
    if (!Number.isFinite(t)) continue;
    if (!best || t > best.t) best = { at, t };
  }
  if (!best) return null;
  const ageMs = now - best.t;
  if (ageMs > 2 * intervalMs) return { at: best.at, ageMs, intervalMs };
  return null;
}

function normalizeViz(value: string | undefined): TrendVisualization {
  const name = (value ?? "LINE").toUpperCase();
  if (
    name === "BAR" ||
    name === "SINGLE_VALUE" ||
    name === "TABLE" ||
    name === "PIE" ||
    name === "HEATMAP" ||
    name === "LINE"
  ) {
    return name;
  }
  return "LINE";
}

/** The run that started latest. A missing start sorts after a recorded one. */
function latestExecution(executions: SearchExecution[]): SearchExecution | null {
  let best: SearchExecution | null = null;
  for (const execution of executions) {
    if (!best) {
      best = execution;
      continue;
    }
    const rank = execution.started_at ?? "";
    const bestRank = best.started_at ?? "";
    if (rank > bestRank || (rank === bestRank && execution.id > best.id)) best = execution;
  }
  return best;
}

function versionBoundaries(points: ResultMetricPoint[]): ResultMetricPoint[] {
  return points.filter((point, i) => i > 0 && point.query_version !== points[i - 1].query_version);
}

function formatDimValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function formatDimensions(dimensions: Record<string, unknown>): string {
  const parts = Object.entries(dimensions).map(([key, value]) => `${key}: ${formatDimValue(value)}`);
  return parts.length ? parts.join(", ") : "—";
}

function httpMessage(status: number | null, x: TrendI18n): string {
  return actionErrorMessage(new ApiError(status ?? 500, ""), x === ENGLISH_TREND ? undefined : x.t);
}

/** The `units` namespace's translator (`second`, `minute`, `hour`, `day`) from a full-key one. */
function unitsOf(x: TrendI18n) {
  return (key: string, values?: Record<string, string | number>) => x.t(`units.${key}`, values);
}

function refused(states: { status: string }[]): boolean {
  return states.some((state) => state.status === "forbidden");
}

function CreatedLine({
  createdAt,
  variant,
  x,
}: {
  createdAt: string | null;
  variant: "page" | "widget";
  x: TrendI18n;
}) {
  const { t } = x;
  const how = variant === "widget" ? t("trend.neverRun.howWidget") : t("trend.neverRun.howPage");
  return (
    <div className={variant === "widget" ? "tw-state" : "notice"}>
      <p>
        {createdAt ? (
          <>
            {t("trend.neverRun.createdBefore")}
            <UtcTime value={createdAt} />
            {t("trend.neverRun.createdAfter")}
          </>
        ) : (
          t("trend.neverRun.noCreated")
        )}
        {how}
      </p>
    </div>
  );
}

function StaleLine({ stale, x }: { stale: Staleness; x: TrendI18n }) {
  const units = unitsOf(x);
  return (
    <p className="subtitle trend-note" role="status">
      {x.t("trend.stale.before")}
      <UtcTime value={stale.at} />
      {x.t("trend.stale.after", {
        age: durationText(units, stale.ageMs / 1000),
        interval: durationText(units, stale.intervalMs / 1000),
      })}
    </p>
  );
}

/** The mark before a lower bound; the screen reader hears `trend.atLeast` instead. */
const LOWER_BOUND_MARK = "≥ ";

function LowerBoundValue({ value, x }: { value: number; x: TrendI18n }) {
  return (
    <>
      <span aria-hidden="true">{LOWER_BOUND_MARK}</span>
      <span className="sr-only">{x.t("trend.atLeast")}</span>
      <span className="num">{value}</span>
    </>
  );
}

export function ResultTrend({
  searchId,
  metricKey,
  truncatedAt,
  visualization,
  search,
  executions: executionsProp,
  createdAt: createdAtProp,
  variant = "page",
  honourRange = false,
  range: rangeProp,
  now,
  i18n: x = ENGLISH_TREND,
}: {
  searchId: string;
  /** Series key. Omitted on a table: the table starts on all rows. */
  metricKey?: string;
  /** Times of truncated runs. They have no stored point; each becomes a gap. */
  truncatedAt?: string[];
  visualization?: string;
  search?: TrendSearch | null;
  executions?: SearchExecution[];
  createdAt?: string | null;
  variant?: "page" | "widget";
  /** When set, the trend read takes the dashboard range. The page leaves this off. */
  honourRange?: boolean;
  range?: WidgetRange | null;
  /** Clock for the staleness check. Tests pass a fixed instant. */
  now?: number;
  /** The interface language; without it the trend is in English (the search page). */
  i18n?: TrendI18n;
}) {
  const { t } = x;
  const chartRef = useRef<echarts.ECharts | null>(null);
  // The node, not only the option: the option can be ready while a loading
  // branch is still mounted, and the effect would otherwise miss the div.
  const [chartNode, setChartNode] = useState<HTMLDivElement | null>(null);
  const viz = normalizeViz(visualization);
  const tableMode = TABLE_VIZ.has(viz);
  const [picked, setPicked] = useState<string | null>(null);
  const activeKey = picked ?? (tableMode ? (metricKey ?? "") : (metricKey ?? "total"));
  const dashboardRange = useEffectiveRange(rangeProp);
  const expanded = useExpanded();
  const bounds = honourRange ? windowBounds(dashboardRange) : null;
  const resultLimit = expanded ? 5000 : 500;
  const groupLimit = expanded ? 500 : 50;
  const clock = now ?? Date.now();

  const fetchExecutions = executionsProp === undefined && variant === "widget";
  const fetchCreated = createdAtProp === undefined && variant === "widget";
  const fetchSearch = search === undefined && variant === "widget";

  const executionsState = useApiResource(
    fetchExecutions ? `/searches/${searchId}/executions` : null,
    (signal) => api.searchExecutions(searchId, signal),
  );
  const versionsState = useApiResource(
    fetchCreated ? `/searches/${searchId}/versions` : null,
    (signal) => api.searchVersions(searchId, signal),
  );
  const searchState = useApiResource(
    fetchSearch ? `/searches/${searchId}` : null,
    (signal) => api.search(searchId, signal),
  );
  const keysState = useApiResource(
    `/searches/${searchId}/metric-keys?limit=1000`,
    (signal) => api.searchMetricKeys(searchId, 1000, signal),
  );

  const executions = executionsProp ?? (executionsState.status === "ok" ? executionsState.data : undefined);
  const knowNeverRun = executions !== undefined && executions.length === 0;
  const resultKey = tableMode
    ? null
    : [
        "results",
        searchId,
        activeKey || "total",
        resultLimit,
        bounds?.since ?? "",
        bounds?.until ?? "",
      ].join("|");
  const resultsState = useApiResource(resultKey, (signal) =>
    api.searchResults(
      searchId,
      activeKey || "total",
      resultLimit,
      bounds ? { start: bounds.since, end: bounds.until } : undefined,
      signal,
    ),
  );
  const groupsKey =
    tableMode && !knowNeverRun ? ["groups", searchId, groupLimit].join("|") : null;
  const groupsState = useApiResource(groupsKey, (signal) =>
    api.searchLatestGroups(searchId, groupLimit, signal),
  );

  const loadedSearch: TrendSearch | null | undefined =
    search ?? (searchState.status === "ok" ? searchState.data : search);
  const createdAt =
    createdAtProp !== undefined
      ? createdAtProp
      : versionsState.status === "ok"
        ? (versionsState.data.find((version) => version.version === 1)?.created_at ??
          versionsState.data.reduce<string | null>(
            (min, version) => (min == null || version.created_at < min ? version.created_at : min),
            null,
          ))
        : null;

  const gaps = truncatedAt ?? (executions ? truncatedRunTimes(executions) : []);
  const truncatedKey = gaps.join("|");
  const trend = resultsState.status === "ok" ? resultsState.data : null;
  const rows = useMemo(
    () => (trend ? trendRows(trend.points, truncatedKey ? truncatedKey.split("|") : []) : []),
    [trend, truncatedKey],
  );
  const gapCount = rows.filter((row) => row.point === null).length;
  const latest = executions ? latestExecution(executions) : null;
  const stale =
    loadedSearch && executions && latest && !FAILED_STATUSES.has(latest.status)
      ? staleness(loadedSearch, executions, clock)
      : null;

  const storedKeys = keysState.status === "ok" ? keysState.data.keys : null;
  const pickerKeys = useMemo(() => {
    const names = storedKeys ? storedKeys.map((row) => row.metric_key) : [];
    if (activeKey && !names.includes(activeKey)) names.unshift(activeKey);
    if (!tableMode && names.length === 0) names.push(activeKey || "total");
    return names;
  }, [storedKeys, activeKey, tableMode]);

  const seriesType = viz === "BAR" ? "bar" : "line";
  const option = useMemo<EChartsOption | null>(() => {
    if (!SERIES_VIZ.has(viz) || viz === "SINGLE_VALUE") return null;
    if (!trend || trend.points.length === 0) return null;
    const points = trend.points;
    // A failed run has no trustworthy count. Plotting it as 0 would invent a
    // cliff that never happened, so the line is broken with null and the run is
    // marked separately.
    //
    // A truncated run has no point at all (its count is a cap, not a
    // measurement), so its row is null too. connectNulls stays false, which is
    // what breaks the line there instead of bridging the neighbours. A bar
    // leaves the same null, so the slot is empty rather than a zero-height bar.
    const values = rows.map((row) =>
      row.point === null || FAILED_STATUSES.has(row.point.execution_status) ? null : row.point.value,
    );
    const times = rows.map((row) => Date.parse(row.at));
    const failures = rows
      .map((row) =>
        row.point !== null && FAILED_STATUSES.has(row.point.execution_status)
          ? { coord: [Date.parse(row.at), 0], value: row.point.execution_status }
          : null,
      )
      .filter((item): item is { coord: number[]; value: string } => item !== null);
    const boundaries = versionBoundaries(points);
    const palette = readChartPalette();
    const text = { color: palette.label, fontFamily: palette.font };
    const lone = isolatedIndices(values);
    const reduceMotion =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const metric = trend.metric_key;

    return {
      animation: !reduceMotion,
      useUTC: true,
      grid: { left: 56, right: 24, top: 32, bottom: 56 },
      textStyle: text,
      tooltip: {
        trigger: "axis",
        backgroundColor: palette.tooltipBg,
        borderColor: palette.tooltipBorder,
        textStyle: { color: palette.tooltipText, fontFamily: palette.font },
        extraCssText: "box-shadow: none;",
        formatter: (params: unknown) => {
          const list = Array.isArray(params) ? params : [params];
          const first = list[0] as { dataIndex?: number } | undefined;
          const idx = first?.dataIndex;
          if (idx == null || !rows[idx]) return "";
          const row = rows[idx];
          if (row.point === null) {
            return [
              utcTimeHtml(row.at),
              t("trend.tooltip.truncatedRun"),
              t("trend.tooltip.truncatedCap"),
            ].join("<br/>");
          }
          const point = row.point;
          const count = FAILED_STATUSES.has(point.execution_status)
            ? t("trend.tooltip.failedCount")
            : trendCount(point.value, x.locale);
          return [
            utcTimeHtml(point.bucket_start),
            t("trend.tooltip.resultCount", { count }),
            t("trend.tooltip.status", { status: point.execution_status }),
            t("trend.tooltip.duration", {
              duration:
                x === ENGLISH_TREND
                  ? formatRunDuration(point.duration_ms)
                  : formatRunDuration(point.duration_ms, unitsOf(x), x.locale),
            }),
            t("trend.tooltip.version", { version: point.query_version }),
            point.threshold_breached ? t("trend.tooltip.breached") : "",
          ]
            .filter(Boolean)
            .join("<br/>");
        },
      },
      xAxis: {
        type: "time",
        name: t("trend.axis.time"),
        nameLocation: "middle",
        nameGap: 32,
        nameTextStyle: text,
        axisLine: { lineStyle: { color: palette.axis } },
        axisLabel: {
          ...text,
          hideOverlap: true,
          formatter: (value: number) => axisTimeLabel(new Date(value).toISOString()),
        },
      },
      yAxis: {
        type: "value",
        name: t("trend.axis.rows"),
        nameTextStyle: { ...text, align: "left" },
        min: 0,
        // Same spacing as the fleet EPS axis: few ticks, and ECharts drops a
        // label that would land on another. The font size stays the default.
        minInterval: 1,
        splitNumber: 3,
        splitLine: { lineStyle: { color: palette.grid } },
        axisLine: { lineStyle: { color: palette.axis } },
        axisLabel: { ...text, hideOverlap: true },
      },
      series: [
        {
          type: seriesType,
          name: metric,
          data: values.map((value, i) => [times[i], value]),
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: palette.observed, width: 1.5 },
          itemStyle: { color: palette.observed },
          markLine: boundaries.length
            ? {
                silent: true,
                symbol: "none",
                label: {
                  formatter: (item: { name?: string }) => item.name ?? "",
                  color: palette.warn,
                  position: "insideEndTop",
                },
                lineStyle: { color: palette.warn, type: "dashed" },
                data: boundaries.map((boundary) => ({
                  xAxis: Date.parse(boundary.bucket_start),
                  name: `v${boundary.query_version}`,
                })),
              }
            : undefined,
          markPoint:
            failures.length || lone.length
              ? {
                  silent: true,
                  label: { show: false },
                  data: [
                    ...lone.map((i) => ({
                      name: t("trend.series.lone"),
                      coord: [times[i], values[i] as number],
                      symbol: "circle",
                      symbolSize: 5,
                      itemStyle: { color: palette.observed },
                    })),
                    ...failures.map((failure) => ({
                      name: failure.value,
                      coord: failure.coord,
                      symbol: "pin",
                      symbolSize: 44,
                      itemStyle: { color: palette.crit },
                      label: { show: true, color: palette.tooltipText, fontSize: 12, formatter: t("trend.series.fail") },
                    })),
                  ],
                }
              : undefined,
        },
        ...(trend.threshold_value != null
          ? [
              {
                type: "line" as const,
                name: t("trend.series.threshold"),
                data: times.map((t) => [t, trend.threshold_value]),
                showSymbol: false,
                lineStyle: { color: palette.crit, type: "dotted" as const, width: 1 },
                itemStyle: { color: palette.crit },
                tooltip: { show: false },
              },
            ]
          : []),
      ],
    };
  }, [viz, seriesType, trend, rows, t, x]);

  useEffect(() => {
    if (!chartNode || !option) return;
    const chart = echarts.init(chartNode);
    chartRef.current = chart;
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartNode);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [chartNode, option]);

  const box = variant === "widget" ? "tw-state" : "notice";
  const reads = [keysState, resultsState, groupsState, executionsState, versionsState, searchState];
  const waiting =
    (fetchExecutions && executionsState.status === "loading") ||
    (!tableMode && (resultsState.status === "loading" || resultsState.status === "idle")) ||
    (tableMode && !knowNeverRun && (groupsState.status === "loading" || groupsState.status === "idle"));

  let body;
  if (waiting) {
    body = (
      <p className={box} role="status">
        {t("trend.loading")}
      </p>
    );
  } else if (refused(reads)) {
    body = (
      <p className={box} role="alert">
        {t("trend.forbidden")}
      </p>
    );
  } else if (
    variant === "widget" &&
    ((searchState.status === "error" && searchState.httpStatus === 404) ||
      (resultsState.status === "error" && resultsState.httpStatus === 404) ||
      (groupsState.status === "error" && groupsState.httpStatus === 404))
  ) {
    body = (
      <ConfigNotice
        lead={t("trend.deletedLead")}
        text={t("trend.deletedText")}
        hint={t("trend.deletedHint")}
      />
    );
  } else if (
    (!tableMode && resultsState.status === "error") ||
    (tableMode && !knowNeverRun && groupsState.status === "error") ||
    (fetchExecutions && executionsState.status === "error")
  ) {
    const failed = !tableMode
      ? resultsState
      : groupsState.status === "error"
        ? groupsState
        : executionsState;
    const status = failed.status === "error" ? failed.httpStatus : null;
    body = (
      <p className={box} role="alert">
        {httpMessage(status, x)}
      </p>
    );
  } else if (latest && FAILED_STATUSES.has(latest.status)) {
    body = (
      <p className={box} role="alert">
        {runReadout(latest, loadedSearch?.consecutive_failures ?? null, t).text}
      </p>
    );
  } else if (knowNeverRun || (executions === undefined && trend !== null && trend.points.length === 0)) {
    body = <CreatedLine createdAt={createdAt} variant={variant} x={x} />;
  } else if (tableMode && groupsState.status === "ok" && groupsState.data.empty !== null) {
    body = (
      <div className={box}>
        <p>
          {groupsState.data.empty === "no_execution"
            ? t("trend.noGrouped.noExecution")
            : t("trend.noGrouped.noRows")}{" "}
          {t("trend.noGrouped.missing")}
        </p>
      </div>
    );
  } else {
    body = (
      <TrendBody
        viz={viz}
        variant={variant}
        activeKey={activeKey}
        pickerKeys={pickerKeys}
        tableMode={tableMode}
        onPick={setPicked}
        trend={trend}
        gapCount={gapCount}
        onChart={setChartNode}
        groups={groupsState.status === "ok" ? groupsState.data : null}
        executions={executions}
        latest={latest}
        stale={stale}
        honourRange={honourRange}
        expanded={expanded}
        x={x}
      />
    );
  }

  return (
    <div data-component="result-trend" className={variant === "widget" ? "sr-widget" : undefined}>
      {body}
    </div>
  );
}

function TrendBody({
  viz,
  variant,
  activeKey,
  pickerKeys,
  tableMode,
  onPick,
  trend,
  gapCount,
  onChart,
  groups,
  executions,
  latest,
  stale,
  honourRange,
  expanded,
  x,
}: {
  viz: TrendVisualization;
  variant: "page" | "widget";
  activeKey: string;
  pickerKeys: string[];
  tableMode: boolean;
  onPick: (key: string) => void;
  trend: SearchResultTrend | null;
  gapCount: number;
  onChart: (node: HTMLDivElement | null) => void;
  groups: LatestGroups | null;
  executions: SearchExecution[] | undefined;
  latest: SearchExecution | null;
  stale: Staleness | null;
  honourRange: boolean;
  expanded: boolean;
  x: TrendI18n;
}) {
  const { t } = x;
  const picker = (
    <div className="trend-toolbar">
      <label>
        {t("trend.metric")}{" "}
        <select aria-label={t("trend.metric")} value={activeKey} onChange={(event) => onPick(event.target.value)}>
          {tableMode ? <option value="">{t("trend.allRows")}</option> : null}
          {pickerKeys.map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
      </label>
    </div>
  );

  if (viz === "SINGLE_VALUE") {
    return (
      <>
        {picker}
        <SingleValue trend={trend} latest={latest} x={x} />
        {stale ? <StaleLine stale={stale} x={x} /> : null}
      </>
    );
  }

  if (tableMode && groups) {
    return (
      <>
        {picker}
        <ShapeNote viz={viz} x={x} />
        <GroupsTable
          groups={groups}
          metricKey={activeKey}
          executions={executions}
          expanded={expanded}
          honourRange={honourRange}
          variant={variant}
          x={x}
        />
        {stale ? <StaleLine stale={stale} x={x} /> : null}
      </>
    );
  }

  if (!trend || trend.points.length === 0) {
    return (
      <div className={variant === "widget" ? "tw-state" : "notice"}>
        <p>{t("trend.noPoints")}</p>
      </div>
    );
  }

  return (
    <>
      {picker}
      <div ref={onChart} className="chart" role="img" aria-label={t("trend.chartLabel")} />
      <p className="subtitle trend-note">
        {t("trend.note.points", { count: trend.count, shown: trendCount(trend.count, x.locale) })}
        <code>{trend.metric_key}</code>
        {trend.threshold_value != null
          ? t("trend.note.threshold", {
              operator: trend.threshold_operator ?? "",
              value: trend.threshold_value,
            })
          : ""}
        {versionBoundaries(trend.points).length > 0 ? t("trend.note.versions") : ""}
        {gapCount > 0 ? t("trend.note.gaps", { count: gapCount, shown: trendCount(gapCount, x.locale) }) : ""}
      </p>
      {stale ? <StaleLine stale={stale} x={x} /> : null}
    </>
  );
}

function ShapeNote({ viz, x }: { viz: TrendVisualization; x: TrendI18n }) {
  if (viz === "PIE") {
    return <p className="subtitle trend-note">{x.t("trend.shape.pie")}</p>;
  }
  if (viz === "HEATMAP") {
    return <p className="subtitle trend-note">{x.t("trend.shape.heatmap")}</p>;
  }
  return null;
}

function SingleValue({
  trend,
  latest,
  x,
}: {
  trend: SearchResultTrend | null;
  latest: SearchExecution | null;
  x: TrendI18n;
}) {
  const { t } = x;
  if (latest?.truncated) {
    const count = resultCount(latest);
    const when = latest.completed_at ?? latest.started_at;
    const verdict = latest.threshold_breached
      ? t("trend.single.thresholdBreached")
      : t("trend.single.thresholdNoVerdict");
    return (
      <div>
        <p className="trend-value">
          {count.kind === "lower-bound" && count.value != null ? (
            <LowerBoundValue value={count.value} x={x} />
          ) : (
            t("trend.single.truncatedNoCount")
          )}
        </p>
        <p className="subtitle trend-note">
          {count.kind === "lower-bound" ? (
            <>
              {t("trend.single.rowsAtBefore")}
              <UtcTime value={when} />
              {t("trend.single.rowsAtAfter")}
            </>
          ) : (
            <>
              <UtcTime value={when} />
              {t("trend.single.timeOnlyAfter")}
            </>
          )}
          {verdict}
          {t("trend.single.olderSuccess")}
        </p>
      </div>
    );
  }
  const measured = trend
    ? [...trend.points].reverse().find((point) => !FAILED_STATUSES.has(point.execution_status))
    : undefined;
  if (!measured) {
    return <p className="subtitle">{t("trend.single.none")}</p>;
  }
  return (
    <div>
      <p className="trend-value">
        <span className="num">{trendCount(measured.value, x.locale)}</span>
      </p>
      <p className="subtitle trend-note">
        {t("trend.single.measuredBefore")}
        <UtcTime value={measured.bucket_start} />
        {t("trend.single.measuredAfter")}
      </p>
    </div>
  );
}

function GroupsTable({
  groups,
  metricKey,
  executions,
  expanded,
  honourRange,
  variant,
  x,
}: {
  groups: LatestGroups;
  metricKey: string;
  executions: SearchExecution[] | undefined;
  expanded: boolean;
  honourRange: boolean;
  variant: "page" | "widget";
  x: TrendI18n;
}) {
  const { t } = x;
  const shown = metricKey ? groups.rows.filter((row) => row.metric_key === metricKey) : groups.rows;
  const run = executions?.find((execution) => execution.id === groups.execution_id) ?? null;
  const verdict = groups.values_are_lower_bounds
    ? run?.threshold_breached
      ? t("trend.groups.breached")
      : t("trend.groups.noVerdict")
    : run?.threshold_breached
      ? t("trend.groups.breached")
      : null;
  const cap = expanded ? 500 : 50;
  const figures = {
    shown: trendCount(shown.length, x.locale),
    total: trendCount(groups.total, x.locale),
  };
  return (
    <>
      <TableScroll label={t("trend.groups.label")}>
        <table className="table-dense">
          <caption className="subtitle trend-note">
            {t("trend.groups.captionStart")}
            {groups.started_at ? (
              <>
                {t("trend.groups.captionTime")}
                <UtcTime value={groups.started_at} />
              </>
            ) : (
              ""
            )}
            {t("trend.groups.captionEnd")}
            {groups.total > shown.length ? t("trend.groups.captionShowing", figures) : ""}
          </caption>
          <thead>
            <tr>
              <th scope="col">{t("trend.groups.metric")}</th>
              <th scope="col">{t("trend.groups.dimensions")}</th>
              <th scope="col" className="num">
                {t("trend.groups.value")}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr>
                <td colSpan={3}>{t("trend.groups.noRow")}</td>
              </tr>
            ) : (
              shown.map((row) => (
                <tr key={`${row.metric_key}:${formatDimensions(row.dimensions)}`}>
                  <td>
                    <code>{row.metric_key}</code>
                  </td>
                  <td className="trend-dims">{formatDimensions(row.dimensions)}</td>
                  <td className="num">
                    {groups.values_are_lower_bounds ? (
                      <LowerBoundValue value={row.value} x={x} />
                    ) : (
                      trendCount(row.value, x.locale)
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </TableScroll>
      {groups.values_are_lower_bounds ? (
        <p className="subtitle trend-note">
          {t("trend.groups.lowerBound")}
          {verdict ? t("trend.groups.thresholdIn", { verdict }) : ""}
        </p>
      ) : verdict ? (
        <p className="subtitle trend-note">{t("trend.groups.threshold", { verdict })}</p>
      ) : null}
      {groups.total > shown.length ? (
        <p className="subtitle trend-note">
          {t("trend.groups.showing", {
            ...figures,
            keeps: cap < groups.total ? t("trend.groups.keeps", { cap: trendCount(cap, x.locale) }) : "",
          })}
        </p>
      ) : null}
      {honourRange && variant === "widget" ? (
        <p className="subtitle trend-note">{t("trend.groups.latestOnly")}</p>
      ) : null}
    </>
  );
}
