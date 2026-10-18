"use client";

// The body of a watch page below its header: the tabs, the selection of values that feeds the
// chart, and the one bulk series read that follows a selection.
//
// The server renders the first view (the default selection's series, the values, the buckets)
// and this component owns what changes without a navigation: which values are plotted. A
// selection fetches the series of the values it has not yet read, in one request (at most 12
// ids, the API's bound; the selection never exceeds five). Nothing is written.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Pagination } from "@/components/Pagination";
import { TableScroll } from "@/components/ui/TableScroll";
import { Tabs } from "@/components/ui/Tabs";
import {
  api,
  type AnomalySummary,
  type PagedPwValues,
  type PropertyWatch,
  type PropertyWatchCatalog,
  type PwBucket,
  type PwValueSeries,
  type WatchScope,
} from "@/lib/api";

import { PropertyWatchSeriesChart } from "./PropertyWatchSeriesChart";
import { honestyNotes, WatchHonesty } from "./WatchHonesty";
import { WatchDefinition } from "./WatchDefinition";
import { WatchIncidents } from "./WatchIncidents";
import {
  activeIncidentsByValue,
  valueText,
  WatchValuesTable,
  type ValueSort,
} from "./WatchValuesTable";
import { PW_RANGES, VALUES_PAGE_SIZE } from "./pwRange";
import { PW_MAX_SERIES } from "./pwSeries";
import { relativeAge } from "./pwFormat";
import { formatDateTime } from "@/lib/health";

import "./propertywatch.css";

export interface WatchWorkspaceProps {
  watch: PropertyWatch;
  catalog: PropertyWatchCatalog | null;
  hours: number;
  rangeKey: string;
  sort: ValueSort;
  offset: number;
  buckets: PwBucket[] | null;
  values: PagedPwValues | null;
  incidents: AnomalySummary[] | null;
  incidentsTotal: number | null;
  /** The resolved membership, or null when it could not be read. */
  scope: WatchScope | null;
  initialSelected: string[];
  initialSeries: PwValueSeries[];
  /** The window the axis covers (ISO), taken once by the server so the page renders one view. */
  windowStart: string;
  windowEnd: string;
}

const byId = (list: PwValueSeries[]) => Object.fromEntries(list.map((s) => [s.value_id, s]));

export function WatchWorkspace(props: WatchWorkspaceProps) {
  const { watch, catalog, hours, buckets, values, incidents } = props;
  const [selected, setSelected] = useState<string[]>(props.initialSelected);
  const [cache, setCache] = useState<Record<string, PwValueSeries>>(() => byId(props.initialSeries));
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  // Ids with a read in flight or answered; `answered` is the part that must not be asked again.
  const asked = useRef<Set<string>>(new Set(props.initialSeries.map((s) => s.value_id)));
  const answered = useRef<Set<string>>(new Set(props.initialSeries.map((s) => s.value_id)));

  useEffect(() => {
    const inFlight = asked.current;
    const done = answered.current;
    const missing = selected.filter((id) => !inFlight.has(id));
    if (missing.length === 0) return;
    const controller = new AbortController();
    for (const id of missing) inFlight.add(id);
    api
      .propertyWatchSeries(watch.id, missing, hours, controller.signal)
      .then((read) => {
        for (const s of read.series) done.add(s.value_id);
        setCache((prev) => ({ ...prev, ...byId(read.series) }));
        setFailed(false);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        for (const id of missing) inFlight.delete(id);
        setFailed(true);
      });
    return () => {
      controller.abort();
      // A read that was superseded did not answer: a later render must be able to ask again.
      for (const id of missing) if (!done.has(id)) inFlight.delete(id);
    };
  }, [selected, watch.id, hours, retry]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) =>
      prev.includes(id)
        ? prev.filter((v) => v !== id)
        : prev.length >= PW_MAX_SERIES
          ? prev
          : [...prev, id],
    );
  }, []);

  const items = useMemo(() => values?.items ?? [], [values]);
  const labels = useMemo(() => {
    const map = new Map<string, string>();
    for (const v of items) map.set(v.id, valueText(v));
    return map;
  }, [items]);
  const latest = buckets && buckets.length > 0 ? buckets[buckets.length - 1] : null;
  const bucketSeconds = latest?.bucket_seconds ?? 300;

  const plotted = selected
    .map((id) => cache[id])
    .filter((s): s is PwValueSeries => s !== undefined)
    .map((s) => ({ ...s, value_key: labels.get(s.value_id) ?? s.value_key }));
  const waiting = selected.filter((id) => !(id in cache));
  const incidentByValue = useMemo(() => activeIncidentsByValue(incidents ?? []), [incidents]);

  // Spans of incidents of the plotted values only: every incident of the watch would be noise.
  const keys = new Set(
    selected.map((id) => items.find((v) => v.id === id)?.value_key).filter((k): k is string => !!k),
  );
  const spans = (incidents ?? [])
    .filter((a) => a.pw_value && keys.has(a.pw_value.value_key) && (a.anomaly_start ?? a.opened_at))
    .map((a) => ({
      start: (a.anomaly_start ?? a.opened_at) as string,
      end: a.anomaly_end ?? a.resolved_at,
    }));

  const notes = honestyNotes({ watch, catalog, buckets, hours });
  const valuesPath = `/behavior/watches/${watch.id}`;
  const baseParams = { range: props.rangeKey === "24h" ? undefined : props.rangeKey };
  const sortHref = (sort: ValueSort) => {
    const q = new URLSearchParams();
    if (baseParams.range) q.set("range", baseParams.range);
    if (sort !== "events") q.set("sort", sort);
    const s = q.toString();
    return s ? `${valuesPath}?${s}` : valuesPath;
  };

  const emptyChart =
    buckets !== null && buckets.length === 0
      ? "No bucket has been collected for this watch in this window. This is an absence of collection, not an observation of zero events."
      : items.length === 0
        ? "No value is tracked, so there is nothing to plot."
        : "Select up to five values in the table to plot them. The default is the pinned values, then the most frequent in the latest bucket.";

  const valuesTab = () => (
    <div className="stack-6">
      <div className="pw-range">
        <nav className="range-selector" aria-label="Window">
          <span className="muted">Window</span>
          {PW_RANGES.map((r) => (
            <a
              key={r.key}
              href={
                r.key === "24h"
                  ? `${valuesPath}${props.sort === "events" ? "" : `?sort=${props.sort}`}`
                  : `${valuesPath}?range=${r.key}${props.sort === "events" ? "" : `&sort=${props.sort}`}`
              }
              className={`range-option${r.hours === hours ? " range-option-active" : ""}`}
              aria-current={r.hours === hours ? "page" : undefined}
            >
              {r.label}
            </a>
          ))}
        </nav>
        <p className="subtitle">
          Last {hours === 168 ? "7 days" : `${hours} h`} to{" "}
          <time dateTime={props.windowEnd}>{formatDateTime(props.windowEnd)}</time>.
          {latest && (
            <>
              {" "}Latest bucket{" "}
              <time dateTime={latest.bucket_start}>{formatDateTime(latest.bucket_start)}</time>{" "}
              <span className="muted">({relativeAge(latest.bucket_start, new Date(props.windowEnd))})</span>.
            </>
          )}
        </p>
      </div>

      {failed && (
        <div className="notice" role="alert">
          <p>
            <strong>The series of the selected values could not be loaded.</strong>
          </p>
          <p>
            The values already drawn stay on screen. The backend may be unreachable; try again, or
            change the selection. Nothing was changed.{" "}
            <button type="button" className="action secondary" onClick={() => setRetry((n) => n + 1)}>
              Try again
            </button>
          </p>
        </div>
      )}
      {waiting.length > 0 && !failed && (
        <p className="notice" role="status" aria-live="polite">
          Loading the series of {waiting.length} selected {waiting.length === 1 ? "value" : "values"}…
        </p>
      )}

      <WatchHonesty notes={notes} />

      <section aria-label="Series chart">
        <PropertyWatchSeriesChart
          series={plotted}
          bucketSeconds={bucketSeconds}
          windowStart={props.windowStart}
          windowEnd={props.windowEnd}
          collected={buckets ? buckets.map((b) => b.bucket_start) : undefined}
          coverage={buckets ? { buckets, floor: catalog?.min_coverage ?? null } : null}
          incidents={spans}
          emptyText={emptyChart}
        />
        {latest && latest.unparsed_events !== null && latest.total_events !== null && (
          <p className="subtitle pw-remainder">
            In the latest bucket, {latest.unparsed_events.toLocaleString("en-US")} of{" "}
            {latest.total_events.toLocaleString("en-US")} events in scope carried no{" "}
            {watch.properties.length === 1 ? "" : "complete set of "}
            <span className="pw-prop-name">{watch.properties.map((p) => p.name).join(" and ")}</span>
            {latest.values_measurement === "LOWER_BOUND" ? " (at most, the bucket is a lower bound)" : ""}.
            They are counted here, not as a value, and never as a zero.
          </p>
        )}
      </section>

      <section aria-labelledby="pw-values-title">
        <h2 id="pw-values-title" className="section-title">
          Values
        </h2>
        {values === null ? (
          <div className="notice" role="alert">
            <p>
              <strong>The tracked values could not be loaded.</strong>
            </p>
            <p>The chart and the table need them. Reload the page to try again. Nothing was changed.</p>
          </div>
        ) : items.length === 0 ? (
          <div className="notice" role="status">
            <p>
              <strong>No value is tracked for this watch in this window.</strong>
            </p>
            <p>
              A value appears once a collected bucket admits it. Until then nothing is judged, and
              this is not a statement that the property takes no values.
            </p>
          </div>
        ) : (
          <>
            <p className="subtitle">
              {selected.length} of at most {PW_MAX_SERIES} values plotted. Counts are events per
              5-minute bucket.
            </p>
            <TableScroll label="Tracked values">
              <WatchValuesTable
                values={items}
                latest={latest}
                selected={new Set(selected)}
                maxSelected={PW_MAX_SERIES}
                onToggle={toggle}
                incidents={incidentByValue}
                sort={props.sort}
                sortHref={sortHref}
              />
            </TableScroll>
            <Pagination
              total={values.total}
              limit={VALUES_PAGE_SIZE}
              offset={props.offset}
              basePath={valuesPath}
              params={{ ...baseParams, sort: props.sort === "events" ? undefined : props.sort }}
            />
          </>
        )}
      </section>

    </div>
  );

  return (
    <Tabs
      label="Watch detail"
      tabs={[
        { id: "values", label: "Values", content: valuesTab },
        {
          id: "incidents",
          label: incidents ? `Incidents (${props.incidentsTotal ?? incidents.length})` : "Incidents",
          content: () => (
            <WatchIncidents incidents={incidents} total={props.incidentsTotal} watchId={watch.id} />
          ),
        },
        {
          id: "definition",
          label: "Definition",
          content: () => <WatchDefinition watch={watch} catalog={catalog} scope={props.scope} />,
        },
      ]}
    />
  );
}

