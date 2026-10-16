"use client";

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

import type { CustomRulePreview } from "@/lib/api";
import { readChartPalette, type ChartPalette } from "@/lib/chartPalette";
import { formatDateTime } from "@/lib/health";

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

type AreaPair = [
  { xAxis: number; itemStyle: { color: string }; name: string },
  { xAxis: number },
];

export function previewTooltip(
  preview: CustomRulePreview,
  subjectLabel: string,
  dataIndex: number,
): string {
  const bucket = preview.buckets[dataIndex];
  if (!bucket) return "";
  const safeSubject = echarts.format.encodeHTML(subjectLabel);
  const value =
    bucket.measurement === "NOT_MEASURED" || bucket.value === null
      ? "Not measured"
      : bucket.measurement === "LOWER_BOUND"
        ? `At least ${bucket.value} ${preview.unit}`
        : `${bucket.value} ${preview.unit}`;
  return [
    safeSubject,
    formatDateTime(bucket.bucket_start),
    `Reading: ${value}`,
    `Measurement: ${bucket.measurement}`,
    bucket.completeness ? `Completeness: ${bucket.completeness}` : "",
    bucket.in_maintenance ? "Maintenance: yes" : "Maintenance: no",
  ]
    .filter(Boolean)
    .join("<br/>");
}

export function previewText(preview: CustomRulePreview, subjectLabel: string): string {
  const lowerBounds = preview.buckets.filter((bucket) => bucket.measurement === "LOWER_BOUND");
  const maintenance = preview.buckets.filter((bucket) => bucket.in_maintenance).length;
  const lowerText =
    lowerBounds.length === 0
      ? "No lower-bound buckets."
      : `${lowerBounds.length} lower-bound ${lowerBounds.length === 1 ? "bucket reads" : "buckets read"} as at least the reported value.`;
  return `${subjectLabel || "Selected subject"}. Would have fired ${preview.fired_count} times. ${preview.measured_count} measured and ${preview.unmeasured_count} unmeasured buckets. ${lowerText} ${maintenance} maintenance ${maintenance === 1 ? "bucket" : "buckets"}.`;
}

export function buildPreviewOption(
  preview: CustomRulePreview,
  subjectLabel: string,
  palette: ChartPalette,
): EChartsOption {
  const times = preview.buckets.map((bucket) => Date.parse(bucket.bucket_start));
  const lastTime = times[times.length - 1] ?? Date.now();
  const areas: AreaPair[] = [
    ...preview.breach_intervals.map(
      (interval): AreaPair => [
        {
          xAxis: Date.parse(interval.start),
          itemStyle: { color: palette.anomaly },
          name: "Breach interval",
        },
        { xAxis: interval.end ? Date.parse(interval.end) : lastTime },
      ],
    ),
    ...preview.buckets
      .filter((bucket) => bucket.in_maintenance)
      .map(
        (bucket): AreaPair => [
          {
            xAxis: Date.parse(bucket.bucket_start),
            itemStyle: { color: palette.gap },
            name: "Maintenance bucket",
          },
          { xAxis: Date.parse(bucket.bucket_start) + 300_000 },
        ],
      ),
  ];
  const text = { color: palette.label, fontFamily: palette.font };
  return {
    animation: false,
    useUTC: true,
    grid: { left: 68, right: 24, top: 28, bottom: 52 },
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
        return previewTooltip(preview, subjectLabel, first?.dataIndex ?? -1);
      },
    },
    xAxis: {
      type: "time",
      name: "Time (UTC)",
      nameLocation: "middle",
      nameGap: 32,
      nameTextStyle: text,
      axisLabel: text,
      axisLine: { lineStyle: { color: palette.axis } },
    },
    yAxis: {
      type: "value",
      name: preview.unit,
      nameTextStyle: text,
      min: 0,
      axisLabel: text,
      axisLine: { lineStyle: { color: palette.axis } },
      splitLine: { lineStyle: { color: palette.grid } },
    },
    series: [
      {
        type: "line",
        name: `Reading (${preview.unit})`,
        data: preview.buckets.map((bucket) => [
          Date.parse(bucket.bucket_start),
          bucket.measurement === "NOT_MEASURED" ? null : bucket.value,
        ]),
        connectNulls: false,
        showSymbol: false,
        lineStyle: { color: palette.observed, width: 1.5 },
        itemStyle: { color: palette.observed },
        markLine: {
          silent: true,
          symbol: "none",
          label: {
            show: true,
            position: "insideEndTop",
            formatter: `Threshold ${preview.threshold_value} ${preview.unit}`,
            color: palette.tooltipText,
          },
          lineStyle: { color: palette.expected, type: "dashed" },
          data: [{ yAxis: preview.threshold_value }],
        },
        markArea: {
          silent: true,
          label: { show: false },
          data: areas,
        },
        markPoint: {
          silent: true,
          symbol: "diamond",
          symbolSize: 9,
          label: { show: false },
          itemStyle: { color: palette.warn },
          data: preview.buckets.flatMap((bucket) =>
            bucket.measurement === "LOWER_BOUND" && bucket.value !== null
              ? [{
                  coord: [Date.parse(bucket.bucket_start), bucket.value],
                  name: `At least ${bucket.value} ${preview.unit}`,
                }]
              : [],
          ),
        },
      },
    ],
  };
}

export function PreviewChart({ preview, subjectLabel }: { preview: CustomRulePreview; subjectLabel: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const text = useMemo(() => previewText(preview, subjectLabel), [preview, subjectLabel]);

  useEffect(() => {
    const element = ref.current;
    if (!element || preview.buckets.length === 0) return;
    const chart = echarts.init(element);
    chart.setOption(buildPreviewOption(preview, subjectLabel, readChartPalette()));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [preview, subjectLabel]);

  if (preview.buckets.length === 0) {
    return (
      <div className="notice">
        <p><strong>No buckets fall in this preview window.</strong></p>
        <p>Choose a longer window or wait for the selected subject to be collected.</p>
      </div>
    );
  }

  return (
    <>
      <div ref={ref} className="cr-chart" role="img" aria-label={text} />
      <p className="sr-only">{text}</p>
    </>
  );
}

export function PreviewSummary({ preview }: { preview: CustomRulePreview }) {
  const total = preview.measured_count + preview.unmeasured_count;
  const majority = total > 0 && preview.unmeasured_count > total / 2;
  const lowerBounds = preview.buckets.filter((bucket) => bucket.measurement === "LOWER_BOUND");
  const maintenance = preview.buckets.filter((bucket) => bucket.in_maintenance).length;
  return (
    <div className="cr-preview-summary" aria-label="Preview summary">
      <p><strong>Would have fired {preview.fired_count} times</strong></p>
      <dl>
        <div><dt>Measured</dt><dd className="num">{preview.measured_count} buckets</dd></div>
        <div><dt>Unmeasured</dt><dd className="num">{preview.unmeasured_count} buckets</dd></div>
        <div><dt>Lower bound</dt><dd className="num">{lowerBounds.length} buckets</dd></div>
        <div><dt>Maintenance</dt><dd className="num">{maintenance} buckets</dd></div>
      </dl>
      {majority && <p className="cr-honesty">Most buckets in this window were not measured, so this preview cannot describe the period reliably.</p>}
      {preview.measured_count === 0 && <p className="cr-honesty">Every bucket is unmeasured. The gaps are not zero and no healthy verdict is implied.</p>}
      {lowerBounds.length > 0 && <p className="cr-honesty">Diamond marks are lower bounds: each reading is at least the value shown.</p>}
      {maintenance > 0 && <p className="cr-honesty">Maintenance buckets are shaded and do not open, refresh or resolve a breach.</p>}
    </div>
  );
}
