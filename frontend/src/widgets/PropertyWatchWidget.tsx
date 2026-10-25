"use client";

// chart.property_watch (Phase G §G.13.5). One watch's pinned values and the top values of its
// last bucket, at most five, drawn by the same series chart as the watch page (`compact`).
// The layout holds a watch id, chosen from an option read. Reads: the watch, the switches, the
// stored buckets, the tracked values and one bulk series read. Nothing is written.
//
// Every state is one of the frame's, except the two the frame cannot word well: a refused read
// names the permission and who grants it, and a watch that is not chosen or no longer exists
// says so. The drill-down leads to the watch page in all of them.

import { useLocale, useTranslations } from "next-intl";
import { useMemo, type ReactNode } from "react";

import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { ConfigNotice } from "@/components/catalog/ConfigNotice";
import { PropertyWatchSeriesChart } from "@/components/propertywatch/PropertyWatchSeriesChart";
import type { PwI18n } from "@/components/propertywatch/pwText";
import { valueText } from "@/components/propertywatch/WatchValuesTable";
import { useApiResource, type ResourceState } from "@/hooks/useApiResource";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import { englishText } from "@/widgets/englishText";
import type { Locale } from "@/i18n/locale";
import { useMeasures } from "@/i18n/measures";
import { api, type PropertyWatch, type PwBucket, type PwValueSeries } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { worded, type Qualifier } from "@/lib/home/qualifiers";
import { ageSeconds, earlyState, isStale, widgetSelection } from "@/widgets/pwWidgetState";
import type { RangedProps } from "@/widgets/runtime";
import type { WidgetState } from "@/widgets/types";
import { UtcTime } from "@/components/ui/UtcTime";


import "./pwWidget.css";

/** `{at}` in a string reason is the instant `at`, drawn as `<UtcTime>` rather than plain text. */
function withInstant<T>(state: WidgetState<T>): WidgetState<T> {
  if (state.kind !== "empty" && state.kind !== "no_data" && state.kind !== "not_collected") {
    return state;
  }
  if (typeof state.reason !== "string" || !state.at || !state.reason.includes("{at}")) return state;
  const [before, after = ""] = state.reason.split("{at}");
  return {
    ...state,
    reason: (
      <>
        {before}
        <UtcTime value={state.at} />
        {after}
      </>
    ),
  };
}

export interface PropertyWatchWidgetConfig {
  watch_id: string | null;
}

export const PW_WIDGET_PERMISSION = "read:property_watches";

/** Refused read, in English: names the permission and who grants it. */
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

/** `what` is the translated, nominative name of what could not be read ("The tracked values"). */
const failure = (
  t: TextTranslator,
  what: string,
  httpStatus: number | null,
): WidgetState<never> => ({
  kind: "error",
  action: t("widgets.propertyWatch.failed", {
    what,
    answer:
      httpStatus === null
        ? t("widgets.propertyWatch.noAnswer")
        : t("widgets.propertyWatch.answeredWith", { status: httpStatus }),
  }),
});

function failedRead(
  t: TextTranslator,
  states: Array<[string, ResourceState<unknown>]>,
): { forbidden: boolean; error: WidgetState<never> | null; pending: boolean } {
  let pending = false;
  for (const [what, s] of states) {
    if (s.status === "forbidden") return { forbidden: true, error: null, pending };
    if (s.status === "error") {
      return { forbidden: false, error: failure(t, what, s.httpStatus), pending };
    }
    if (s.status !== "ok") pending = true;
  }
  return { forbidden: false, error: null, pending };
}

/** The two caveats of the stored buckets, worded here (the frame shows the text as given). */
export function qualifiersOf(
  buckets: PwBucket[],
  hours: number,
  t: TextTranslator = englishText,
): Qualifier[] {
  const out: Qualifier[] = [];
  const width = buckets[0]?.bucket_seconds ?? 300;
  const intervals = Math.floor((hours * 3600) / width);
  if (buckets.length < intervals) {
    out.push(
      worded({
        kind: "PARTIAL",
        have: buckets.length,
        of: intervals,
        unit: t("widgets.propertyWatch.fiveMinuteIntervals"),
        text: t("widgets.propertyWatch.partialText", { have: buckets.length, of: intervals }),
      }),
    );
  }
  const lower = buckets.filter((b) => b.values_measurement === "LOWER_BOUND").length;
  if (lower > 0) {
    out.push(
      worded({
        kind: "TRUNCATED",
        shown: lower,
        total: buckets.length,
        text: t("widgets.propertyWatch.lowerBoundsText", { lower, total: buckets.length }),
      }),
    );
  }
  return out;
}

/** `96 %`, or `at least 88 %` when the grouped search was cut; the em dash when unmeasured. */
function coverageText(
  coverage: PropertyWatch["coverage"],
  t: TextTranslator,
  locale: Locale,
): string {
  if (!coverage || coverage.ratio === null) return "—";
  const text = formatPercent(coverage.ratio, null, 0, locale);
  return coverage.measurement === "LOWER_BOUND"
    ? t("widgets.propertyWatch.atLeast", { value: text })
    : text;
}

/** The unit of the Y axis: events per bucket of the series. */
function eventsUnit(bucketSeconds: number | null | undefined, t: TextTranslator): string {
  if (!bucketSeconds || bucketSeconds <= 0) return t("widgets.propertyWatch.perBucket");
  const minutes = bucketSeconds / 60;
  return Number.isInteger(minutes)
    ? t("widgets.propertyWatch.perMinutes", { minutes })
    : t("widgets.propertyWatch.perSeconds", { seconds: bucketSeconds });
}

/** The window of every read. The series API takes 1 to 168 h; the widget fixes 24 h, which the
 *  registry does not offer as a dashboard range (its reads are the watch page's, not the fleet's). */
const HOURS = 24;

export function PropertyWatchWidget({
  id,
  config,
}: { id: string; config: PropertyWatchWidgetConfig } & RangedProps) {
  const t = useTranslations("widgets.propertyWatch");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
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
      ? earlyState(watch, catalog, say, locale)
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

  const title = watch?.name ?? t("defaultTitle");
  const link = {
    href: watchId ? `/behavior/watches/${watchId}` : "/behavior/watches",
    label: t("link"),
  };
  const frame = (state: WidgetState<Drawn>, extra: { badge?: string; windowed?: boolean } = {}) => (
    <WidgetFrame
      id={`property-watch-${id}`}
      title={title}
      what={t("what")}
      permission={PW_WIDGET_PERMISSION}
      windowText={extra.windowed ? say("units.range.24h") : undefined}
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
      what={t("what")}
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
        lead={t("chooseLead")}
        text={t("chooseText")}
        hint={t("chooseHint")}
      />,
    );
  }
  if (watchS.status === "idle" || watchS.status === "loading") return frame({ kind: "loading" });
  if (watchS.status === "forbidden") {
    return note(
      <p className="tw-state" role="alert">
        {t("forbidden")}
      </p>,
    );
  }
  if (watchS.status === "error") {
    if (watchS.httpStatus === 404) {
      return note(
        <ConfigNotice
          lead={t("deletedLead")}
          text={t("deletedText")}
          hint={t("deletedHint")}
        />,
      );
    }
    return frame(failure(say, t("theWatch"), watchS.httpStatus));
  }
  if (!settled) return frame({ kind: "loading" });
  if (early !== null) return frame(withInstant(early));

  const read = failedRead(say, [
    [t("theValues"), valuesS],
    [t("theBuckets"), bucketsS],
    ...(selection.length > 0
      ? ([[t("theSeries"), seriesS]] as Array<[string, ResourceState<unknown>]>)
      : []),
  ]);
  if (read.forbidden) {
    return note(
      <p className="tw-state" role="alert">
        {t("forbidden")}
      </p>,
    );
  }
  if (read.error) return frame(read.error, { windowed: true });
  if (valuesS.status !== "ok" || bucketsS.status !== "ok") return frame({ kind: "loading" });
  if (liveBuckets !== null && liveBuckets.length === 0) {
    return frame({
      kind: "empty",
      reason: t("noCollected", {
        reason: watch.history.reason ?? t("noFirstBucket"),
      }),
    });
  }
  if (items === null || items.length === 0) {
    return frame(
      {
        kind: "empty",
        reason: t.rich("noValues", {
          window: say("units.range.24h"),
          created: () => <UtcTime value={watch?.created_at} />,
        }),
      },
      { windowed: true },
    );
  }
  if (selection.length === 0) {
    return frame(
      {
        kind: "empty",
        reason: t("nothingToDraw"),
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
    qualifiers: qualifiersOf(liveBuckets ?? [], hours, say),
    lastBucket,
    refreshFailed: [watchS, valuesS, bucketsS, seriesS].some(
      (s) => s.status === "ok" && s.refreshFailed !== null,
    ),
  };
  const age = ageSeconds(lastBucket, new Date());
  const badge = t("coverageBadge", { value: coverageText(watch.coverage, say, locale) });
  const state: WidgetState<Drawn> =
    isStale(watch, age) && age !== null
      ? { kind: "stale", data: drawn, asOf: lastBucket, ageSeconds: age }
      : { kind: "ok", data: drawn, asOf: lastBucket };
  return frame(state, { badge, windowed: true });
}

function PwBody({ drawn }: { drawn: Drawn }) {
  const t = useTranslations("widgets.propertyWatch");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const pwText = useMemo<PwI18n>(() => ({ t: say, locale: measures.locale }), [say, measures.locale]);
  const unit = eventsUnit(drawn.bucketSeconds, say);
  const age = measures.age(ageSeconds(drawn.lastBucket, new Date()));
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
          emptyText={t("nonePlotted")}
          ariaLabel={t("chartLabel", { unit })}
          i18n={pwText}
        />
      </div>
      <p className="tw-note">
        {t.rich("lastBucket", {
          time: () => <UtcTime value={drawn.lastBucket} />,
          age: () => <span className="pww-age">({age})</span>,
          unit,
        })}
      </p>
      <WidgetQualifiers qualifiers={drawn.qualifiers} />
      {drawn.refreshFailed ? (
        <p className="tw-note warn" role="status">
          {t("refreshFailed")}
        </p>
      ) : null}
    </div>
  );
}
