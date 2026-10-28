"use client";

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
import { formatMetric, formatRatio, robustScoreMeaning, stateMeaningIn, stateTone } from "@/lib/behavior";
import { enumLabel } from "@/i18n/enumLabels";
import { formatCount } from "@/lib/format";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import {
  eventsUnit,
  figureText,
  formatEvents,
  newValueFigures,
  pwTypeLabel,
  scopeKindLabel,
} from "./pwFormat";
import { pwSentence } from "./pwReason";
import { valueText } from "./WatchValuesTable";
import { ENGLISH_PW, type PwI18n } from "./pwText";
import { GuideLink } from "@/components/guide/GuideLink";
import { guideFor } from "@/lib/guideRoutes";

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
const RANGE_ARROW = " → ";
const RANGE_DASH = " – ";

function Recorded({ children, ok, i18n }: { children: React.ReactNode; ok: boolean; i18n: PwI18n }) {
  return ok ? <>{children}</> : <span className="muted">{i18n.t("propertyWatch.common.notRecorded")}</span>;
}

export function PwInvestigation({
  anomaly,
  data,
  i18n: providedI18n,
}: {
  anomaly: PwAnomalyDetail;
  data: PwInvestigationData;
  i18n?: PwI18n;
}) {
  const runtimeI18n = useBehaviorI18n();
  const i18n = providedI18n ?? runtimeI18n ?? ENGLISH_PW;
  const t = i18n.t;
  const ref = anomaly.pw_value;
  const d = anomaly.detection;
  const text = valueText(ref, i18n);
  const volume = VOLUME.has(anomaly.anomaly_type);
  const bucketSeconds = data.buckets?.[0]?.bucket_seconds ?? 300;
  const unit = eventsUnit(bucketSeconds, i18n);
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
  const reason = d?.reason
    ? pwSentence(d.reason, { code: d.reason_code, params: d.reason_params }, i18n)
    : anomaly.explanation
      ? pwSentence(
          anomaly.explanation,
          { code: anomaly.explanation_code, params: anomaly.explanation_params },
          i18n,
        )
      : null;
  const historyEvidence =
    (anomaly.anomaly_type === "PW_NEW_VALUE" || anomaly.anomaly_type === "PW_NEW_COMBINATION") &&
    d?.history_buckets_from_history !== null &&
    d?.history_buckets_from_history !== undefined &&
    d.history_floor !== null &&
    d.history_buckets_verified !== null
      ? {
          days: (d.history_buckets_verified * bucketSeconds) / 86_400,
          before: anomaly.anomaly_start ?? anomaly.detected_at,
          verified: d.history_buckets_verified,
          fromHistory: d.history_buckets_from_history,
          floor: d.history_floor,
        }
      : null;
  return (
    <>
      <header className="page-header incident-header">
        <div>
          <p className="incident-back">
            <Link href="/anomalies">{t("propertyWatch.investigation.back")}</Link>
          </p>
          <div className="row incident-identity">
            <span className="incident-detector">{pwTypeLabel(anomaly.anomaly_type, i18n)}</span>
            <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>{enumLabel(i18n.t, "anomalyState", anomaly.state)}</span>
            <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>{enumLabel(i18n.t, "severityPill", anomaly.severity)}</span>
            <span className="incident-interval">
              {anomaly.anomaly_start ? (
                <>
                  <UtcTime value={anomaly.anomaly_start} />
                  {RANGE_ARROW}
                  {anomaly.anomaly_end ? (
                    <UtcTime value={anomaly.anomaly_end} />
                  ) : (
                    t("propertyWatch.investigation.stillRunning")
                  )}
                </>
              ) : (
                t("propertyWatch.investigation.intervalMissing")
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
            {t(valueId ? "propertyWatch.investigation.openValue" : "propertyWatch.investigation.openWatch")}
          </Link>
          <GuideLink section={guideFor("/anomalies/[id]")} />
        </div>
      </header>

      {valueId === null && (
        <p className="notice" role="status">
          {t("propertyWatch.investigation.retained")}
        </p>
      )}
      {reason && <p className="incident-summary">{reason}</p>}
      <p className="subtitle">{stateMeaningIn(anomaly.state, i18n)}</p>

      <section aria-labelledby="pw-inc-what">
        <h2 id="pw-inc-what" className="section-title">
          {t("propertyWatch.investigation.what")}
        </h2>
        <dl className="dimension-meta pw-dl">
          <div>
            <dt>{t("propertyWatch.investigation.watch")}</dt>
            <dd>
              <Link href={watchHref}>{ref.watch_name}</Link>
              <span className="muted">{t("propertyWatch.investigation.version", { version: formatCount(ref.watch_version, i18n.locale) })}</span>
            </dd>
          </div>
          <div>
            <dt>{t("propertyWatch.investigation.scope")}</dt>
            <dd>{scopeKindLabel(ref.scope_kind, i18n)}</dd>
          </div>
          <div>
            <dt>{t(ref.properties.length === 1 ? "propertyWatch.common.property" : "propertyWatch.common.properties")}</dt>
            <dd className="pw-prop-name">{ref.properties.map((p) => p.name).join(" × ")}</dd>
          </div>
          <div>
            <dt>{t("propertyWatch.investigation.consecutive")}</dt>
            <dd className="num">{formatCount(anomaly.consecutive_buckets, i18n.locale)}</dd>
          </div>
          <div>
            <dt>{t("propertyWatch.investigation.detected")}</dt>
            <dd className="num">
              <UtcTime value={anomaly.detected_at} />
            </dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="pw-inc-det">
        <h2 id="pw-inc-det" className="section-title">
          {t("propertyWatch.investigation.against")}
        </h2>
        {d === null ? (
          <div className="notice" role="status">
            {t("propertyWatch.investigation.workingMissing")}
          </div>
        ) : volume ? (
          <dl className="dimension-meta pw-dl" aria-label={t("propertyWatch.investigation.figures")}>
            <div>
              <dt>{t("propertyWatch.investigation.observed")}</dt>
              <dd className="num">
                <Recorded ok={observed != null} i18n={i18n}>
                  {formatEvents(observed, measurement, i18n)}
                  <span className="muted"> {unit}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.expected")}</dt>
              <dd className="num">
                <Recorded ok={band !== null} i18n={i18n}>
                  {formatEvents(d.expected_low, undefined, i18n)}{RANGE_DASH}{formatEvents(d.expected_high, undefined, i18n)}
                  <span className="muted"> {unit}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.median")}</dt>
              <dd className="num">
                <Recorded ok={(d.expected_median ?? anomaly.expected_value) != null} i18n={i18n}>
                  {formatMetric(d.expected_median ?? anomaly.expected_value, 1, i18n.locale)}
                  <span className="muted"> {unit}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.deviation")}</dt>
              <dd className="num">
                <Recorded ok={(anomaly.deviation_ratio ?? d.ratio) != null} i18n={i18n}>
                  {formatRatio(anomaly.deviation_ratio ?? d.ratio, i18n.locale)}
                  <span className="muted">{t("propertyWatch.investigation.ofMedian")}</span>
                </Recorded>
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.robust")}</dt>
              <dd className="num">
                <Recorded ok={d.robust_z != null} i18n={i18n}>{formatMetric(d.robust_z, 2, i18n.locale)}</Recorded>
                {d.threshold != null && (
                  <span className="muted">{t("propertyWatch.investigation.threshold", { value: formatMetric(d.threshold, 1, i18n.locale) })}</span>
                )}
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.sample")}</dt>
              <dd className="num">
                <Recorded ok={d.baseline_sample_count != null} i18n={i18n}>
                  {figureText({ label: "", value: d.baseline_sample_count, unit: "buckets" }, i18n)}
                </Recorded>
                <span className="muted">{t("propertyWatch.investigation.sampleHour")}</span>
              </dd>
            </div>
          </dl>
        ) : (
          <dl className="dimension-meta pw-dl" aria-label={t("propertyWatch.investigation.figures")}>
            <div>
              <dt>{t("propertyWatch.investigation.observed")}</dt>
              <dd className="num">
                {figureText({
                  label: "",
                  value: observed ?? null,
                  unit: "events",
                  lowerBound: lower,
                }, i18n)}
              </dd>
            </div>
            {newValueFigures(d, lower, i18n).map((f) => (
              <div key={f.label}>
                <dt>{f.label}</dt>
                <dd className="num">{figureText(f, i18n)}</dd>
              </div>
            ))}
            {d.classification && (
              <div>
                <dt>{t("propertyWatch.investigation.classification")}</dt>
                <dd>{pwTypeLabel(d.classification, i18n)}</dd>
              </div>
            )}
          </dl>
        )}
        {d?.robust_score_status && volume && d.robust_score_status !== "OK" && (
          <p className="subtitle">{robustScoreMeaning(d.robust_score_status as "DEGENERATE", i18n)}</p>
        )}
        {d?.claim && (
          <p className="subtitle">
            {pwSentence(d.claim, { code: d.claim_code, params: d.claim_params }, i18n)}.
          </p>
        )}
        {historyEvidence && (
          <p className="subtitle">
            {t("propertyWatch.investigation.historyEvidence", {
              days: Number.isInteger(historyEvidence.days)
                ? formatCount(historyEvidence.days, i18n.locale)
                : formatMetric(historyEvidence.days, 1, i18n.locale),
              before: utcTimeText(historyEvidence.before),
              verified: formatCount(historyEvidence.verified, i18n.locale),
              history: formatCount(historyEvidence.fromHistory, i18n.locale),
              floor: utcTimeText(historyEvidence.floor),
            })}
          </p>
        )}
        {lower && (
          <p className="subtitle">
            {t("propertyWatch.investigation.lowerBound")}
          </p>
        )}
      </section>

      <section aria-labelledby="pw-inc-series">
        <h2 id="pw-inc-series" className="section-title">
          {t("propertyWatch.investigation.series")}
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
            emptyText={t("propertyWatch.investigation.seriesEmpty")}
            i18n={i18n}
          />
        )}
      </section>

      {anomaly.explanation_package && (
        <section aria-labelledby="pw-inc-evidence">
          <h2 id="pw-inc-evidence" className="section-title">
            {t("propertyWatch.investigation.evidence")}
          </h2>
          <dl className="dimension-meta pw-dl">
            <div>
              <dt>{t("propertyWatch.common.state")}</dt>
              <dd>{anomaly.explanation_package.status}</dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.incidentWindow")}</dt>
              <dd className="num">
                <UtcTime value={anomaly.explanation_package.anomaly_window_start} />
                {RANGE_ARROW}
                <UtcTime value={anomaly.explanation_package.anomaly_window_end} />
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.incidentEvents")}</dt>
              <dd className="num">
                {formatCount(anomaly.explanation_package.anomaly_total_events, i18n.locale)}
              </dd>
            </div>
            <div>
              <dt>{t("propertyWatch.investigation.comparisonEvents")}</dt>
              <dd className="num">
                {formatCount(anomaly.explanation_package.baseline_total_events, i18n.locale)}
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
          {t("propertyWatch.investigation.lifecycle")}
        </h2>
        <LifecycleTimeline transitions={transitions} policyVersion={anomaly.policy_version} />
      </section>
    </>
  );
}
