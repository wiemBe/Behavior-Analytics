"use client";

// WAF endpoint traffic (UR.5.1): one endpoint's requests per bucket against its seasonal
// band. The config is the endpoint (and the WAF source it is chosen under); the layout stores
// ids only. The title is read from the source's endpoint list, so it names the endpoint without
// the layout holding a label.
//
// The chart is the investigation page's `WafRequestChart` in its compact form: a bucket that
// was not fully collected is a gap, never a zero; the band is the cell of each bucket's UTC
// weekday and hour, absent where that cell is missing or unreliable.
//
// A subject that is not chosen, or no longer exists, is a sentence with Configure. It never
// falls back to another endpoint's data.

import { useMemo } from "react";

import { WidgetFrame, WidgetLoading, secondary } from "@/components/home/WidgetFrame";
import { WafRequestChart } from "@/components/waf/WafRequestChart";
import { buildBaselineLookup } from "@/components/waf/wafBaseline";
import { bucketWidth } from "@/components/waf/wafFormat";
import { useApiResource } from "@/hooks/useApiResource";
import { bucketsAsOf } from "@/lib/home/asOf";
import {
  homeReads,
  type BucketSeries,
  type WafBaselineCell,
  type WafMetricBucket,
} from "@/lib/api";
import { badgeText } from "@/lib/home/qualifiers";
import { endpointOptionLabel } from "@/widgets/optionSources";
import { RANGE_TEXT, useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";

import { ConfigNotice } from "./ConfigNotice";
import { mapResource, seriesQualifiers } from "./subject";

import "./catalog.css";

export interface WafTrafficConfig {
  waf_source: string | null;
  endpoint: string | null;
}

type Body =
  | { kind: "notice"; lead: string; text: string; hint: string }
  | { kind: "series"; series: BucketSeries<WafMetricBucket> };

const idle = async (): Promise<never> => {
  throw new Error("not read");
};
const text = (value: unknown) => (typeof value === "string" && value !== "" ? value : null);

const NOT_CHOSEN: Body = {
  kind: "notice",
  lead: "Choose an endpoint.",
  text: "No WAF endpoint is set for this widget, so there is nothing to chart.",
  hint: "Open Customize, then Configure, to choose one.",
};
const GONE: Body = {
  kind: "notice",
  lead: "This endpoint no longer exists.",
  text: "The WAF endpoint set for this widget is not tracked any more. No other endpoint is drawn in its place.",
  hint: "Open Customize, then Configure, to choose another.",
};

const INSUFFICIENT =
  "No reliable expected range for this window yet: the baseline cells for these weekdays and hours are still learning. Requests are drawn without a band, and nothing is judged.";

export function WafEndpointTraffic({
  id = "single",
  config,
  range,
}: { id?: string; config?: Partial<WafTrafficConfig> } & RangedProps) {
  const window = useEffectiveRange(range);
  const expanded = useExpanded();
  const sourceId = text(config?.waf_source);
  const endpointId = text(config?.endpoint);
  const configured = sourceId !== null && endpointId !== null;

  const endpointsRead = sourceId ? homeReads.wafEndpointsOf(sourceId) : null;
  const seriesRead = configured ? homeReads.wafEndpointSeries(endpointId, window) : null;
  const cellsRead = configured ? homeReads.wafEndpointCells(endpointId) : null;
  const endpoints = useApiResource(endpointsRead?.key ?? null, endpointsRead?.fetch ?? idle);
  const series = useApiResource(seriesRead?.key ?? null, seriesRead?.fetch ?? idle);
  const cellsState = useApiResource(cellsRead?.key ?? null, cellsRead?.fetch ?? idle);

  const found =
    endpoints.status === "ok" ? endpoints.data.items.find((e) => e.id === endpointId) : undefined;
  // The list is paged: an endpoint missing from a truncated page is not proven gone. The
  // series read answers 404 for an endpoint that does not exist.
  const gone =
    configured &&
    ((series.status === "error" && series.httpStatus === 404) ||
      (endpoints.status === "error" && endpoints.httpStatus === 404) ||
      (endpoints.status === "ok" &&
        found === undefined &&
        endpoints.data.total <= endpoints.data.items.length));
  const label = found ? endpointOptionLabel(found) : null;

  const cells = secondary(cellsState);
  const view = useMemo(() => {
    if (series.status !== "ok") return null;
    const { buckets, total, truncated } = series.data;
    const version = buckets.reduce((v, b) => Math.max(v, b.normalizer_version), 0) || null;
    const lookup = cells.data ? buildBaselineLookup(cells.data, "request_count", version) : null;
    const qualifiers = seriesQualifiers(
      buckets.map((b) => ({ start: b.bucket_start, completeness: b.completeness })),
      lookup,
      truncated ? { shown: buckets.length, total } : null,
      cells.data ? INSUFFICIENT : "The expected range could not be read, so none is drawn.",
    );
    return { qualifiers, version };
  }, [series, cells.data]);

  let state: Parameters<typeof WidgetFrame<Body>>[0]["state"];
  if (!configured) state = { kind: "ok", data: NOT_CHOSEN, asOf: null };
  else if (gone) state = { kind: "ok", data: GONE, asOf: null };
  else state = mapResource(series, (s): Body => ({ kind: "series", series: s }));

  return (
    <WidgetFrame
      id={`waf-traffic-${id}`}
      title={label ?? "WAF endpoint traffic"}
      windowText={configured && !gone ? RANGE_TEXT[window] : undefined}
      badge={(view && badgeText(view.qualifiers)) || undefined}
      link={
        sourceId && !gone
          ? { href: `/behavior/waf/sources/${sourceId}`, label: "Endpoints" }
          : { href: "/behavior/waf", label: "WAF" }
      }
      state={state}
      dataTo={{
        at: !configured || gone || series.status !== "ok" ? null : bucketsAsOf(series.data.buckets),
        family: "WAF_COLLECTION",
      }}
      what="the endpoint's request series"
      permission="read:anomalies"
      fillBody
    >
      {({ data }) => {
        if (data.kind === "notice") return <ConfigNotice {...data} />;
        const { buckets } = data.series;
        if (buckets.length === 0) {
          return (
            <p className="tw-state">
              <strong>No request buckets stored.</strong> None was stored for this endpoint in
              the last {RANGE_TEXT[window]}. That is an absence of collection, not zero requests.
            </p>
          );
        }
        if (cells.pending) return <WidgetLoading what="the expected range" />;
        if (!buckets.some((b) => b.completeness === "COMPLETE")) {
          return (
            <p className="tw-state">
              <strong>No bucket was fully collected.</strong> No line is drawn; a bucket that was
              not collected is not zero requests.
            </p>
          );
        }
        const qualifiers = view?.qualifiers ?? [];
        const width = bucketWidth(buckets[0]?.bucket_seconds);
        const complete = buckets.filter((b) => b.completeness === "COMPLETE").length;
        return (
          <>
            {expanded && label ? (
              <p className="tw-state">
                <strong>{label}</strong> requests per {width ?? "bucket"}, last {RANGE_TEXT[window]}.
              </p>
            ) : null}
            <WafRequestChart
              compact
              historyLabel={expanded}
              buckets={buckets}
              expected={null}
              expectedLow={null}
              expectedHigh={null}
              baseline={
                cells.data
                  ? {
                      cells: cells.data as WafBaselineCell[],
                      metric: "request_count",
                      normalizerVersion: view?.version ?? null,
                    }
                  : null
              }
              ariaLabel={`${label ?? "WAF endpoint"} requests per ${width ?? "bucket"} against the expected range, last ${RANGE_TEXT[window]}`}
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
