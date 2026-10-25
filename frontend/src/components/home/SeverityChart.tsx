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
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { useRefreshTick } from "@/components/shell/AutoRefresh";
import { useApiResource } from "@/hooks/useApiResource";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads } from "@/lib/api";
import { severityVar, type HomePalette } from "@/lib/home/chartPalette";
import { lastHours, severityPerHour, type SeverityPerHour } from "@/lib/home/severityPerHour";
import { useWidgetCopy } from "@/widgets/copy";

import { buildSeverityOption, stackOrder } from "./chartOptions";
import { badgePhrase } from "./qualifierText";
import { useEChart } from "./useEChart";
import { WidgetFrame } from "./WidgetFrame";

import "./charts.css";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

const HOURS = 24;
const HOUR_MS = 3_600_000;

/** The anomalies list for a range, and for one severity within it. */
export function anomaliesHref(since: string, until: string, severity?: string): string {
  const q = new URLSearchParams({ since, until });
  if (severity) q.set("severity", severity);
  return `/anomalies?${q.toString()}`;
}

function Columns({
  model,
  range,
}: {
  model: SeverityPerHour;
  range: { since: string; until: string };
}) {
  const router = useRouter();
  const t = useTranslations("home.severity");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const { label } = useEnumLabels();
  const option = useMemo(
    () => (p: HomePalette) =>
      buildSeverityOption(model, p, undefined, { t: say, locale: measures.locale }),
    [model, say, measures.locale],
  );
  const ref = useEChart(option, (click) => {
    const hour = click.dataIndex != null ? model.hours[click.dataIndex] : undefined;
    if (!hour) return;
    const end = new Date(Date.parse(hour.start) + HOUR_MS).toISOString();
    router.push(anomaliesHref(hour.start, end, click.seriesName));
  });
  // The stack as prose: the totals the chart cannot give a screen reader.
  const seen = model.hours.reduce((sum, hour) => sum + hour.total, 0);
  const bySeverity = model.severities
    .map(
      (s) =>
        `${measures.count(model.hours.reduce((sum, hour) => sum + (hour.counts[s] ?? 0), 0))} ${label("severity", s).toLocaleLowerCase(measures.locale === "tr" ? "tr-TR" : "en-US")}`,
    )
    .join(", ");
  const summary = t("summary", {
    since: range.since,
    until: range.until,
    bound: model.lowerBound ? t("atLeast") : "",
    seen: measures.count(seen),
    hours: measures.count(model.hours.length),
    by: bySeverity ? ` (${bySeverity})` : "",
    tail: model.lowerBound ? t("cappedTail") : ".",
  });
  return (
    <>
      <div
        ref={ref}
        className="hc-chart"
        role="img"
        aria-label={t("ariaLabel")}
      />
      <p className="sr-only">{summary}</p>
      <ul className="hc-key-list" aria-label={t("keyLabel")}>
        {stackOrder(model.severities).map((s) => (
          <li key={s}>
            <svg className="hc-swatch" viewBox="0 0 1 1" aria-hidden="true" focusable="false">
              <rect width="1" height="1" fill={severityVar(s)} />
            </svg>
            {label("severity", s)}
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
  const t = useTranslations("home.severity");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const copy = useWidgetCopy();
  const windowLabel = copy.range("24h");
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
      title={t("title")}
      windowText={windowLabel}
      badge={(model && badgePhrase(say, measures, model.qualifiers)) || undefined}
      link={{ href: anomaliesHref(range.since, range.until), label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:anomalies"
      fillBody
    >
      {(ok) => {
        if (!model) return null;
        const kind = emptyWindow(model, ok.data.total, ok.data.items.length);
        if (kind === "empty") {
          return <p className="tw-state">{t("empty", { window: windowLabel })}</p>;
        }
        if (kind === "capped") {
          return <p className="tw-state">{t("capped")}</p>;
        }
        if (kind === "uncounted") {
          return <p className="tw-state">{t("uncounted")}</p>;
        }
        return <Columns model={model} range={range} />;
      }}
    </WidgetFrame>
  );
}

