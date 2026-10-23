"use client";

// Per catch-all source: the volume and the honesty of each bucket over time.
//
// A bucket with no row was not measured. The chart shades such a gap only where
// it can be known (inside the range, before the latest measured bucket) and the
// text beside it says how far collection got. The selected bucket shows its top
// senders and what admission did, in words.

import { useEffect, useId, useMemo, useState } from "react";

import { ApiError, catchallApi, type CatchallSourceList } from "@/lib/api";

import { HistoryKey } from "@/components/behavior/HistoryKey";
import { originMarker } from "@/components/behavior/historyOrigin";
import { UtcTime } from "@/components/ui/UtcTime";


import { BucketChart } from "./BucketChart";
import { SenderId } from "./SenderId";
import {
  RANGES,
  REFUSED_NOTE,
  admissionSentences,
  buildSlots,
  bucketUnit,
  historyCount,
  collectedUpTo,
  formatCount,
  latestMeasuredIndex,
  measurementText,
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
  if (seconds === null) return <UtcTime value={startIso} />;
  const end = new Date(Date.parse(startIso) + seconds * 1000).toISOString();
  return (
    <>
      <UtcTime value={startIso} /> to <UtcTime value={end} />
    </>
  );
}

export function SourceVolume({ sources, now }: { sources: CatchallSourceList; now: string }) {
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
              ? "Your role cannot read the fleet (permission read:fleet). Nothing was changed."
              : "The buckets could not be loaded. The backend may be unreachable; check that it is running, then try again.",
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
  const unit = series?.bucketSeconds ? bucketUnit(series.bucketSeconds) : null;

  const summary = series ? (
    series.bucketSeconds === null ? (
      "No bucket was measured in this range."
    ) : (
      <>
        Events per {unit} bucket from <UtcTime value={new Date(sinceMs).toISOString()} /> to{" "}
        <UtcTime value={new Date(untilMs).toISOString()} />. {series.gapCount} buckets were not
        measured and are shown as gaps, not as zero. {series.lowerBoundCount} buckets are lower
        bounds.
      </>
    )
  ) : (
    ""
  );

  return (
    <section aria-labelledby={`${ids}-h`} className="catchall-volume">
      <h2 id={`${ids}-h`} className="section-title">
        Volume by source
      </h2>

      <div className="filters" role="group" aria-label="Choose a source and a range">
        <label className="sr-only" htmlFor={`${ids}-src`}>
          Catch-all source
        </label>
        <select id={`${ids}-src`} value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
          {sources.items.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <div className="range-selector" role="group" aria-label="Range">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              className="action secondary"
              aria-pressed={r.key === range.key}
              onClick={() => setRange(r)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {load.kind === "loading" && (
        <p className="subtitle" role="status">
          Loading buckets…
        </p>
      )}

      {load.kind === "error" && (
        <div className="notice" role="alert">
          <p>
            <strong>
              {load.forbidden ? "The buckets cannot be shown." : "The buckets could not be loaded."}
            </strong>
          </p>
          <p>{load.text}</p>
          {!load.forbidden && (
            <p>
              <button type="button" className="action secondary" onClick={() => setTick((n) => n + 1)}>
                Try again
              </button>
            </p>
          )}
        </div>
      )}

      {series && series.bucketSeconds === null && (
        <div className="notice">
          <p>
            <strong>No bucket was measured in this range.</strong>
          </p>
          <p>
            {source?.latest_bucket_start ? (
              <>
                The latest measured bucket of {source.name} starts{" "}
                <UtcTime value={source.latest_bucket_start} />. Choose a longer range to reach it.
              </>
            ) : (
              `No bucket was ever measured for ${source?.name ?? "this source"}. An empty chart here is an absence of collection, not zero traffic.`
            )}
          </p>
        </div>
      )}

      {series && series.bucketSeconds !== null && (
        <>
          <p className="subtitle collected-up-to">
            {upTo !== null ? (
              <>
                Collected up to <UtcTime value={new Date(upTo).toISOString()} />.{" "}
              </>
            ) : (
              "No bucket in this range holds a measurement. "
            )}
            {series.gapCount > 0
              ? `${formatCount(series.gapCount)} ${series.gapCount === 1 ? "bucket" : "buckets"} before that ${
                  series.gapCount === 1 ? "was" : "were"
                } not measured (shaded). They are unknown, not zero.`
              : "Every bucket before that was measured."}
          </p>
          <BucketChart
            slots={series.slots}
            bucketSeconds={series.bucketSeconds}
            selected={selected}
            onSelect={setSelected}
            ariaLabel={`Events per ${unit} bucket for ${source?.name ?? "the source"}`}
            textSummary={summary}
          />
          <ul className="chart-legend" aria-label="Chart key">
            <li>
              <span className="chart-key chart-key-bar" aria-hidden="true" />
              Events per bucket
            </li>
            {series.lowerBoundCount > 0 && (
              <li>
                <span className="chart-key chart-key-bar-lower" aria-hidden="true" />
                At least: the search was truncated, so more events may exist
              </li>
            )}
            {series.gapCount > 0 && (
              <li>
                <span className="chart-key chart-key-gap" aria-hidden="true" />
                Not measured
              </li>
            )}
            {historyCount(series.slots) > 0 && <HistoryKey />}
          </ul>

          <div className="bucket-nav" role="group" aria-label="Choose a bucket">
            <button
              type="button"
              className="action secondary"
              disabled={selected == null || stepMeasured(series.slots, selected, -1) === null}
              onClick={() =>
                selected != null && setSelected(stepMeasured(series.slots, selected, -1))
              }
            >
              Earlier measured bucket
            </button>
            <button
              type="button"
              className="action secondary"
              disabled={selected == null || stepMeasured(series.slots, selected, 1) === null}
              onClick={() =>
                selected != null && setSelected(stepMeasured(series.slots, selected, 1))
              }
            >
              Later measured bucket
            </button>
            <span className="muted">Or click a bar in the chart.</span>
          </div>

          {slot && (
            <div className="panel bucket-detail" aria-label="Selected bucket" role="group">
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
                  {slot.state === "gap"
                    ? "This bucket was not measured: no row was stored for it. Its volume is unknown, not zero."
                    : "This bucket is after the latest measured one. Collection had not reached it, so nothing is known about it yet."}
                </p>
              ) : (
                <div className="bucket-grid">
                  <dl className="bucket-facts">
                    <div>
                      <dt>Measurement</dt>
                      <dd>
                        <span
                          className={`pill ${slot.state === "lower_bound" ? "warn" : ""}`}
                        >
                          {slot.state === "lower_bound" ? "At least" : "Exact"}
                        </span>
                        {measurementText(slot.bucket) && (
                          <span className="cell-sub">{measurementText(slot.bucket)}</span>
                        )}
                      </dd>
                    </div>
                    {originMarker(slot.bucket.origin) && (
                      <div>
                        <dt>Origin</dt>
                        <dd>
                          <span className="pill">{originMarker(slot.bucket.origin)}</span>
                        </dd>
                      </div>
                    )}
                    <div>
                      <dt>Events</dt>
                      <dd className="num">
                        {slot.state === "lower_bound" ? "at least " : ""}
                        {formatCount(slot.bucket.events)}
                        <span className="unit"> events / {unit}</span>
                      </dd>
                    </div>
                    <div>
                      <dt>Records</dt>
                      <dd className="num">{formatCount(slot.bucket.records)}</dd>
                    </div>
                    <div>
                      <dt>Distinct senders</dt>
                      <dd className="num">
                        {slot.bucket.distinct_senders_lower_bound ? "at least " : ""}
                        {formatCount(slot.bucket.distinct_senders)}
                      </dd>
                    </div>
                  </dl>

                  <div className="bucket-admission">
                    <h4>What admission did</h4>
                    <ul className="plain-list">
                      {admissionSentences(slot.bucket).map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                    <p className="muted">{REFUSED_NOTE}</p>
                  </div>

                  <div className="bucket-top">
                    <h4>Top senders in this bucket</h4>
                    {slot.bucket.top_senders.length === 0 ? (
                      <p className="muted">
                        No tracked sender is listed for this bucket.
                      </p>
                    ) : (
                      <div className="bucket-top-table">
                        <table className="catchall-table">
                          <caption className="sr-only">
                            The busiest tracked senders of the selected bucket, at most 20.
                          </caption>
                          <thead>
                            <tr>
                              <th scope="col">Sender</th>
                              <th scope="col" className="num">
                                Events
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {slot.bucket.top_senders.slice(0, 20).map((t) => (
                              <tr key={t.identifier}>
                                <th scope="row" className="cell-sender">
                                  <SenderId value={t.identifier} />
                                </th>
                                <td className="num">{formatCount(t.events)}</td>
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
