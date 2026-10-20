"use client";

// chart.property_watch (Phase G §G.13.5). One watch's pinned values and the top values of its
// last bucket, at most five, drawn by the same series chart as the watch page (`compact`).
// The layout holds a watch id, chosen from an option read. Reads: the watch, the switches, the
// stored buckets, the tracked values and one bulk series read. Nothing is written.
//
// Every state is one of the frame's, except the two the frame cannot word well: a refused read
// names the permission and who grants it, and a watch that is not chosen or no longer exists
// says so. The drill-down leads to the watch page in all of them.

import { useMemo, type ReactNode } from "react";

import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { ConfigNotice } from "@/components/catalog/ConfigNotice";
import { PropertyWatchSeriesChart } from "@/components/propertywatch/PropertyWatchSeriesChart";
import { coverageText, eventsUnit, relativeAge } from "@/components/propertywatch/pwFormat";
import { valueText } from "@/components/propertywatch/WatchValuesTable";
import { useApiResource, type ResourceState } from "@/hooks/useApiResource";
import { api, type PwBucket, type PwValueSeries } from "@/lib/api";
import { formatDateTime } from "@/lib/health";
import type { Qualifier } from "@/lib/home/qualifiers";
import { ageSeconds, earlyState, isStale, widgetSelection } from "@/widgets/pwWidgetState";
import type { RangedProps } from "@/widgets/runtime";
import type { WidgetState } from "@/widgets/types";

import "./pwWidget.css";

export interface PropertyWatchWidgetConfig {
  watch_id: string | null;
}

export const PW_WIDGET_PERMISSION = "read:property_watches";

/** Refused read. Names the permission and who grants it. */
export const PW_WIDGET_FORBIDDEN =
  "Your role cannot read property watches (permission read:property_watches). An administrator grants read:property_watches. Nothing was changed.";

interface Drawn {
  lines: PwValueSeries[];
  bucketSeconds: number;
  hours: number;
  windowStart: string;
  windowEnd: string;
  collected: string[];
  qualifiers: Qualifier[];
  lastBucket: string;
  refreshFailed: boolean;
}

const failure = (what: string, httpStatus: number | null): WidgetState<never> => ({
  kind: "error",
  action: `${what} could not be read. ${
    httpStatus === null ? "The backend did not answer. " : `The backend answered with HTTP ${httpStatus}. `
  }Nothing was changed, and the previous answer stays on screen when there is one. Use Refresh in the top bar; if it still fails, check that the backend is reachable.`,
});

function failedRead(
  states: Array<[string, ResourceState<unknown>]>,
): { forbidden: boolean; error: WidgetState<never> | null; pending: boolean } {
  let pending = false;
  for (const [what, s] of states) {
    if (s.status === "forbidden") return { forbidden: true, error: null, pending };
    if (s.status === "error") return { forbidden: false, error: failure(what, s.httpStatus), pending };
    if (s.status !== "ok") pending = true;
  }
  return { forbidden: false, error: null, pending };
}

function qualifiersOf(buckets: PwBucket[], hours: number): Qualifier[] {
  const out: Qualifier[] = [];
  const width = buckets[0]?.bucket_seconds ?? 300;
  const intervals = Math.floor((hours * 3600) / width);
  if (buckets.length < intervals) {
    const have = buckets.length.toLocaleString("en-US");
    const of = intervals.toLocaleString("en-US");
    out.push({
      kind: "PARTIAL",
      have: buckets.length,
      of: intervals,
      unit: "five-minute intervals",
      text: `${have} of ${of} five-minute intervals collected; the rest are gaps, not zero events`,
    });
  }
  const lower = buckets.filter((b) => b.values_measurement === "LOWER_BOUND").length;
  if (lower > 0) {
    out.push({
      kind: "TRUNCATED",
      shown: lower,
      total: buckets.length,
      text: `${lower.toLocaleString("en-US")} of ${buckets.length.toLocaleString("en-US")} collected ${lower === 1 ? "bucket is a lower bound" : "buckets are lower bounds"}; their points read "at least n" and absent values are unknown, not zero`,
    });
  }
  return out;
}

/** The window of every read. The series API takes 1 to 168 h; the widget fixes 24 h, which the
 *  registry does not offer as a dashboard range (its reads are the watch page's, not the fleet's). */
const HOURS = 24;
const WINDOW_TEXT = "24 h";

export function PropertyWatchWidget({
  id,
  config,
}: { id: string; config: PropertyWatchWidgetConfig } & RangedProps) {
  const hours = HOURS;
  const watchId =
    typeof config.watch_id === "string" && config.watch_id !== "" ? config.watch_id : null;

  const watchS = useApiResource(watchId ? `/property-watches/${watchId}` : null, () =>
    api.propertyWatch(watchId as string),
  );
  const catalogS = useApiResource(watchId ? "/property-watches/catalog" : null, () =>
    api.propertyWatchCatalog(),
  );
  const watch = watchS.status === "ok" ? watchS.data : null;
  const catalog = catalogS.status === "ok" ? catalogS.data : null;
  // The catalog only adds the collection switch: a refused or failed one leaves the watch's own
  // status to speak, and a slow one is waited for so the widget does not flash a chart first.
  const early =
    watch !== null && catalogS.status !== "idle" && catalogS.status !== "loading"
      ? earlyState(watch, catalog)
      : null;
  const settled = watch !== null && catalogS.status !== "idle" && catalogS.status !== "loading";
  const reading = settled && early === null && watchId !== null;

  const bucketsS = useApiResource(reading ? `/property-watches/${watchId}/buckets?hours=${hours}` : null, () =>
    api.propertyWatchBuckets(watchId as string, hours),
  );
  const valuesS = useApiResource(
    reading ? `/property-watches/${watchId}/values?hours=${hours}&sort=events&limit=50` : null,
    () => api.propertyWatchValues(watchId as string, { hours, sort: "events", limit: 50 }),
  );
  const items = valuesS.status === "ok" ? valuesS.data.items : null;
  const selection = useMemo(() => (items ? widgetSelection(items) : []), [items]);
  const seriesS = useApiResource(
    reading && selection.length > 0
      ? `/property-watches/${watchId}/series?${selection.map((v) => `value_ids=${v}`).join("&")}&hours=${hours}`
      : null,
    (signal) => api.propertyWatchSeries(watchId as string, selection, hours, signal),
  );

  // Stable inputs for the chart: a new array on every render would rebuild it every render.
  const seriesData = seriesS.status === "ok" ? seriesS.data : null;
  const seriesAt = seriesS.status === "ok" ? seriesS.receivedAt : null;
  const plotted = useMemo(() => {
    if (!seriesData || !items) return null;
    const labels = new Map(items.map((v) => [v.id, valueText(v)]));
    return seriesData.series.map((s) => ({
      ...s,
      value_key: labels.get(s.value_id) ?? s.value_key,
      points: s.points.filter((point) => point.origin === "LIVE"),
    }));
  }, [seriesData, items]);
  const bucketData = bucketsS.status === "ok" ? bucketsS.data : null;
  const liveBuckets = useMemo(
    () => bucketData?.filter((bucket) => bucket.origin === "LIVE") ?? null,
    [bucketData],
  );
  const collected = useMemo(() => liveBuckets?.map((b) => b.bucket_start) ?? null, [liveBuckets]);
  const win = useMemo(() => {
    if (!seriesAt) return null;
    const end = new Date(seriesAt);
    end.setUTCSeconds(0, 0);
    return {
      end: end.toISOString(),
      start: new Date(end.getTime() - hours * 3_600_000).toISOString(),
    };
  }, [seriesAt, hours]);

  const title = watch?.name ?? "Property watch";
  const link = {
    href: watchId ? `/behavior/watches/${watchId}` : "/behavior/watches",
    label: "Watch",
  };
  const frame = (state: WidgetState<Drawn>, extra: { badge?: string; windowed?: boolean } = {}) => (
    <WidgetFrame
      id={`property-watch-${id}`}
      title={title}
      what="property watches"
      permission={PW_WIDGET_PERMISSION}
      windowText={extra.windowed ? WINDOW_TEXT : undefined}
      badge={extra.badge}
      link={link}
      fillBody
      state={state}
    >
      {(ok) => <PwBody drawn={ok.data} />}
    </WidgetFrame>
  );
  // A body of the widget's own, in the frame's `ok` state: the frame keeps the title and the link.
  const note = (body: ReactNode) => (
    <WidgetFrame
      id={`property-watch-${id}`}
      title={title}
      what="property watches"
      permission={PW_WIDGET_PERMISSION}
      link={link}
      state={{ kind: "ok", data: null, asOf: null }}
    >
      {() => body}
    </WidgetFrame>
  );

  if (watchId === null) {
    return note(
      <ConfigNotice
        lead="Choose a property watch."
        text="No watch is set for this widget, so there is nothing to chart."
        hint="Open Customize, then Configure, to choose one."
      />,
    );
  }
  if (watchS.status === "idle" || watchS.status === "loading") return frame({ kind: "loading" });
  if (watchS.status === "forbidden") {
    return note(
      <p className="tw-state" role="alert">
        {PW_WIDGET_FORBIDDEN}
      </p>,
    );
  }
  if (watchS.status === "error") {
    if (watchS.httpStatus === 404) {
      return note(
        <ConfigNotice
          lead="This property watch was deleted."
          text="The watch set for this widget no longer exists."
          hint="Open Customize, then Configure, to choose another."
        />,
      );
    }
    return frame(failure("The property watch", watchS.httpStatus));
  }
  if (!settled) return frame({ kind: "loading" });
  if (early !== null) return frame(early);

  const read = failedRead([
    ["The tracked values", valuesS],
    ["The stored buckets", bucketsS],
    ...(selection.length > 0 ? ([["The series", seriesS]] as Array<[string, ResourceState<unknown>]>) : []),
  ]);
  if (read.forbidden) {
    return note(
      <p className="tw-state" role="alert">
        {PW_WIDGET_FORBIDDEN}
      </p>,
    );
  }
  if (read.error) return frame(read.error, { windowed: true });
  if (valuesS.status !== "ok" || bucketsS.status !== "ok") return frame({ kind: "loading" });
  if (liveBuckets !== null && liveBuckets.length === 0) {
    return frame({
      kind: "empty",
      reason: `No collected bucket exists after the history hand-off. ${watch.history.reason ?? "The watch has not collected its first current bucket yet."} Open the watch to inspect the history read.`,
    });
  }
  if (items === null || items.length === 0) {
    return frame(
      {
        kind: "empty",
        reason: `No value is tracked for this watch in the last ${WINDOW_TEXT}. A value appears once a collected bucket admits it, and nothing is judged until then. Created ${formatDateTime(watch?.created_at)}.`,
      },
      { windowed: true },
    );
  }
  if (selection.length === 0) {
    return frame(
      {
        kind: "empty",
        reason:
          "No value is pinned, and no tracked value has a counted event in the last bucket, so there is nothing to draw. This is not a reading of zero events.",
      },
      { windowed: true },
    );
  }
  if (seriesS.status !== "ok" || plotted === null || win === null || collected === null) {
    return frame({ kind: "loading" });
  }

  const lastBucket = watch?.coverage?.bucket_start ?? null;
  if (watch === null || lastBucket === null) return frame({ kind: "loading" });
  const bucketSeconds = liveBuckets?.[liveBuckets.length - 1]?.bucket_seconds ?? 300;
  const drawn: Drawn = {
    lines: plotted,
    bucketSeconds,
    hours,
    windowStart: win.start,
    windowEnd: win.end,
    collected,
    qualifiers: qualifiersOf(liveBuckets ?? [], hours),
    lastBucket,
    refreshFailed: [watchS, valuesS, bucketsS, seriesS].some(
      (s) => s.status === "ok" && s.refreshFailed !== null,
    ),
  };
  const age = ageSeconds(lastBucket, new Date());
  const badge = `Coverage ${coverageText(watch.coverage)}`;
  const state: WidgetState<Drawn> =
    isStale(watch, age) && age !== null
      ? { kind: "stale", data: drawn, asOf: lastBucket, ageSeconds: age }
      : { kind: "ok", data: drawn, asOf: lastBucket };
  return frame(state, { badge, windowed: true });
}

function PwBody({ drawn }: { drawn: Drawn }) {
  const unit = eventsUnit(drawn.bucketSeconds);
  return (
    <div className="pww">
      <div className="pww-chart">
        <PropertyWatchSeriesChart
          compact
          series={drawn.lines}
          bucketSeconds={drawn.bucketSeconds}
          windowStart={drawn.windowStart}
          windowEnd={drawn.windowEnd}
          collected={drawn.collected}
          emptyText="No value is plotted."
          ariaLabel={`${unit} for the pinned and the top values of the last bucket, in UTC`}
        />
      </div>
      <p className="tw-note">
        Last bucket <time dateTime={drawn.lastBucket}>{formatDateTime(drawn.lastBucket)}</time>{" "}
        <span className="pww-age">({relativeAge(drawn.lastBucket, new Date()) || "age unknown"})</span>
        . Y axis: {unit}.
      </p>
      <WidgetQualifiers qualifiers={drawn.qualifiers} />
      {drawn.refreshFailed ? (
        <p className="tw-note warn" role="status">
          The latest refresh failed. The previous answer stays on screen; use Refresh in the top bar to
          try again.
        </p>
      ) : null}
    </div>
  );
}
