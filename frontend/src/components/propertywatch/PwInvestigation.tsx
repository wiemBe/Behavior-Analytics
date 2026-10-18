// The investigation view of a property-watch incident: which value of which watch, what the
// detector measured against what it expected, and where the value sits in the watch's own
// series. Composition only: the page fetches, this arranges.
//
// The value is attacker-controlled text and is rendered as text. The page links to the watch
// with the value selected by the id `GET /anomalies/{id}` carries in `pw_value.value_id`; a value's
// text never goes in a URL. When retention has deleted the value the id is null, the incident is
// kept, and the page says so instead of linking.

import Link from "next/link";

import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import { PropertyWatchSeriesChart } from "@/components/propertywatch/PropertyWatchSeriesChart";
import type { PwAnomalyDetail, PwBucket, PwValueSeries } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { formatMetric, formatRatio, robustScoreMeaning, stateMeaning, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

import {
  eventsUnit,
  figureText,
  formatEvents,
  newValueFigures,
  pwTypeLabel,
  scopeKindLabel,
} from "./pwFormat";
import { valueText } from "./WatchValuesTable";

export interface PwInvestigationData {
  /** The value's series around the incident, or null when it could not be read. */
  series: PwValueSeries | null;
  buckets: PwBucket[] | null;
  /** Why there is no chart, in words, when there is none. */
  chartNote: string | null;
  windowStart: string;
  windowEnd: string;
}

const VOLUME = new Set(["PW_VALUE_SPIKE", "PW_VALUE_DROP", "PW_VALUE_DISAPPEARED"]);

const NOT_RECORDED = "not recorded";

function Recorded({ children, ok }: { children: React.ReactNode; ok: boolean }) {
  return ok ? <>{children}</> : <span className="muted">{NOT_RECORDED}</span>;
}

export function PwInvestigation({
  anomaly,
  data,
}: {
  anomaly: PwAnomalyDetail;
  data: PwInvestigationData;
}) {
  const ref = anomaly.pw_value;
  const d = anomaly.detection;
  const text = valueText(ref);
  const volume = VOLUME.has(anomaly.anomaly_type);
  const bucketSeconds = data.buckets?.[0]?.bucket_seconds ?? 300;
  const unit = eventsUnit(bucketSeconds);
  const watchHref = `/behavior/watches/${ref.watch_id}`;
  const measurement = d?.observed_measurement ?? anomaly.observed_measurement;
  const lower = measurement === "LOWER_BOUND";
  const valueId = ref.value_id;
  const band =
    volume && d && d.expected_low != null && d.expected_high != null
      ? {
          start: anomaly.anomaly_start ?? anomaly.detected_at,
          end: anomaly.anomaly_end,
          low: d.expected_low,
          high: d.expected_high,
        }
      : null;
  const transitions = [...(anomaly.transitions ?? [])].sort(
    (a, b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at),
  );
  const observed = d?.observed_events ?? anomaly.observed_value;
  const reason = d?.reason ?? anomaly.explanation;
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
            href={valueId ? `${watchHref}?value=${valueId}` : watchHref}
            className="action secondary button-link"
          >
            {valueId ? "Open this value on the watch" : "Open the watch"}
          </Link>
        </div>
      </header>

      {valueId === null && (
        <p className="notice" role="status">
          This value is no longer stored (retention); the incident is kept. The figures below are
          the ones recorded when it was detected.
        </p>
      )}
      {reason && <p className="incident-summary">{reason}</p>}
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
      </section>

      <section aria-labelledby="pw-inc-det">
        <h2 id="pw-inc-det" className="section-title">
          Against what normal
        </h2>
        {d === null ? (
          <div className="notice" role="status">
            No detector working was recorded for this incident, so its expected range, threshold
            and baseline cannot be shown. This is a missing record, not a normal baseline.
          </div>
        ) : volume ? (
          <dl className="dimension-meta pw-dl" aria-label="Detector figures">
            <div>
              <dt>Observed</dt>
              <dd className="num">
                <Recorded ok={observed != null}>
                  {formatEvents(observed, measurement)}
                  <span className="muted"> {unit}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>Expected range for this hour of the week</dt>
              <dd className="num">
                <Recorded ok={band !== null}>
                  {formatEvents(d.expected_low)} – {formatEvents(d.expected_high)}
                  <span className="muted"> {unit}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>Expected median</dt>
              <dd className="num">
                <Recorded ok={(d.expected_median ?? anomaly.expected_value) != null}>
                  {formatMetric(d.expected_median ?? anomaly.expected_value, 1)}
                  <span className="muted"> {unit}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>Deviation</dt>
              <dd className="num">
                <Recorded ok={(anomaly.deviation_ratio ?? d.ratio) != null}>
                  {formatRatio(anomaly.deviation_ratio ?? d.ratio)}
                  <span className="muted"> of the median</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>Robust z-score</dt>
              <dd className="num">
                <Recorded ok={d.robust_z != null}>{formatMetric(d.robust_z)}</Recorded>
                {d.threshold != null && (
                  <span className="muted"> threshold {formatMetric(d.threshold, 1)} (z-score)</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Baseline sample</dt>
              <dd className="num">
                <Recorded ok={d.baseline_sample_count != null}>
                  {figureText({ label: "", value: d.baseline_sample_count, unit: "buckets" })}
                </Recorded>
                <span className="muted"> of this hour of the week</span>
              </dd>
            </div>
          </dl>
        ) : (
          <dl className="dimension-meta pw-dl" aria-label="Detector figures">
            <div>
              <dt>Observed</dt>
              <dd className="num">
                {figureText({
                  label: "",
                  value: observed ?? null,
                  unit: "events",
                  lowerBound: lower,
                })}
              </dd>
            </div>
            {newValueFigures(d, lower).map((f) => (
              <div key={f.label}>
                <dt>{f.label}</dt>
                <dd className="num">{figureText(f)}</dd>
              </div>
            ))}
            {d.classification && (
              <div>
                <dt>Classification</dt>
                <dd>{d.classification.replace("PW_", "").replaceAll("_", " ").toLowerCase()}</dd>
              </div>
            )}
          </dl>
        )}
        {d?.robust_score_status && volume && d.robust_score_status !== "OK" && (
          <p className="subtitle">{robustScoreMeaning(d.robust_score_status as "DEGENERATE")}</p>
        )}
        {d?.claim && <p className="subtitle">{d.claim}.</p>}
        {lower && (
          <p className="subtitle">
            The observed count is a lower bound: at least that many events, so the finding is real
            and its size is not exact.
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
            detectionBand={band}
            emptyText="No series was stored for this value in this window."
          />
        )}
      </section>

      {anomaly.explanation_package && (
        <section aria-labelledby="pw-inc-evidence">
          <h2 id="pw-inc-evidence" className="section-title">
            Evidence collected
          </h2>
          <dl className="dimension-meta pw-dl">
            <div>
              <dt>Status</dt>
              <dd>{anomaly.explanation_package.status}</dd>
            </div>
            <div>
              <dt>Incident window</dt>
              <dd className="num">
                <time dateTime={anomaly.explanation_package.anomaly_window_start}>
                  {formatDateTime(anomaly.explanation_package.anomaly_window_start)}
                </time>
                {" → "}
                <time dateTime={anomaly.explanation_package.anomaly_window_end}>
                  {formatDateTime(anomaly.explanation_package.anomaly_window_end)}
                </time>
              </dd>
            </div>
            <div>
              <dt>Events in the incident window</dt>
              <dd className="num">
                {anomaly.explanation_package.anomaly_total_events.toLocaleString("en-US")}
              </dd>
            </div>
            <div>
              <dt>Events in the comparison window</dt>
              <dd className="num">
                {anomaly.explanation_package.baseline_total_events.toLocaleString("en-US")}
              </dd>
            </div>
          </dl>
          {anomaly.explanation_package.error && (
            <p className="notice" role="alert">
              {anomaly.explanation_package.error}
            </p>
          )}
        </section>
      )}

      <section aria-labelledby="pw-inc-life">
        <h2 id="pw-inc-life" className="section-title">
          Lifecycle, oldest first
        </h2>
        <LifecycleTimeline transitions={transitions} policyVersion={anomaly.policy_version} />
      </section>
    </>
  );
}
