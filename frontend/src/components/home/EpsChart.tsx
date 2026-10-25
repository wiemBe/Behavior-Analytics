"use client";

// Fleet EPS (UR.4.3): the observed line over the window, with the sum of per-source
// expected where every source has a reliable baseline. A step that was not measured is a
// gap, shaded, never a zero and never bridged (guidelines §4). There is no expected
// band: a sum of per-source medians is not the median of the sum, and a sum of p05/p95
// would be a different, much wider range.
//
// A title row (title, window, qualifier; the frame adds the window presets, Expand and the
// drill-down), the chart, and the key. What a mark means is in the tooltip, the key's `title` and the accessible
// description.

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

import { useApiResource } from "@/hooks/useApiResource";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads, type FleetEpsSeries } from "@/lib/api";
import { epsSeriesAsOf } from "@/lib/home/asOf";
import { epsChartModel, type EpsChartModel } from "@/lib/home/epsChart";
import type { HomePalette } from "@/lib/home/chartPalette";
import { fleetDrillHref } from "@/lib/fleet";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";

import "@/components/behavior/history.css";

import { buildEpsOption } from "./chartOptions";
import { epsHistory, epsHistorySummary, type EpsHistory } from "./epsHistory";
import { badgePhrase, reasonText } from "./qualifierText";
import { useEChart } from "./useEChart";
import { WidgetFrame } from "./WidgetFrame";

import "./charts.css";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

function Chart({ series, model }: { series: FleetEpsSeries; model: EpsChartModel }) {
  const t = useTranslations("home.eps");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const copy = useWidgetCopy();
  const expanded = useExpanded();
  // The tile is too small to hold the words clear of the line; the key, tooltip and text say it.
  const history = useMemo(() => epsHistory(series, expanded), [series, expanded]);
  const i18n = useMemo(() => ({ t: say, locale: measures.locale }), [say, measures.locale]);
  const option = useMemo(
    () =>
      model.measured > 0
        ? (p: HomePalette) => buildEpsOption(model, p, undefined, history, i18n)
        : null,
    [model, history, i18n],
  );
  const ref = useEChart(option);
  const windowLabel = copy.range(series.window);
  const label = t("ariaLabel", { window: windowLabel });

  if (model.measured === 0) {
    // Nothing measured: the reason replaces the chart, and no empty axes stand in for it.
    const q = model.qualifiers[0];
    return (
      <p className="tw-state">
        {q && q.kind === "NOT_COLLECTED" ? reasonText(say, q) : t("noStep")}{" "}
        {t("noLine")}
      </p>
    );
  }
  // What the chart shows, as prose for assistive technology.
  const head = t("summaryHead", { window: windowLabel });
  const summary =
    `${head} ` +
    t("summaryMeasured", {
      measured: measures.count(model.measured),
      total: measures.count(model.total),
      peak: measures.eps(model.peak as number),
    }) +
    (model.hasExpected ? ` ${t("summaryExpected")}` : ` ${t("summaryNoExpected")}`) +
    (model.measured < model.total ? ` ${t("summaryGaps")}` : "");
  return (
    <>
      <div ref={ref} className="hc-chart" role="img" aria-label={label} />
      <p className="sr-only">
        {[summary, epsHistorySummary(history, say)].filter(Boolean).join(" ")}
      </p>
      <Key model={model} history={history} />
    </>
  );
}

function Key({ model, history }: { model: EpsChartModel; history: EpsHistory }) {
  const t = useTranslations("home.eps");
  const chart = useTranslations("home.chart");
  const historyT = useTranslations("home.history");
  if (model.measured === 0) return <span />;
  return (
    <ul className="hc-key-list" aria-label={t("keyLabel")}>
      <li>
        <span className="chart-key chart-key-observed" aria-hidden="true" />
        {t("observed")}
      </li>
      {model.hasExpected && (
        <li title={t("expectedMeaning")}>
          <span className="chart-key chart-key-expected" aria-hidden="true" />
          {chart("expectedName")}
        </li>
      )}
      {model.gaps.length > 0 && (
        <li title={t("notMeasuredMeaning")}>
          <span className="chart-key chart-key-gap" aria-hidden="true" />
          {t("notMeasured")}
        </li>
      )}
      {history.spans.length > 0 && (
        <li>
          <span className="chart-key chart-key-history" aria-hidden="true" />
          {historyT("key")}
        </li>
      )}
    </ul>
  );
}

export function EpsChart({ range }: RangedProps = {}) {
  const t = useTranslations("home.eps");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const copy = useWidgetCopy();
  const window = useEffectiveRange(range);
  const read = homeReads.epsSeries(window, "none");
  const state = useApiResource(read.key, read.fetch);
  const data = state.status === "ok" ? state.data : null;
  const model = useMemo(() => (data ? epsChartModel(data) : null), [data]);

  return (
    <WidgetFrame
      id="home-eps"
      title={t("title")}
      windowText={copy.range(window)}
      badge={(model && badgePhrase(say, measures, model.qualifiers)) || undefined}
      link={{ href: fleetDrillHref(window), label: t("link") }}
      state={state}
      dataTo={{ at: data ? epsSeriesAsOf(data) : null, family: "PHASE_A_VOLUME" }}
      what={t("what")}
      permission="read:fleet"
      fillBody
    >
      {(ok) => model && <Chart series={ok.data} model={model} />}
    </WidgetFrame>
  );
}
