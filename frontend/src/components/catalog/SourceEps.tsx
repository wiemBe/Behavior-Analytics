"use client";

// Observed against expected EPS, source mode (UR.5.1): one monitored log source's EPS against its
// seasonal expected range. The fleet form is `chart.eps_fleet_total`. The config is the source;
// the layout stores its id only, and the title is read from the monitored-source list.
//
// A source that is not chosen, or no longer monitored, is a sentence with Configure. It never
// falls back to another source's data.

import { useMemo } from "react";

import { WidgetFrame, WidgetLoading, secondary } from "@/components/home/WidgetFrame";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type BucketSeries, type MetricBucket } from "@/lib/api";
import { badgeText } from "@/lib/home/qualifiers";
import { RANGE_TEXT, useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";

import { ConfigNotice } from "./ConfigNotice";
import { SourceEpsChart } from "./SourceEpsChart";
import { hasMeasured, mapResource, seriesQualifiers, sourceCellLookup } from "./subject";

import "./catalog.css";

export interface SourceEpsConfig {
  source: string | null;
}

type Body =
  | { kind: "notice"; lead: string; text: string; hint: string }
  | { kind: "series"; series: BucketSeries<MetricBucket> };

const idle = async (): Promise<never> => {
  throw new Error("not read");
};

const NOT_CHOSEN: Body = {
  kind: "notice",
  lead: "Choose a log source.",
  text: "No log source is set for this widget, so there is nothing to chart.",
  hint: "Open Customize, then Configure, to choose one.",
};
const GONE: Body = {
  kind: "notice",
  lead: "This log source is no longer monitored.",
  text: "The source set for this widget is not in the monitored list any more. No other source is drawn in its place.",
  hint: "Open Customize, then Configure, to choose another.",
};

const INSUFFICIENT =
  "No reliable expected range for this window yet: the baseline cells for these weekdays and hours are still learning. EPS is drawn without a band, and nothing is judged.";

export function SourceEps({
  id = "single",
  config,
  range,
}: { id?: string; config?: Partial<SourceEpsConfig> } & RangedProps) {
  const window = useEffectiveRange(range);
  const expanded = useExpanded();
  const sourceId = typeof config?.source === "string" && config.source !== "" ? config.source : null;

  const seriesRead = sourceId ? homeReads.sourceSeries(sourceId, window) : null;
  const cellsRead = sourceId ? homeReads.sourceCells(sourceId) : null;
  const listRead = sourceId ? homeReads.sourceBehaviors() : null;
  const series = useApiResource(seriesRead?.key ?? null, seriesRead?.fetch ?? idle);
  const cellsState = useApiResource(cellsRead?.key ?? null, cellsRead?.fetch ?? idle);
  const list = useApiResource(listRead?.key ?? null, listRead?.fetch ?? idle);

  // The monitored list is complete (not paged), so a source missing from it is gone.
  const found = list.status === "ok" ? list.data.find((s) => s.log_source_id === sourceId) : undefined;
  const gone =
    sourceId !== null &&
    ((series.status === "error" && series.httpStatus === 404) ||
      (list.status === "ok" && found === undefined));
  const label = found?.name ?? null;

  const cells = secondary(cellsState);
  const lookup = useMemo(() => (cells.data ? sourceCellLookup(cells.data) : null), [cells.data]);
  const qualifiers = useMemo(() => {
    if (series.status !== "ok") return [];
    const { buckets, total, truncated } = series.data;
    return seriesQualifiers(
      buckets.map((b) => ({ start: b.bucket_start, completeness: b.completeness })),
      lookup,
      truncated ? { shown: buckets.length, total } : null,
      lookup ? INSUFFICIENT : "The expected range could not be read, so none is drawn.",
    );
  }, [series, lookup]);

  let state: Parameters<typeof WidgetFrame<Body>>[0]["state"];
  if (sourceId === null) state = { kind: "ok", data: NOT_CHOSEN, asOf: null };
  else if (gone) state = { kind: "ok", data: GONE, asOf: null };
  else state = mapResource(series, (s): Body => ({ kind: "series", series: s }));

  return (
    <WidgetFrame
      id={`eps-source-${id}`}
      title={label ?? "Log source EPS"}
      windowText={sourceId !== null && !gone ? RANGE_TEXT[window] : undefined}
      badge={badgeText(qualifiers) || undefined}
      link={
        sourceId !== null && !gone
          ? { href: `/behavior/sources/${sourceId}`, label: "Source" }
          : { href: "/behavior/sources", label: "Sources" }
      }
      state={state}
      what="the source's EPS series"
      permission="read:anomalies"
      fillBody
    >
      {({ data }) => {
        if (data.kind === "notice") return <ConfigNotice {...data} />;
        const { buckets } = data.series;
        if (buckets.length === 0) {
          return (
            <p className="tw-state">
              <strong>No metric buckets stored.</strong> None was stored for this source in the
              last {RANGE_TEXT[window]}. That is an absence of collection, not zero EPS.
            </p>
          );
        }
        if (cells.pending) return <WidgetLoading what="the expected range" />;
        const facts = buckets.map((b) => ({ start: b.bucket_start, completeness: b.completeness }));
        if (!hasMeasured(facts)) {
          return (
            <p className="tw-state">
              <strong>No bucket was fully collected.</strong> No line is drawn; a bucket that was
              not collected is not zero EPS.
            </p>
          );
        }
        const complete = facts.filter((b) => b.completeness === "COMPLETE").length;
        return (
          <>
            {expanded && label ? (
              <p className="tw-state">
                <strong>{label}</strong> events per second, last {RANGE_TEXT[window]}.
              </p>
            ) : null}
            <SourceEpsChart
              buckets={buckets}
              lookup={lookup}
              ariaLabel={`${label ?? "Log source"} EPS against the expected range, last ${RANGE_TEXT[window]}`}
              textSummary={`${complete} of ${buckets.length} stored buckets were fully collected in the last ${RANGE_TEXT[window]}.`}
            />
            {qualifiers
              .filter((q) => q.kind === "INSUFFICIENT_DATA")
              .map((q) => (
                <p className="tw-note" key={q.kind}>
                  {q.kind === "INSUFFICIENT_DATA" ? q.reason : ""}
                </p>
              ))}
          </>
        );
      }}
    </WidgetFrame>
  );
}
