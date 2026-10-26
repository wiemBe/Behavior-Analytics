"use client";

// Observed against expected EPS, source mode (UR.5.1): one monitored log source's EPS against its
// seasonal expected range. The fleet form is `chart.eps_fleet_total`. The config is the source;
// the layout stores its id only, and the title is read from the monitored-source list.
//
// A source that is not chosen, or no longer monitored, is a sentence with Configure. It never
// falls back to another source's data.

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { badgePhrase } from "@/components/home/qualifierText";
import { WidgetFrame, WidgetLoading, secondary } from "@/components/home/WidgetFrame";
import { useApiResource } from "@/hooks/useApiResource";
import { useMeasures } from "@/i18n/measures";
import { asTextTranslator } from "@/i18n/textTranslator";
import { homeReads, type BucketSeries, type MetricBucket } from "@/lib/api";
import { plainCount, useFleetText } from "@/lib/fleetText";
import { bucketsAsOf } from "@/lib/home/asOf";
import { sourceDrillHref } from "@/lib/sourceDetail";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";

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

export function SourceEps({
  id = "single",
  config,
  range,
}: { id?: string; config?: Partial<SourceEpsConfig> } & RangedProps) {
  const t = useTranslations("catalog.sourceEps");
  const copy = useWidgetCopy();
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const text = useFleetText();
  const window = useEffectiveRange(range);
  const windowWords = copy.range(window);
  const expanded = useExpanded();
  const notChosenNotice: Body = {
    kind: "notice",
    lead: t("notChosenLead"),
    text: t("notChosenText"),
    hint: t("notChosenHint"),
  };
  const goneNotice: Body = {
    kind: "notice",
    lead: t("goneLead"),
    text: t("goneText"),
    hint: t("goneHint"),
  };
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

  const cells = secondary(cellsState, say);
  const lookup = useMemo(() => (cells.data ? sourceCellLookup(cells.data) : null), [cells.data]);
  const qualifiers = useMemo(() => {
    if (series.status !== "ok") return [];
    const { buckets, total, truncated } = series.data;
    return seriesQualifiers(
      buckets.map((b) => ({ start: b.bucket_start, completeness: b.completeness })),
      lookup,
      truncated ? { shown: buckets.length, total } : null,
      lookup ? t("insufficient") : t("rangeUnread"),
    );
  }, [series, lookup, t]);

  let state: Parameters<typeof WidgetFrame<Body>>[0]["state"];
  if (sourceId === null) state = { kind: "ok", data: notChosenNotice, asOf: null };
  else if (gone) state = { kind: "ok", data: goneNotice, asOf: null };
  else state = mapResource(series, (s): Body => ({ kind: "series", series: s }));

  return (
    <WidgetFrame
      id={`eps-source-${id}`}
      title={label ?? t("title")}
      windowText={sourceId !== null && !gone ? windowWords : undefined}
      badge={badgePhrase(say, measures, qualifiers) || undefined}
      link={
        sourceId !== null && !gone
          ? { href: sourceDrillHref(sourceId, window), label: t("linkSource") }
          : { href: "/behavior/sources", label: t("linkSources") }
      }
      state={state}
      dataTo={{
        at: sourceId === null || gone || series.status !== "ok" ? null : bucketsAsOf(series.data.buckets),
        family: "PHASE_A_VOLUME",
      }}
      what={t("what")}
      permission="read:anomalies"
      fillBody
    >
      {({ data }) => {
        if (data.kind === "notice") return <ConfigNotice {...data} />;
        const { buckets } = data.series;
        if (buckets.length === 0) {
          return (
            <p className="tw-state">
              {t.rich("noBuckets", {
                range: windowWords,
                b: (chunks) => <strong>{chunks}</strong>,
              })}
            </p>
          );
        }
        if (cells.pending) return <WidgetLoading what={t("expectedRangeWhat")} />;
        const facts = buckets.map((b) => ({ start: b.bucket_start, completeness: b.completeness }));
        if (!hasMeasured(facts)) {
          return (
            <p className="tw-state">
              {t.rich("noneCollected", { b: (chunks) => <strong>{chunks}</strong> })}
            </p>
          );
        }
        const complete = facts.filter((b) => b.completeness === "COMPLETE").length;
        return (
          <>
            {expanded && label ? (
              <p className="tw-state">
                {t.rich("expandedLead", {
                  label,
                  range: windowWords,
                  b: (chunks) => <strong>{chunks}</strong>,
                })}
              </p>
            ) : null}
            <SourceEpsChart
              buckets={buckets}
              lookup={lookup}
              labelled={expanded}
              ariaLabel={t("chartLabel", { label: label ?? t("logSource"), range: windowWords })}
              textSummary={t("summary", {
                complete: plainCount(complete, text.locale),
                total: plainCount(buckets.length, text.locale),
                range: windowWords,
              })}
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
