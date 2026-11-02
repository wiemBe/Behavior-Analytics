"use client";

// The choropleth. ECharts stays in this file's graph (with `useEChart`), as in the other charts,
// so it never reaches a server component; the outlines arrive through `useWorld`, a lazy chunk.
// Every state of the plot has its own sentence at the plot's size, so the page does not shift
// when the outlines arrive. The canvas is not the only carrier of the data: it is one named image
// whose label gives the top countries, and the table beside it lists every one.

import { MapChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { useEChart } from "@/components/home/useEChart";
import { formatCount, formatShare, type FormatLocale } from "@/lib/format";
import type { GeoCountries } from "@/lib/api";

import { countryName } from "./geoModel";
import { buildMapOption, MAP_NAME, readMapPalette } from "./mapOption";
import type { WorldState } from "./world";

import "./geo.css";

echarts.use([MapChart, TooltipComponent, CanvasRenderer]);

function reducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function GeoMap({
  data,
  world,
  locale,
  ariaLabel,
}: {
  data: GeoCountries;
  world: WorldState;
  locale: FormatLocale;
  /** What the image shows, as one sentence (the top countries are in it). */
  ariaLabel: string;
}) {
  const t = useTranslations("geo.map");
  const build = useMemo(() => {
    if (world.status !== "ok") return null;
    const { geoJson, codes } = world.world;
    return () => {
      echarts.registerMap(MAP_NAME, geoJson as Parameters<typeof echarts.registerMap>[1]);
      return buildMapOption({
        data,
        drawable: codes,
        palette: readMapPalette(),
        reduceMotion: reducedMotion(),
        text: {
          noData: t("noData"),
          events: (count, atLeast) =>
            t(atLeast ? "eventsAtLeast" : "events", { count: formatCount(count, locale) }),
          share: (share) => t("share", { share: formatShare(share, locale) }),
          name: (code) => countryName(code, locale),
        },
      });
    };
  }, [world, data, locale, t]);
  const ref = useEChart(build);

  if (world.status === "ok") {
    return <div ref={ref} className="geo-plot" role="img" aria-label={ariaLabel} />;
  }
  return (
    <div className="geo-plot geo-plot-state">
      {world.status === "loading" ? (
        <p className="tw-state" role="status">
          {t("loadingOutlines")}
        </p>
      ) : (
        <p className="tw-state" role="alert">
          <strong>{t("outlinesFailed")}</strong> {t("outlinesFailedAction")}
        </p>
      )}
    </div>
  );
}
