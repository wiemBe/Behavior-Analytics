"use client";

// Events per bucket for one catch-all source, with the honesty of each bucket.
//
// ECharts stays in this file, as in VolumeChart, so it never reaches a server
// component. The chart's rules, each with a test:
//   * a bucket with no row is a null, so no bar is drawn: it is not zero;
//   * buckets before the latest measured one that have no row are shaded as
//     "not measured", by a full-height bar per bucket (so a one-bucket gap is as
//     visible as a long one, at any chart width); nothing after the latest
//     measured bucket is shaded or drawn;
//   * a LOWER_BOUND bucket takes the warn fill, and the legend says "at least";
//   * one value axis only; distinct senders are in the bucket details and the
//     tooltip, where a lower-bound count is flagged;
//   * every axis names its unit, and the time axis names UTC;
//   * every colour comes from the tokens through `readChartPalette`.
// Clicking a bar (or a gap) selects that bucket; the page offers the same
// selection with buttons, because a canvas cannot be operated from the keyboard.

import { BarChart } from "echarts/charts";
import {
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef, type ReactNode } from "react";

import { useFleetText } from "@/lib/fleetText";

import { HistoryText } from "@/components/behavior/HistoryKey";
import {
  historyMarkArea,
  originText,
  readHistoryShade,
} from "@/components/behavior/historyOrigin";
import { readChartPalette } from "@/lib/chartPalette";
import { utcTimeHtml } from "@/components/ui/UtcTime";

import {
  axisLabel,
  barValue,
  bucketUnit,
  formatCount,
  gapBand,
  historySpans,
  niceMax,
  type Slot,
} from "./catchall";

import type { EChartsOption } from "echarts";

echarts.use([
  BarChart,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

export function BucketChart({
  slots,
  bucketSeconds,
  selected,
  onSelect,
  ariaLabel,
  textSummary,
}: {
  slots: Slot[];
  bucketSeconds: number;
  selected: number | null;
  onSelect: (index: number) => void;
  ariaLabel: string;
  textSummary: ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // The click handler is registered once per chart; the ref keeps it current.
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  const text = useFleetText();
  const { say, locale } = text;
  const unit = say("catchall.chart.unit", { bucket: bucketUnit(bucketSeconds, text) });
  const buildOption = useMemo(() => {
    return (): EChartsOption => {
      const c = readChartPalette();
      const text = { color: c.label, fontFamily: c.font };
      const categories = slots.map((s) => String(s.t));
      const yMax = niceMax(Math.max(0, ...slots.map((s) => barValue(s) ?? 0)));
      const band = gapBand(slots, yMax);
      const shade = readHistoryShade();
      // One span per run of contiguous history buckets, on the shading series, behind the bars.
      const pastAreas = historySpans(slots).map(([first, last], i) =>
        historyMarkArea(first, last, shade, i === 0),
      );
      const reduceMotion =
        typeof window !== "undefined" &&
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      return {
        animation: !reduceMotion,
        textStyle: text,
        grid: { left: 64, right: 24, top: 32, bottom: 52 },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "line", lineStyle: { color: c.marker } },
          backgroundColor: c.tooltipBg,
          borderColor: c.tooltipBorder,
          textStyle: { color: c.tooltipText, fontFamily: c.font },
          extraCssText: "box-shadow: none;",
          formatter: (params: unknown) => {
            const list = Array.isArray(params) ? params : [params];
            const first = list[0] as { dataIndex?: number } | undefined;
            const slot = first?.dataIndex != null ? slots[first.dataIndex] : undefined;
            if (!slot) return "";
            const when = utcTimeHtml(new Date(slot.t).toISOString());
            if (!slot.bucket) {
              return [
                when,
                slot.state === "gap" ? say("catchall.chart.gap") : say("catchall.chart.beyond"),
              ].join("<br/>");
            }
            const b = slot.bucket;
            const atLeast = slot.state === "lower_bound";
            return [
              when,
              say("catchall.chart.events", {
                atLeast: atLeast ? `${say("catchall.atLeast")} ` : "",
                count: formatCount(b.events, locale),
                unit,
              }),
              say("catchall.chart.distinct", {
                atLeast: b.distinct_senders_lower_bound ? `${say("catchall.atLeast")} ` : "",
                count: formatCount(b.distinct_senders, locale),
              }),
              atLeast ? say("catchall.chart.truncated") : "",
              originText(b.origin),
            ]
              .filter(Boolean)
              .join("<br/>");
          },
        },
        xAxis: {
          type: "category",
          data: categories,
          name: say("catchall.chart.xAxis"),
          nameLocation: "middle",
          nameGap: 32,
          nameTextStyle: text,
          axisLine: { lineStyle: { color: c.axis } },
          axisTick: { show: false },
          axisLabel: { ...text, formatter: (value: string) => axisLabel(Number(value)) },
        },
        yAxis: {
          type: "value",
          name: say("catchall.chart.yAxis", { unit }),
          nameTextStyle: { ...text, align: "left" },
          min: 0,
          max: yMax,
          splitLine: { lineStyle: { color: c.grid } },
          axisLine: { lineStyle: { color: c.axis } },
          axisLabel: text,
        },
        series: [
          {
            // The not-measured shading: one full-height bar per unmeasured
            // bucket, drawn first and behind the events.
            type: "bar",
            name: say("catchall.chart.seriesGap"),
            data: band,
            silent: true,
            barGap: "-100%",
            barCategoryGap: 0,
            itemStyle: { color: c.gap, borderColor: c.gap, borderWidth: 1 },
            z: 1,
            markArea: pastAreas.length
              ? { silent: true, label: { show: false }, data: pastAreas }
              : undefined,
          },
          {
            type: "bar",
            name: say("catchall.chart.seriesEvents"),
            data: slots.map((s) => {
              const value = barValue(s);
              return value === null
                ? null
                : s.state === "lower_bound"
                  ? { value, itemStyle: { color: c.warn } }
                  : value;
            }),
            itemStyle: { color: c.observed },
            barGap: "-100%",
            barCategoryGap: "20%",
            z: 2,
            markLine:
              selected != null && slots[selected]
                ? {
                    silent: true,
                    symbol: "none",
                    label: { show: false },
                    lineStyle: { color: c.marker, type: "dashed", width: 1 },
                    data: [{ xAxis: selected }],
                  }
                : undefined,
          },
        ],
      };
    };
  }, [slots, selected, unit, say, locale]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chart.setOption(buildOption());
    const zr = chart.getZr();
    zr.on("click", (event: { offsetX: number; offsetY: number }) => {
      const point = [event.offsetX, event.offsetY];
      if (!chart.containPixel("grid", point)) return;
      const converted = chart.convertFromPixel({ xAxisIndex: 0 }, point) as unknown as number;
      if (Number.isInteger(converted) && converted >= 0) selectRef.current(converted);
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [buildOption]);

  return (
    <>
      <div ref={containerRef} className="chart" role="img" aria-label={ariaLabel} />
      <p className="sr-only">{textSummary}</p>
      <HistoryText
        rows={slots.flatMap((s) => (s.bucket ? [s.bucket] : []))}
      />
    </>
  );
}
