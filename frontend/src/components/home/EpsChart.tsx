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
import { useMemo } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type FleetEpsSeries } from "@/lib/api";
import { epsChartModel, epsSummaryText, windowText, type EpsChartModel } from "@/lib/home/epsChart";
import type { HomePalette } from "@/lib/home/chartPalette";
import { badgeText } from "@/lib/home/qualifiers";
import { useEffectiveRange, type RangedProps } from "@/widgets/runtime";

import { buildEpsOption, EXPECTED_NAME } from "./chartOptions";
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

const EXPECTED_MEANING =
  "The sum of every source's expected EPS, drawn only where every source has a reliable baseline. It is not a range.";

function Chart({ series, model }: { series: FleetEpsSeries; model: EpsChartModel }) {
  const option = useMemo(
    () => (model.measured > 0 ? (p: HomePalette) => buildEpsOption(model, p) : null),
    [model],
  );
  const ref = useEChart(option);
  const label = `Fleet EPS, ${windowText(series.window)}`;

  if (model.measured === 0) {
    // Nothing measured: the reason replaces the chart, and no empty axes stand in for it.
    const q = model.qualifiers[0];
    return (
      <p className="tw-state">
        {q && q.kind === "NOT_COLLECTED" ? q.reason : "No step was measured."} No line is drawn; a
        step that was not measured is not zero traffic.
      </p>
    );
  }
  return (
    <>
      <div ref={ref} className="hc-chart" role="img" aria-label={label} />
      <p className="sr-only">{epsSummaryText(series, model)}</p>
      <Key model={model} />
    </>
  );
}

function Key({ model }: { model: EpsChartModel }) {
  if (model.measured === 0) return <span />;
  return (
    <ul className="hc-key-list" aria-label="Chart key">
      <li>
        <span className="chart-key chart-key-observed" aria-hidden="true" />
        Observed
      </li>
      {model.hasExpected && (
        <li title={EXPECTED_MEANING}>
          <span className="chart-key chart-key-expected" aria-hidden="true" />
          {EXPECTED_NAME}
        </li>
      )}
      {model.gaps.length > 0 && (
        <li title="Intervals where not every source-interval was complete. They are not zero traffic.">
          <span className="chart-key chart-key-gap" aria-hidden="true" />
          Not measured
        </li>
      )}
    </ul>
  );
}

export function EpsChart({ range }: RangedProps = {}) {
  const window = useEffectiveRange(range);
  const read = homeReads.epsSeries(window, "none");
  const state = useApiResource(read.key, read.fetch);
  const data = state.status === "ok" ? state.data : null;
  const model = useMemo(() => (data ? epsChartModel(data) : null), [data]);

  return (
    <WidgetFrame
      id="home-eps"
      title="Fleet EPS"
      windowText={windowText(window)}
      badge={(model && badgeText(model.qualifiers)) || undefined}
      link={{ href: "/fleet", label: "Fleet" }}
      state={state}
      what="the fleet EPS series"
      permission="read:fleet"
      fillBody
    >
      {(ok) => model && <Chart series={ok.data} model={model} />}
    </WidgetFrame>
  );
}
