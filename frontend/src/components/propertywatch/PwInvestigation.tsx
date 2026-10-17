// The investigation view of a property-watch incident: which value of which watch, what the
// detector measured against what it expected, and where the value sits in the watch's own
// series. Composition only: the page fetches, this arranges.
//
// The value is attacker-controlled text and is rendered as text. The page links to the watch
// with the value selected, by id; the id is found among the watch's tracked values, because the
// anomaly row carries the value's text and not its id, and a value's text never goes in a URL.

import Link from "next/link";

import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import { PropertyWatchSeriesChart } from "@/components/propertywatch/PropertyWatchSeriesChart";
import type { PwAnomalyDetail, PwBucket, PwValueSeries } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { formatMetric, formatRatio, stateMeaning, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

import { eventsUnit, formatEvents, pwTypeLabel, scopeKindLabel } from "./pwFormat";
import { valueText } from "./WatchValuesTable";

export interface PwInvestigationData {
  /** The tracked value's id, when it was found among the watch's values. */
  valueId: string | null;
  /** The value's series around the incident, or null when it could not be read. */
  series: PwValueSeries | null;
  buckets: PwBucket[] | null;
  /** Why there is no chart, in words, when there is none. */
  chartNote: string | null;
  windowStart: string;
  windowEnd: string;
}

const VOLUME = new Set(["PW_VALUE_SPIKE", "PW_VALUE_DROP", "PW_VALUE_DISAPPEARED"]);

export function PwInvestigation({
  anomaly,
  data,
}: {
  anomaly: PwAnomalyDetail;
  data: PwInvestigationData;
}) {
  const ref = anomaly.pw_value;
  const text = valueText(ref);
  const volume = VOLUME.has(anomaly.anomaly_type);
  const bucketSeconds = data.buckets?.[0]?.bucket_seconds ?? 300;
  const unit = eventsUnit(bucketSeconds);
  const watchHref = `/behavior/watches/${ref.watch_id}`;
  const lower = anomaly.observed_measurement === "LOWER_BOUND";
  return (
    <>
      <header className="page-header incident-header">
        <div>
          <p className="incident-back">
            <Link href="/anomalies">← All anomalies</Link>
          </p>
          <div className="row incident-identity">
            <span className="incident-detector">{pwTypeLabel(anomaly.anomaly_type)}</span>
            <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>{anomaly.state}</span>
            <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>{anomaly.severity}</span>
            <span className="incident-interval">
              {anomaly.anomaly_start ? (
                <>
                  <time dateTime={anomaly.anomaly_start}>{formatDateTime(anomaly.anomaly_start)}</time>
                  {" → "}
                  {anomaly.anomaly_end ? (
                    <time dateTime={anomaly.anomaly_end}>{formatDateTime(anomaly.anomaly_end)}</time>
                  ) : (
                    "still running"
                  )}
                </>
              ) : (
                "Interval not recorded"
              )}
            </span>
          </div>
          <h1 className="page-title incident-source pw-value-text">{text}</h1>
        </div>
        <div className="page-header-actions">
          <Link
            href={data.valueId ? `${watchHref}?value=${data.valueId}` : watchHref}
            className="action secondary button-link"
          >
            {data.valueId ? "Open this value on the watch" : "Open the watch"}
          </Link>
        </div>
      </header>

      {anomaly.explanation && <p className="incident-summary">{anomaly.explanation}</p>}
      <p className="subtitle">{stateMeaning(anomaly.state)}</p>

      <section aria-labelledby="pw-inc-what">
        <h2 id="pw-inc-what" className="section-title">
          What was measured
        </h2>
        <dl className="dimension-meta pw-dl">
          <div>
            <dt>Watch</dt>
            <dd>
              <Link href={watchHref}>{ref.watch_name}</Link>
              <span className="muted"> version {ref.watch_version}</span>
            </dd>
          </div>
          <div>
            <dt>Scope</dt>
            <dd>{scopeKindLabel(ref.scope_kind)}</dd>
          </div>
          <div>
            <dt>{ref.properties.length === 1 ? "Property" : "Properties"}</dt>
            <dd className="pw-prop-name">{ref.properties.map((p) => p.name).join(" × ")}</dd>
          </div>
          {volume ? (
            <>
              <div>
                <dt>Observed</dt>
                <dd className="num">
                  {formatEvents(anomaly.observed_value, anomaly.observed_measurement)}
                  <span className="muted"> {unit}</span>
                </dd>
              </div>
              <div>
                <dt>Expected</dt>
                <dd className="num">
                  {formatMetric(anomaly.expected_value, 1)}
                  <span className="muted"> {unit}</span>
                </dd>
              </div>
              <div>
                <dt>Deviation</dt>
                <dd className="num">{formatRatio(anomaly.deviation_ratio)}</dd>
              </div>
            </>
          ) : (
            <div>
              <dt>Observed</dt>
              <dd className="num">
                {formatMetric(anomaly.observed_value, 0)}
                <span className="muted">
                  {" "}
                  the count the detector read; the unit is the detector&apos;s own
                </span>
              </dd>
            </div>
          )}
          <div>
            <dt>Consecutive buckets</dt>
            <dd className="num">{anomaly.consecutive_buckets}</dd>
          </div>
          <div>
            <dt>Detected</dt>
            <dd className="num">
              <time dateTime={anomaly.detected_at}>{formatDateTime(anomaly.detected_at)}</time>
            </dd>
          </div>
        </dl>
        {lower && (
          <p className="subtitle">
            The observed count is a lower bound: at least that many events, so the spike is real and
            its size is not exact.
          </p>
        )}
      </section>

      <section aria-labelledby="pw-inc-series">
        <h2 id="pw-inc-series" className="section-title">
          The value around the incident
        </h2>
        {data.chartNote ? (
          <div className="notice" role="status">
            {data.chartNote}
          </div>
        ) : (
          <PropertyWatchSeriesChart
            series={data.series ? [{ ...data.series, value_key: text }] : []}
            bucketSeconds={bucketSeconds}
            windowStart={data.windowStart}
            windowEnd={data.windowEnd}
            collected={data.buckets ? data.buckets.map((b) => b.bucket_start) : undefined}
            coverage={null}
            incidents={
              anomaly.anomaly_start
                ? [{ start: anomaly.anomaly_start, end: anomaly.anomaly_end }]
                : []
            }
            emptyText="No series was stored for this value in this window."
          />
        )}
      </section>

      <section aria-labelledby="pw-inc-life">
        <h2 id="pw-inc-life" className="section-title">
          Lifecycle
        </h2>
        <LifecycleTimeline
          transitions={anomaly.transitions ?? []}
          policyVersion={anomaly.policy_version}
        />
      </section>
    </>
  );
}
