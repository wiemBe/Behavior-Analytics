"use client";

// Per catch-all source: the volume and the honesty of each bucket over time.
//
// A bucket with no row was not measured. The chart shades such a gap only where
// it can be known (inside the range, before the latest measured bucket) and the
// text beside it says how far collection got. The selected bucket shows its top
// senders and what admission did, in words.

import { useTranslations } from "next-intl";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { ApiError, catchallApi, type CatchallSourceList } from "@/lib/api";
import { plainCount, useFleetText } from "@/lib/fleetText";
import { unitGap } from "@/i18n/measures";

import { HistoryKey } from "@/components/behavior/HistoryKey";
import { originMarker } from "@/components/behavior/historyOrigin";
import { UtcTime } from "@/components/ui/UtcTime";


import { BucketChart } from "./BucketChart";
import { SenderId } from "./SenderId";
import {
  RANGES,
  admissionSentences,
  buildSlots,
  bucketUnit,
  historyCount,
  collectedUpTo,
  formatCount,
  latestMeasuredIndex,
  measurementText,
  rangeLabel,
  stepMeasured,
  type RangeOption,
  type SlotSeries,
} from "./catchall";

type Load =
  | { kind: "loading" }
  | { kind: "error"; text: string; forbidden: boolean }
  | { kind: "ready"; series: SlotSeries };

/** The selected bucket's span. A missing width shows only the start. */
function BucketWindow({ startIso, seconds }: { startIso: string; seconds: number | null }) {
  const t = useTranslations("catchall.volume");
  if (seconds === null) return <UtcTime value={startIso} />;
  const end = new Date(Date.parse(startIso) + seconds * 1000).toISOString();
  return (
    <>
      {t.rich("window", {
        start: () => <UtcTime value={startIso} />,
        end: () => <UtcTime value={end} />,
      })}
    </>
  );
}

export function SourceVolume({ sources, now }: { sources: CatchallSourceList; now: string }) {
  const t = useTranslations("catchall.volume");
  const q = useTranslations("catchall.queue");
  const common = useTranslations("common");
  const text = useFleetText();
  const { locale } = text;
  const textRef = useRef(text);
  textRef.current = text;
  const ids = useId();
  // Open on the source that was measured most recently, not on an arbitrary one.
  const initialSource = useMemo(() => {
    const measured = sources.items
      .filter((s) => s.latest_bucket_start !== null)
      .sort((a, b) => (b.latest_bucket_start ?? "").localeCompare(a.latest_bucket_start ?? ""));
    return (measured[0] ?? sources.items[0])?.id ?? "";
  }, [sources]);
  const [sourceId, setSourceId] = useState(initialSource);
  const [range, setRange] = useState<RangeOption>(RANGES[1]);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [selected, setSelected] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  const source = sources.items.find((s) => s.id === sourceId);
  const untilMs = Date.parse(now);
  const sinceMs = untilMs - range.ms;

  useEffect(() => {
    if (!sourceId) return;
    let cancelled = false;
    setLoad({ kind: "loading" });
    catchallApi
      .buckets(sourceId, new Date(sinceMs).toISOString(), new Date(untilMs).toISOString())
      .then((list) => {
        if (cancelled) return;
        const series = buildSlots(list.items, sinceMs, untilMs);
        setLoad({ kind: "ready", series });
        setSelected(latestMeasuredIndex(series.slots));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoad({
          kind: "error",
          forbidden: err instanceof ApiError && err.status === 403,
          text:
            err instanceof ApiError && err.status === 403
              ? textRef.current.say("fleet.errors.forbidden")
              : textRef.current.say("catchall.volume.unreachable"),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [sourceId, sinceMs, untilMs, tick]);

  if (sources.items.length === 0) return null;

  const series = load.kind === "ready" ? load.series : null;
  const slot = series && selected != null ? series.slots[selected] : null;
  const upTo = series ? collectedUpTo(series) : null;
  const unit = series?.bucketSeconds ? bucketUnit(series.bucketSeconds, text) : null;

  const summary = series ? (
    series.bucketSeconds === null ? (
      t("noBucket")
    ) : (
      <>
        {t.rich("summary", {
          unit: unit ?? "",
          gaps: plainCount(series.gapCount, locale),
          lower: plainCount(series.lowerBoundCount, locale),
          start: () => <UtcTime value={new Date(sinceMs).toISOString()} />,
          end: () => <UtcTime value={new Date(untilMs).toISOString()} />,
        })}
      </>
    )
  ) : (
    ""
  );

  return (
    <section aria-labelledby={`${ids}-h`} className="catchall-volume">
      <h2 id={`${ids}-h`} className="section-title">
        {t("heading")}
      </h2>

      <div className="filters" role="group" aria-label={t("chooseGroup")}>
        <label className="sr-only" htmlFor={`${ids}-src`}>
          {t("source")}
        </label>
        <select id={`${ids}-src`} value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
          {sources.items.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <div className="range-selector" role="group" aria-label={t("rangeGroup")}>
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              className="action secondary"
              aria-pressed={r.key === range.key}
              onClick={() => setRange(r)}
            >
              {rangeLabel(r, text)}
            </button>
          ))}
        </div>
      </div>

      {load.kind === "loading" && (
        <p className="subtitle" role="status">
          {t("loading")}
        </p>
      )}

      {load.kind === "error" && (
        <div className="notice" role="alert">
          <p>
            <strong>
              {load.forbidden ? t("errorForbidden") : t("errorFailed")}
            </strong>
          </p>
          <p>{load.text}</p>
          {!load.forbidden && (
            <p>
              <button type="button" className="action secondary" onClick={() => setTick((n) => n + 1)}>
                {common("tryAgain")}
              </button>
            </p>
          )}
        </div>
      )}

      {series && series.bucketSeconds === null && (
        <div className="notice">
          <p>
            <strong>{t("noBucket")}</strong>
          </p>
          <p>
            {source?.latest_bucket_start ? (
              <>
                {t.rich("latestBucket", {
                  name: source.name,
                  time: () => <UtcTime value={source.latest_bucket_start as string} />,
                })}
              </>
            ) : (
              t("neverMeasured", { name: source?.name ?? t("thisSource") })
            )}
          </p>
        </div>
      )}

      {series && series.bucketSeconds !== null && (
        <>
          <p className="subtitle collected-up-to">
            {upTo !== null
              ? t.rich("collectedUpTo", {
                  time: () => <UtcTime value={new Date(upTo).toISOString()} />,
                })
              : t("noMeasurement")}{" "}
            {series.gapCount > 0
              ? t("gaps", { count: series.gapCount })
              : t("allMeasured")}
          </p>
          <BucketChart
            slots={series.slots}
            bucketSeconds={series.bucketSeconds}
            selected={selected}
            onSelect={setSelected}
            ariaLabel={t("chartLabel", { unit: unit ?? "", name: source?.name ?? t("theSource") })}
            textSummary={summary}
          />
          <ul className="chart-legend" aria-label={t("key")}>
            <li>
              <span className="chart-key chart-key-bar" aria-hidden="true" />
              {t("keyEvents")}
            </li>
            {series.lowerBoundCount > 0 && (
              <li>
                <span className="chart-key chart-key-bar-lower" aria-hidden="true" />
                {t("keyLower")}
              </li>
            )}
            {series.gapCount > 0 && (
              <li>
                <span className="chart-key chart-key-gap" aria-hidden="true" />
                {t("keyGap")}
              </li>
            )}
            {historyCount(series.slots) > 0 && <HistoryKey />}
          </ul>

          <div className="bucket-nav" role="group" aria-label={t("chooseBucket")}>
            <button
              type="button"
              className="action secondary"
              disabled={selected == null || stepMeasured(series.slots, selected, -1) === null}
              onClick={() =>
                selected != null && setSelected(stepMeasured(series.slots, selected, -1))
              }
            >
              {t("earlier")}
            </button>
            <button
              type="button"
              className="action secondary"
              disabled={selected == null || stepMeasured(series.slots, selected, 1) === null}
              onClick={() =>
                selected != null && setSelected(stepMeasured(series.slots, selected, 1))
              }
            >
              {t("later")}
            </button>
            <span className="muted">{t("orClick")}</span>
          </div>

          {slot && (
            <div className="panel bucket-detail" aria-label={t("selected")} role="group">
              <h3 className="bucket-detail-title">
                <BucketWindow
                  startIso={
                    slot.bucket
                      ? slot.bucket.bucket_start
                      : new Date(slot.t).toISOString()
                  }
                  seconds={slot.bucket ? slot.bucket.bucket_seconds : series.bucketSeconds}
                />
              </h3>
              {slot.bucket === null ? (
                <p className="subtitle">
                  {slot.state === "gap" ? t("notMeasuredDetail") : t("beyondDetail")}
                </p>
              ) : (
                <div className="bucket-grid">
                  <dl className="bucket-facts">
                    <div>
                      <dt>{t("measurement")}</dt>
                      <dd>
                        <span
                          className={`pill ${slot.state === "lower_bound" ? "warn" : ""}`}
                        >
                          {slot.state === "lower_bound" ? t("pillAtLeast") : t("pillExact")}
                        </span>
                        {measurementText(slot.bucket, text) && (
                          <span className="cell-sub">{measurementText(slot.bucket, text)}</span>
                        )}
                      </dd>
                    </div>
                    {originMarker(slot.bucket.origin) && (
                      <div>
                        <dt>{t("origin")}</dt>
                        <dd>
                          <span className="pill">{originMarker(slot.bucket.origin)}</span>
                        </dd>
                      </div>
                    )}
                    <div>
                      <dt>{t("events")}</dt>
                      <dd className="num">
                        {slot.state === "lower_bound" ? `${text.say("catchall.atLeast")} ` : ""}
                        {formatCount(slot.bucket.events, locale)}
                        <span className="unit">
                          {unitGap(locale)}
                          {t("eventsPer", { unit: unit ?? "" })}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("records")}</dt>
                      <dd className="num">{formatCount(slot.bucket.records, locale)}</dd>
                    </div>
                    <div>
                      <dt>{t("distinct")}</dt>
                      <dd className="num">
                        {slot.bucket.distinct_senders_lower_bound
                          ? `${text.say("catchall.atLeast")} `
                          : ""}
                        {formatCount(slot.bucket.distinct_senders, locale)}
                      </dd>
                    </div>
                  </dl>

                  <div className="bucket-admission">
                    <h4>{t("admissionHeading")}</h4>
                    <ul className="plain-list">
                      {admissionSentences(slot.bucket, text).map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                    <p className="muted">{text.say("catchall.admission.refusedNote")}</p>
                  </div>

                  <div className="bucket-top">
                    <h4>{t("topHeading")}</h4>
                    {slot.bucket.top_senders.length === 0 ? (
                      <p className="muted">{t("noTop")}</p>
                    ) : (
                      <div className="bucket-top-table">
                        <table className="catchall-table">
                          <caption className="sr-only">{t("topCaption")}</caption>
                          <thead>
                            <tr>
                              <th scope="col">{q("thSender")}</th>
                              <th scope="col" className="num">
                                {t("events")}
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {slot.bucket.top_senders.slice(0, 20).map((top) => (
                              <tr key={top.identifier}>
                                <th scope="row" className="cell-sender">
                                  <SenderId value={top.identifier} />
                                </th>
                                <td className="num">{formatCount(top.events, locale)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
