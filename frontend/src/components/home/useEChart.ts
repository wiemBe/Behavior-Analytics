"use client";

// Creates an ECharts instance on mount, disposes it on unmount and resizes it with its
// container, as `VolumeChart` does. The option is built inside the effect because it
// reads the palette from the computed style, which exists only in the browser. Each
// chart registers the ECharts modules it needs itself (`echarts.use`), so nothing here
// pulls the full bundle.

import * as echarts from "echarts/core";
import { useEffect, useRef } from "react";

import { readHomePalette, type HomePalette } from "@/lib/home/chartPalette";

import type { EChartsOption } from "echarts";

export interface ChartClick {
  seriesName?: string;
  dataIndex?: number;
}

/**
 * Pass `null` while there is nothing to draw. The returned ref goes on the container,
 * which must be rendered whenever `build` is not null. Memoize `build` on the model.
 */
export function useEChart(
  build: ((palette: HomePalette) => EChartsOption) | null,
  onClick?: (click: ChartClick) => void,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !build) return;
    const chart = echarts.init(el);
    chart.setOption(build(readHomePalette()));
    chart.on("click", (e: unknown) => clickRef.current?.(e as ChartClick));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [build]);

  return containerRef;
}
