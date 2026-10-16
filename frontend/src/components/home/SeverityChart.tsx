"use client";

// Anomalies per hour by severity (UR.4.3): stacked columns over the last 24 UTC hours,
// from the capped list GET /anomalies?since&until&limit=200.
//
// When the list's total exceeds what came back, every column is a lower bound: the
// header says "Truncated: first 200 of n", the axis says "at least" and the tooltip says
// "at least", so a capped stack is never read as the whole. A column links to the
// anomalies list for that hour and severity.
//
// A complete list with nothing in the window is an empty window: one sentence, no plot.
// That is the backend's empty page (items and total both 0) and a complete page whose
// rows all fall outside the window. A capped list that happens to show nothing in the
// window is not that sentence, and a failed read never reaches this body.

import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { useRefreshTick } from "@/components/shell/AutoRefresh";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { severityVar, type HomePalette } from "@/lib/home/chartPalette";
import { badgeText } from "@/lib/home/qualifiers";
import {
  lastHours,
  severityPerHour,
  severityText,
  type SeverityPerHour,
} from "@/lib/home/severityPerHour";

import { buildSeverityOption, stackOrder } from "./chartOptions";
import { useEChart } from "./useEChart";
import { WidgetFrame } from "./WidgetFrame";

import "./charts.css";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

const HOURS = 24;
const HOUR_MS = 3_600_000;
/** The header and the empty sentence share this, taken from the hour span the chart reads. */
const WINDOW_LABEL = `${HOURS} h`;

/** The anomalies list for a range, and for one severity within it. */
export function anomaliesHref(since: string, until: string, severity?: string): string {
  const q = new URLSearchParams({ since, until });
  if (severity) q.set("severity", severity);
  return `/anomalies?${q.toString()}`;
}

const word = (s: string) => `${s.charAt(0)}${s.slice(1).toLowerCase()}`;

function Columns({
  model,
  range,
}: {
  model: SeverityPerHour;
  range: { since: string; until: string };
}) {
  const router = useRouter();
  const option = useMemo(() => (p: HomePalette) => buildSeverityOption(model, p), [model]);
  const ref = useEChart(option, (click) => {
    const hour = click.dataIndex != null ? model.hours[click.dataIndex] : undefined;
    if (!hour) return;
    const end = new Date(Date.parse(hour.start) + HOUR_MS).toISOString();
    router.push(anomaliesHref(hour.start, end, click.seriesName));
  });
  return (
    <>
      <div
        ref={ref}
        className="hc-chart"
        role="img"
        aria-label="Anomalies per hour by severity, last 24 hours"
      />
      <p className="sr-only">{severityText(model, range)}</p>
      <ul className="hc-key-list" aria-label="Severity key">
        {stackOrder(model.severities).map((s) => (
          <li key={s}>
            <svg className="hc-swatch" viewBox="0 0 1 1" aria-hidden="true" focusable="false">
              <rect width="1" height="1" fill={severityVar(s)} />
            </svg>
            {word(s)}
          </li>
        ))}
      </ul>
    </>
  );
}

/**
 * What the body says when the window has no column to draw. A complete read with
 * nothing counted is an empty window. A capped read is not: zero rows seen is not
 * "no anomaly". A total that contradicts the rows is not a count either.
 */
function emptyWindow(
  model: SeverityPerHour,
  total: number,
  itemCount: number,
): "empty" | "capped" | "uncounted" | null {
  const counted = model.hours.reduce((sum, hour) => sum + hour.total, 0);
  // No hours means the range was rejected. That is not an empty window.
  if (counted > 0 || model.hours.length === 0) return null;
  if (model.lowerBound) return "capped";
  if (total === itemCount) return "empty";
  return "uncounted";
}

export function SeverityChart() {
  const tick = useRefreshTick();
  // The range moves with the hour, not with the render, so the request's key is stable.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const range = useMemo(() => lastHours(Date.now(), HOURS), [tick]);
  const read = homeReads.anomaliesBetween(range.since, range.until, 200);
  const state = useApiResource(read.key, read.fetch);
  const data = state.status === "ok" ? state.data : null;
  const model = useMemo(() => (data ? severityPerHour(data, range) : null), [data, range]);

  return (
    <WidgetFrame
      id="home-severity"
      title="Anomalies by severity"
      windowText={WINDOW_LABEL}
      badge={(model && badgeText(model.qualifiers)) || undefined}
      link={{ href: anomaliesHref(range.since, range.until), label: "Anomalies" }}
      state={state}
      what="the anomaly list"
      permission="read:anomalies"
      fillBody
    >
      {(ok) => {
        if (!model) return null;
        const kind = emptyWindow(model, ok.data.total, ok.data.items.length);
        if (kind === "empty") {
          return <p className="tw-state">No anomaly opened in the last {WINDOW_LABEL}.</p>;
        }
        if (kind === "capped") {
          return (
            <p className="tw-state">
              None of the returned anomalies fall in this window. The list was capped, so this is
              not a measured zero.
            </p>
          );
        }
        if (kind === "uncounted") {
          return (
            <p className="tw-state">
              The list&apos;s total does not match the rows returned, so this window is not counted.
            </p>
          );
        }
        return <Columns model={model} range={range} />;
      }}
    </WidgetFrame>
  );
}

