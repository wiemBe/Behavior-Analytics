"use client";

// The counts of one catalogue event ID over the window, in events per 5 minutes.
//
// The honesty rules live in `identitySeries.ts` and are pinned there. This component only reads the
// palette from the tokens and hands the option to ECharts.

import { LineChart } from "echarts/charts";
import { GridComponent, MarkAreaComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useRef } from "react";

import { readChartPalette } from "@/lib/chartPalette";

import {
  buildIdentityOption,
  plotCounts,
  type IdentityChartWords,
  type IdentityPalette,
  type SeriesPlot,
} from "./identitySeries";

import "./identity.css";

echarts.use([LineChart, GridComponent, TooltipComponent, MarkAreaComponent, CanvasRenderer]);

export function readIdentityPalette(root: Element = document.documentElement): IdentityPalette {
  const base = readChartPalette(root);
  return {
    line: base.observed,
    grid: base.grid,
    axis: base.axis,
    label: base.label,
    gap: base.gap,
    floor: getComputedStyle(root).getPropertyValue("--neutral-bg").trim(),
    tooltipBg: base.tooltipBg,
    tooltipBorder: base.tooltipBorder,
    tooltipText: base.tooltipText,
    font: base.font,
  };
}

export function IdentityEventChart({
  plot,
  eventLabel,
}: {
  plot: SeriesPlot;
  /** The event in words, for the accessible name: "4625 Failed logon". */
  eventLabel: string;
}) {
  const t = useTranslations("identity.chart");
  const ref = useRef<HTMLDivElement | null>(null);
  const counts = plotCounts(plot);
  const words = useMemo<IdentityChartWords>(
    () => ({
      unit: t("unit"),
      axisTime: t("axisTime"),
      notCollected: t("notCollected"),
      atFloor: t("atFloor"),
      count: (value) => t("tooltip.count", { value }),
      atLeast: (value) => t("tooltip.atLeast", { value }),
      noCount: t("tooltip.noCount"),
      namesCut: t("tooltip.namesCut"),
    }),
    [t],
  );

  useEffect(() => {
    const el = ref.current;
    if (!el || counts.exact + counts.truncated === 0) return;
    const reduce =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const chart = echarts.init(el);
    chart.setOption(buildIdentityOption(plot, readIdentityPalette(), words, reduce));
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [plot, words, counts.exact, counts.truncated]);

  if (counts.exact + counts.truncated === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("noneTitle")}</strong>
        </p>
        <p>{t("noneText", { event: eventLabel })}</p>
      </div>
    );
  }

  return (
    <div>
      <div
        ref={ref}
        className="identity-chart"
        role="img"
        aria-label={t("aria", { event: eventLabel, unit: t("unit") })}
      />
      <p className="sr-only">
        {t("summary", {
          exact: counts.exact,
          truncated: counts.truncated,
          missing: counts.notCollected,
        })}
      </p>
      <ul className="chart-legend" aria-label={t("keyLabel")}>
        <li>
          <span className="chart-key chart-key-observed" aria-hidden="true" />
          {t("keyCount")}
        </li>
        {counts.truncated > 0 && (
          <li>
            <span className="chart-key chart-key-floor" aria-hidden="true" />
            {t("keyTruncated", { count: counts.truncated })}
          </li>
        )}
        {counts.notCollected > 0 && (
          <li>
            <span className="chart-key chart-key-gap" aria-hidden="true" />
            {t("keyNotCollected", { count: counts.notCollected })}
          </li>
        )}
      </ul>
      <p className="subtitle chart-note">{t("note")}</p>
    </div>
  );
}
