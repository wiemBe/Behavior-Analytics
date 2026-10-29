"use client";

// The body of a watch page below its header: the tabs, the selection of values that feeds the
// chart, and the one bulk series read that follows a selection.
//
// The server renders the first view (the default selection's series, the values, the buckets)
// and this component owns what changes without a navigation: which values are plotted. A
// selection fetches the series of the values it has not yet read, in one request (at most 12
// ids, the API's bound; the selection never exceeds five). Nothing is written.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

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
import { utcTimeText } from "@/components/ui/UtcTime";
import { formatCount } from "@/lib/format";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { PropertyWatchSeriesChart } from "./PropertyWatchSeriesChart";
import { honestyNotes, WatchHonesty } from "./WatchHonesty";
import { WatchDefinition } from "./WatchDefinition";
import type { AqlRead } from "./WatchAql";
import { ThresholdRules, type RuleAccess } from "./ThresholdRules";
import { WatchIncidents } from "./WatchIncidents";
import {
  activeIncidentsByValue,
  valueText,
  WatchValuesTable,
  type ValueSort,
} from "./WatchValuesTable";
import { PW_RANGES, pwRangeLabel, VALUES_PAGE_SIZE } from "./pwRange";
import { PW_MAX_SERIES } from "./pwSeries";
import { relativeAge } from "./pwFormat";
import { ENGLISH_PW, type PwI18n } from "./pwText";

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
  /** The generated query text; null when the read did not happen. */
  aql?: AqlRead | null;
  /** Whether the caller holds `custom_rule:write`. */
  ruleAccess?: RuleAccess;
  initialSelected: string[];
  initialSeries: PwValueSeries[];
  /** The window the axis covers (ISO), taken once by the server so the page renders one view. */
  windowStart: string;
  windowEnd: string;
  i18n?: PwI18n;
}

const byId = (list: PwValueSeries[]) => Object.fromEntries(list.map((s) => [s.value_id, s]));

export function WatchWorkspace(props: WatchWorkspaceProps) {
  const { watch, catalog, hours, buckets, values, incidents } = props;
  const runtimeI18n = useBehaviorI18n();
  const i18n = props.i18n ?? runtimeI18n ?? ENGLISH_PW;
  const t = i18n.t;
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
    for (const v of items) map.set(v.id, valueText(v, i18n));
    return map;
  }, [items, i18n]);
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

  const notes = honestyNotes({ watch, catalog, buckets, hours, i18n });
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
      ? t("propertyWatch.workspace.emptyChartNoBucket")
      : items.length === 0
        ? t("propertyWatch.workspace.emptyChartNoValues")
        : t("propertyWatch.workspace.emptyChartSelect");

  const valuesTab = () => (
    <div className="stack-6">
      <div className="pw-range">
        <nav className="range-selector" aria-label={t("propertyWatch.workspace.window")}>
          <span className="muted">{t("propertyWatch.workspace.window")}</span>
          {PW_RANGES.map((r) => (
            <Link
              key={r.key}
              href={
                r.key === "24h"
                  ? `${valuesPath}${props.sort === "events" ? "" : `?sort=${props.sort}`}`
                  : `${valuesPath}?range=${r.key}${props.sort === "events" ? "" : `&sort=${props.sort}`}`
              }
              className={`range-option${r.hours === hours ? " range-option-active" : ""}`}
              aria-current={r.hours === hours ? "page" : undefined}
            >
              {pwRangeLabel(r, i18n)}
            </Link>
          ))}
        </nav>
        <p className="subtitle">
          {t("propertyWatch.workspace.lastTo", {
            range:
              i18n.locale === "en"
                ? hours === 168 ? "7 days" : `${hours} h`
                : hours === 168
                  ? t("propertyWatch.common.days", { count: 7 })
                  : t("propertyWatch.common.hours", { count: hours }),
            time: utcTimeText(props.windowEnd),
          })}
          {latest && (
            <>
              {t("propertyWatch.workspace.latestBucket", {
                origin: latest.origin === "HISTORY" ? t("propertyWatch.workspace.historyOrigin") : "",
                time: utcTimeText(latest.bucket_start),
                age: `(${relativeAge(latest.bucket_start, new Date(props.windowEnd), i18n)})`,
              })}
            </>
          )}
        </p>
      </div>

      {failed && (
        <div className="notice" role="alert">
          <p>
            <strong>{t("propertyWatch.workspace.seriesFailedTitle")}</strong>
          </p>
          <p>
            {t("propertyWatch.workspace.seriesFailedBody")}{" "}
            <button type="button" className="action secondary" onClick={() => setRetry((n) => n + 1)}>
              {t("propertyWatch.workspace.tryAgain")}
            </button>
          </p>
        </div>
      )}
      {waiting.length > 0 && !failed && (
        <p className="notice" role="status" aria-live="polite">
          {t("propertyWatch.workspace.seriesLoading", { count: waiting.length })}
        </p>
      )}

      <WatchHonesty notes={notes} i18n={i18n} />

      <section aria-label={t("propertyWatch.workspace.chartLabel")}>
        <PropertyWatchSeriesChart
          series={plotted}
          bucketSeconds={bucketSeconds}
          windowStart={props.windowStart}
          windowEnd={props.windowEnd}
          collected={buckets ? buckets.map((b) => b.bucket_start) : undefined}
          coverage={buckets ? { buckets, floor: catalog?.min_coverage ?? null } : null}
          incidents={spans}
          emptyText={emptyChart}
          i18n={i18n}
        />
        {latest && latest.unparsed_events !== null && latest.total_events !== null && (
          <p className="subtitle pw-remainder">
            {t("propertyWatch.workspace.remainder", {
              unparsed: formatCount(latest.unparsed_events, i18n.locale),
              total: formatCount(latest.total_events, i18n.locale),
              complete: watch.properties.length === 1 ? "" : t("propertyWatch.workspace.completeSet"),
              property: watch.properties.map((property) => property.name).join(t("propertyWatch.format.propertyJoin")),
              bound: latest.values_measurement === "LOWER_BOUND" ? t("propertyWatch.workspace.lowerBound") : "",
            })}
          </p>
        )}
      </section>

      <section aria-labelledby="pw-values-title">
        <h2 id="pw-values-title" className="section-title">
          {t("propertyWatch.common.values")}
        </h2>
        {values === null ? (
          <div className="notice" role="alert">
            <p>
              <strong>{t("propertyWatch.workspace.valuesFailedTitle")}</strong>
            </p>
            <p>{t("propertyWatch.workspace.valuesFailedBody")}</p>
          </div>
        ) : items.length === 0 ? (
          <div className="notice" role="status">
            <p>
              <strong>{t("propertyWatch.workspace.valuesEmptyTitle")}</strong>
            </p>
            <p>
              {t("propertyWatch.workspace.valuesEmptyBody")}
            </p>
          </div>
        ) : (
          <>
            <p className="subtitle">
              {t("propertyWatch.workspace.plotted", { selected: formatCount(selected.length, i18n.locale), max: formatCount(PW_MAX_SERIES, i18n.locale) })}
            </p>
            <TableScroll label={t("propertyWatch.valuesTable.label")}>
              <WatchValuesTable
                values={items}
                latest={latest}
                selected={new Set(selected)}
                maxSelected={PW_MAX_SERIES}
                onToggle={toggle}
                incidents={incidentByValue}
                sort={props.sort}
                sortHref={sortHref}
                history={watch.history}
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
      label={t("propertyWatch.workspace.tabsLabel")}
      tabs={[
        { id: "values", label: t("propertyWatch.workspace.tabValues"), content: valuesTab },
        {
          id: "incidents",
          label: incidents
            ? t("propertyWatch.workspace.tabIncidentsCount", { count: props.incidentsTotal ?? incidents.length })
            : t("propertyWatch.workspace.tabIncidents"),
          content: () => (
            <WatchIncidents incidents={incidents} total={props.incidentsTotal} watchId={watch.id} />
          ),
        },
        {
          id: "rules",
          label: watch.threshold_rules
            ? t("propertyWatch.workspace.tabRulesCount", { count: watch.threshold_rules.length })
            : t("propertyWatch.workspace.tabRules"),
          content: () => (
            <ThresholdRules
              rules={watch.threshold_rules ?? null}
              access={props.ruleAccess ?? "unknown"}
            />
          ),
        },
        {
          id: "definition",
          label: t("propertyWatch.workspace.tabDefinition"),
          content: () => (
            <WatchDefinition watch={watch} catalog={catalog} scope={props.scope} aql={props.aql ?? null} i18n={i18n} />
          ),
        },
      ]}
    />
  );
}

