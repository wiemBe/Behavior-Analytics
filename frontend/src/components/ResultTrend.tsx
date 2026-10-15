"use client";

// Result-trend chart for one scheduled search.
//
// ECharts is imported here and nowhere else: it touches `document` and
// `ResizeObserver` on construction, so it must never be pulled into a server
// component. The page stays a server component and passes only the search id.

import { LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, useState } from "react";

import { actionErrorMessage, api, type ResultMetricPoint, type SearchResultTrend } from "@/lib/api";
import { readChartPalette } from "@/lib/chartPalette";
import { formatDateTime } from "@/lib/health";
import { axisTimeLabel, formatRunDuration, isolatedIndices, trendRows } from "@/lib/searchRuns";

import type { EChartsOption } from "echarts";

// Modular registration rather than `import * as echarts from "echarts"`: the
// full bundle costs ~340 kB on this route, and the trend needs one chart type
// and four components.
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

const FAILED_STATUSES = new Set(["FAILED", "TIMEOUT", "CANCELLED"]);

/** Indices where the query version changes — results either side of an AQL
 *  change are not comparable, so the chart must say where the break is. */
function versionBoundaries(points: ResultMetricPoint[]): ResultMetricPoint[] {
  return points.filter((p, i) => i > 0 && p.query_version !== points[i - 1].query_version);
}

export function ResultTrend({
  searchId,
  metricKey = "total",
  truncatedAt = [],
}: {
  searchId: string;
  metricKey?: string;
  /** Times of truncated runs. They have no stored point; each becomes a gap. */
  truncatedAt?: string[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  const [trend, setTrend] = useState<SearchResultTrend | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Guards against a late response from a previous searchId overwriting a
    // newer one, and against setState after unmount.
    let active = true;
    setLoading(true);
    setError(null);
    api
      .searchResults(searchId, metricKey)
      .then((data) => {
        if (active) setTrend(data);
      })
      .catch((err) => {
        if (active) setError(actionErrorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [searchId, metricKey]);

  // A new array identity on every render would rebuild the chart each time;
  // the times themselves are what the chart depends on.
  const truncatedKey = truncatedAt.join("|");
  const rows = useMemo(
    () => (trend ? trendRows(trend.points, truncatedKey ? truncatedKey.split("|") : []) : []),
    [trend, truncatedKey],
  );
  const gapCount = rows.filter((r) => r.point === null).length;

  const option = useMemo<EChartsOption | null>(() => {
    if (!trend || trend.points.length === 0) return null;
    const points = trend.points;

    // A failed run has no trustworthy count. Plotting it as 0 would invent a
    // cliff that never happened, so the line is broken with null and the run is
    // marked separately.
    //
    // A truncated run has no point at all (its count is a cap, not a
    // measurement), so its row is null too. connectNulls stays false, which is
    // what breaks the line there instead of bridging the neighbours.
    const values = rows.map((r) =>
      r.point === null || FAILED_STATUSES.has(r.point.execution_status) ? null : r.point.value,
    );

    const failures = rows
      .map((r, i) =>
        r.point !== null && FAILED_STATUSES.has(r.point.execution_status)
          ? { coord: [i, 0], value: r.point.execution_status }
          : null,
      )
      .filter((x): x is { coord: number[]; value: string } => x !== null);

    const boundaries = versionBoundaries(points);
    const c = readChartPalette();
    const text = { color: c.label, fontFamily: c.font };
    const lone = isolatedIndices(values);

    return {
      grid: { left: 56, right: 24, top: 32, bottom: 56 },
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
          const idx = first?.dataIndex;
          if (idx == null || !rows[idx]) return "";
          const row = rows[idx];
          if (row.point === null) {
            return [
              formatDateTime(row.at),
              "Truncated run: no result count recorded",
              "The search hit its row cap, so only a lower bound exists",
            ].join("<br/>");
          }
          const p = row.point;
          const count = FAILED_STATUSES.has(p.execution_status)
            ? "no result (run failed)"
            : String(p.value);
          return [
            formatDateTime(p.bucket_start),
            `Result count: ${count}`,
            `Status: ${p.execution_status}`,
            `Duration: ${formatRunDuration(p.duration_ms)}`,
            `Query version: v${p.query_version}`,
            p.threshold_breached ? "Threshold: breached" : "",
          ]
            .filter(Boolean)
            .join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: rows.map((r) => axisTimeLabel(r.at)),
        name: "Time (UTC)",
        nameLocation: "middle",
        nameGap: 32,
        nameTextStyle: text,
        axisLine: { lineStyle: { color: c.axis } },
        axisLabel: { ...text, hideOverlap: true },
      },
      yAxis: {
        type: "value",
        name: metricKey === "total" ? "Result rows" : metricKey,
        nameTextStyle: { ...text, align: "left" },
        min: 0,
        splitLine: { lineStyle: { color: c.grid } },
        axisLine: { lineStyle: { color: c.axis } },
        axisLabel: text,
      },
      series: [
        {
          type: "line",
          name: metricKey,
          data: values,
          // Leave a visible gap rather than bridging a failed run.
          connectNulls: false,
          // No marker on every point: a dense series reads as a string of
          // beads. A point with no measured neighbour would be invisible as a
          // line, so only those get a mark (below).
          showSymbol: false,
          lineStyle: { color: c.observed, width: 1.5 },
          itemStyle: { color: c.observed },
          markLine: boundaries.length
            ? {
                silent: true,
                symbol: "none",
                label: {
                  formatter: (p: { name?: string }) => p.name ?? "",
                  color: c.warn,
                  position: "insideEndTop",
                },
                lineStyle: { color: c.warn, type: "dashed" },
                data: boundaries.map((b) => ({
                  xAxis: rows.findIndex((r) => r.point === b),
                  name: `v${b.query_version}`,
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
                      name: "Measured run between gaps",
                      coord: [i, values[i] as number],
                      symbol: "circle",
                      symbolSize: 5,
                      itemStyle: { color: c.observed },
                    })),
                    ...failures.map((f) => ({
                      name: f.value,
                      coord: f.coord,
                      symbol: "pin",
                      symbolSize: 44,
                      itemStyle: { color: c.crit },
                      label: { show: true, color: c.tooltipText, fontSize: 12, formatter: "fail" },
                    })),
                  ],
                }
              : undefined,
        },
        ...(trend.threshold_value != null
          ? [
              {
                type: "line" as const,
                name: "threshold",
                data: rows.map(() => trend.threshold_value),
                showSymbol: false,
                lineStyle: { color: c.crit, type: "dotted" as const, width: 1 },
                itemStyle: { color: c.crit },
                tooltip: { show: false },
              },
            ]
          : []),
      ],
    };
  }, [trend, metricKey, rows]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !option) return;

    const chart = echarts.init(el);
    chartRef.current = chart;
    chart.setOption(option);

    // ECharts does not track element size on its own; without this the chart
    // keeps its first-paint width when the layout changes.
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);

    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [option]);

  if (loading) {
    return <div className="notice">Loading result trend…</div>;
  }
  if (error) {
    return (
      <div className="notice" role="alert">
        {error}
      </div>
    );
  }
  if (!trend || trend.points.length === 0) {
    return (
      <div className="notice">
        No result metrics recorded yet. The trend appears once this search has run.
      </div>
    );
  }

  return (
    <>
      <div ref={containerRef} className="chart" role="img" aria-label="Search result trend" />
      <p className="subtitle trend-note">
        {trend.count} point{trend.count === 1 ? "" : "s"} · metric <code>{trend.metric_key}</code>
        {trend.threshold_value != null
          ? ` · threshold ${trend.threshold_operator} ${trend.threshold_value}`
          : ""}
        {versionBoundaries(trend.points).length > 0
          ? " · dashed lines mark query-version changes; results across versions are not comparable"
          : ""}
        {gapCount > 0
          ? ` · ${gapCount} truncated run${gapCount === 1 ? "" : "s"} left a gap: no count was stored, so the line is broken there`
          : ""}
      </p>
    </>
  );
}
