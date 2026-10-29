// Everything that makes the verdict checkable rather than asserted.
//
// All of it was previously above the fold: eight KPI cards including robust z,
// a versions table, a fourteen-row threshold table, and a twenty-row
// provenance table with full AQL expanded inline. None of it is deleted — it
// is grouped into five disclosures, all collapsed, so an analyst auditing the
// verdict finds it in one place and an analyst triaging never has to scroll
// past it.
//
// The provenance section renders query *structure* only. The backend never
// writes a SEC token, a request header, a credential or a raw event payload
// into this document, and this component reads only the named fields below, so
// a future addition to the provenance blob cannot leak through it.

import { useTranslations } from "next-intl";

import { CodePanel } from "@/components/ui/CodePanel";
import { Disclosure } from "@/components/ui/Disclosure";
import type { AnomalyDetail, ExplanationPackage } from "@/lib/api";
import {
  dimensionLabel,
  formatCount,
  formatMetric,
  formatShare,
  formatRatio,
  hasConfidenceLimitation,
  robustScoreMeaning,
} from "@/lib/behavior";
import { useBackendMessage } from "@/i18n/backendMessage";
import { unitGap } from "@/i18n/measures";
import { ARROW, scoreStatusCode, versionLabel } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

function Missing({ reason }: { reason: string }) {
  return (
    <span className="muted">
      —<span className="sr-only"> {reason}</span>
    </span>
  );
}

export function TechnicalDetails({
  anomaly,
  packaged,
}: {
  anomaly: AnomalyDetail;
  packaged: ExplanationPackage | null;
}) {
  const t = useTranslations("anomalies.technical");
  const i18n = useBehaviorI18n();
  const backendMessage = useBackendMessage();
  const { locale } = i18n;
  const metric = (value: number | null | undefined, digits = 2) =>
    formatMetric(value, digits, locale);
  const d = anomaly.detection;
  const degenerate = hasConfidenceLimitation(d?.robust_score_status);
  const robustZ =
    d?.robust_score_status === "DEGENERATE" ? (
      <Missing reason={t("robustNotUsed")} />
    ) : d?.robust_score_status === "UNAVAILABLE" ? (
      <Missing reason={t("robustNotComputed")} />
    ) : (
      metric(d?.robust_z ?? anomaly.robust_z)
    );
  const provenance = packaged?.query_provenance ?? {};
  const queries = Array.isArray(provenance.queries) ? provenance.queries : [];

  return (
    <div className="stack-6">
      {/* 1 — detection thresholds ---------------------------------------- */}
      <Disclosure summary={t("thresholds")}>
        {/* The detector's own sentence for the verdict, in the reader's language. */}
        {d?.reason && (
          <p className="subtitle">
            <strong>{t("detectorReason")}</strong>{" "}
            {backendMessage({ text: d.reason, code: d.reason_code, params: d.reason_params })}
          </p>
        )}
        {d ? (
          <dl className="dimension-meta">
            <Row label={t("expectedEps")} value={metric(d.expected_eps ?? anomaly.expected_value)} />
            <Row label={t("observedEps")} value={metric(d.observed_eps ?? anomaly.observed_value)} />
            <Row
              label={t("expectedRange")}
              value={`${metric(d.expected_low)} – ${metric(d.expected_high)}`}
            />
            <Row
              label={t("deviationRatio")}
              value={
                <>
                  {formatRatio(d.ratio ?? anomaly.deviation_ratio, locale)}
                  {/* A spike out of a zero baseline has no ratio at all.
                      Without this note the em dash reads as "unchanged". */}
                  {d.ratio_basis === "expected_zero" && (
                    <span className="muted"> {t("zeroBaseline")}</span>
                  )}
                </>
              }
            />
            <Row label={t("expectedEvents")} value={metric(d.expected_events, 0)} />
            <Row label={t("observedEvents")} value={metric(d.observed_events, 0)} />
            <Row
              label={t("absoluteDelta")}
              value={metric(d.absolute_delta_events ?? anomaly.absolute_delta, 0)}
            />
            <Row
              label={t("bucketWidth")}
              value={
                d.bucket_seconds != null ? t("seconds", { count: d.bucket_seconds }) : "—"
              }
            />
            <Row label={t("zThreshold")} value={`±${metric(d.threshold)}`} />
            <Row
              label={t("consecutive")}
              value={formatCount(anomaly.consecutive_buckets, locale)}
            />
          </dl>
        ) : (
          <div className="notice">{t("noDetection")}</div>
        )}
      </Disclosure>

      {/* 2 — baseline and score ------------------------------------------ */}
      <Disclosure
        summary={t("baselineScore")}
        note={d?.robust_score_status ? scoreStatusCode(i18n, d.robust_score_status) : undefined}
      >
        {d ? (
          <>
            <dl className="dimension-meta">
              <Row label={t("robustZ")} value={robustZ} />
              <Row
                label={t("madStatus")}
                value={
                  d.robust_score_status
                    ? scoreStatusCode(i18n, d.robust_score_status)
                    : t("notRecorded")
                }
              />
              <Row label={t("confidence")} value={metric(anomaly.confidence)} />
              <Row label={t("baselineSamples")} value={formatCount(d.baseline_sample_count, locale)} />
              <Row
                label={t("baselineCompleteness")}
                value={formatShare(d.baseline_completeness, locale)}
              />
              <Row
                label={t("baselineVersion")}
                value={anomaly.baseline_version != null ? versionLabel(anomaly.baseline_version) : "—"}
              />
            </dl>
            {d.robust_score_status && (
              <p className="subtitle">{robustScoreMeaning(d.robust_score_status, i18n)}</p>
            )}
            {degenerate && d.fallback_bound != null && (
              <p className="subtitle">
                {t("fallback", { value: metric(d.fallback_bound) })}
              </p>
            )}
          </>
        ) : (
          <div className="notice">{t("noEvidence")}</div>
        )}
      </Disclosure>

      {/* 3 — evidence provenance ------------------------------------------ */}
      <Disclosure summary={t("provenance")}>
        {packaged ? (
          <dl className="dimension-meta">
            <Row label={t("strategy")} value={packaged.comparison_strategy} />
            <Row
              label={t("anomalyWindow")}
              value={
                <>
                  <UtcTime value={packaged.anomaly_window_start} />
                  {` ${ARROW} `}
                  <UtcTime value={packaged.anomaly_window_end} />
                </>
              }
            />
            <Row
              label={t("baselineWindow")}
              value={
                <>
                  <UtcTime value={packaged.baseline_window_start} />
                  {` ${ARROW} `}
                  <UtcTime value={packaged.baseline_window_end} />
                </>
              }
            />
            <Row
              label={t("anomalyEvents")}
              value={formatCount(packaged.anomaly_total_events, locale)}
            />
            <Row
              label={t("baselineEvents")}
              value={formatCount(packaged.baseline_total_events, locale)}
            />
            <Row label={t("requested")} value={<UtcTime value={packaged.requested_at} />} />
            <Row
              label={t("completed")}
              value={
                <>
                  {<UtcTime value={packaged.completed_at} />}
                  {packaged.collection_duration_ms != null &&
                    ` (${packaged.collection_duration_ms}${unitGap(locale)}ms)`}
                </>
              }
            />
          </dl>
        ) : (
          <div className="notice">{t("noPackage")}</div>
        )}
      </Disclosure>

      {/* 4 — the queries themselves --------------------------------------- */}
      <Disclosure
        summary={t("queries")}
        note={queries.length > 0 ? `${queries.length}` : undefined}
      >
        <p className="subtitle">{t("queriesNote")}</p>
        {queries.length === 0 ? (
          <div className="notice">{t("noQueries")}</div>
        ) : (
          <div className="stack-6">
            {queries.map((q, i) => (
              <div key={`${q.dimension}-${q.window}-${i}`}>
                <CodePanel
                  label={`${q.dimension ? dimensionLabel(q.dimension, i18n) : t("query")} · ${q.window ?? t("window")}`}
                  meta={[
                    q.rows != null ? t("rows", { count: q.rows }) : null,
                    q.truncated ? t("truncated") : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  code={q.aql ?? t("noStatement")}
                />
                {q.error && <p className="subtitle">{q.error}</p>}
              </div>
            ))}
          </div>
        )}
      </Disclosure>

      {/* 5 — versions and collection metadata ------------------------------ */}
      <Disclosure summary={t("versions")}>
        <dl className="dimension-meta">
          <Row label={t("policyVersion")} value={versionLabel(anomaly.policy_version)} />
          <Row
            label={t("baselineVersion")}
            value={anomaly.baseline_version != null ? versionLabel(anomaly.baseline_version) : "—"}
          />
          <Row
            label={t("schemaVersion")}
            value={packaged ? versionLabel(packaged.schema_version) : "—"}
          />
          <Row label={t("collectionSource")} value={t("collectionSourceValue")} />
          <Row label={t("detected")} value={<UtcTime value={anomaly.detected_at} />} />
          <Row label={t("opened")} value={<UtcTime value={anomaly.opened_at} />} />
          <Row label={t("resolved")} value={<UtcTime value={anomaly.resolved_at} />} />
        </dl>
      </Disclosure>
    </div>
  );
}
