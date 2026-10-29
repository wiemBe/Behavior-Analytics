// Against what normal: the detector's own working for a WAF incident.
//
// Projected by the API from the stored evidence by whitelist
// (`WafDetectionDetailOut`); the UI renders it and never recomputes it. A
// field the detector did not compute is an em dash, which is not a zero.

import { useTranslations } from "next-intl";

import type { WafAnomalyDetail } from "@/lib/api";
import {
  formatCount,
  formatMetric,
  formatShare,
  robustScoreMeaning,
} from "@/lib/behavior";
import { useBackendMessage } from "@/i18n/backendMessage";
import { scoreStatusCode, versionLabel } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { formatPercent } from "@/lib/format";
import { formatRequests, requestsUnit } from "./wafFormat";
import { RANGE_SEP } from "./wafI18n";
import { isNewEndpointType, isRatioType } from "./wafSummary";

function Missing({ reason }: { reason: string }) {
  return (
    <span className="muted">
      —<span className="sr-only"> {reason}</span>
    </span>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

export function WafDetectionBlock({
  anomaly,
  bucketSeconds = null,
}: {
  anomaly: WafAnomalyDetail;
  /** Bucket width from the stored buckets, for the unit; null when unknown. */
  bucketSeconds?: number | null;
}) {
  const t = useTranslations("waf.detection");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  // The detector's own sentences, in the reader's language when the API sent their code.
  const say = useBackendMessage();
  const d = anomaly.detection;
  if (!d) {
    return <div className="notice">{t("none")}</div>;
  }

  const reason =
    d.reason != null
      ? say({ text: d.reason, code: d.reason_code, params: d.reason_params })
      : anomaly.explanation != null
        ? say({
            text: anomaly.explanation,
            code: anomaly.explanation_code,
            params: anomaly.explanation_params,
          })
        : null;
  const caveat = d.caveat
    ? say({ text: d.caveat, code: d.caveat_code, params: d.caveat_params })
    : null;
  const ratio = isRatioType(anomaly.anomaly_type);
  const robustZ =
    d.robust_score_status === "DEGENERATE" ? (
      <Missing reason={t("zNotUsed")} />
    ) : d.robust_score_status === "UNAVAILABLE" ? (
      <Missing reason={t("zNotComputed")} />
    ) : (
      formatMetric(d.robust_z, 2, locale)
    );
  const hasRange = d.expected_low != null && d.expected_high != null;
  // A range carries its unit; a missing one says it was not recorded rather than
  // standing as a bare dash.
  const range = !hasRange ? (
    <span className="muted">
      —<span className="sr-only"> {t("rangeNotRecorded")}</span>
    </span>
  ) : ratio ? (
    <>
      {formatPercent(d.expected_low, null, 1, locale)}
      {RANGE_SEP}
      {formatPercent(d.expected_high, null, 1, locale)}
      <span className="muted"> {t("ofBucketRequests")}</span>
    </>
  ) : (
    <>
      {formatRequests(d.expected_low, null, locale)}
      {RANGE_SEP}
      {formatRequests(d.expected_high, null, locale)}
      <span className="muted"> {requestsUnit(bucketSeconds, i18n)}</span>
    </>
  );
  if (isNewEndpointType(anomaly.anomaly_type)) {
    // Judged by a materiality floor on the cumulative count, not by a robust score against a
    // seasonal baseline: those items do not exist for this detector, so none is drawn.
    return (
      <div className="stack-6">
        {reason && <p className="waf-reason">{reason}</p>}
        <dl className="dimension-meta">
          <Row
            label={t("floor")}
            value={
              d.threshold != null ? (
                <>{t("floorValue", { count: formatCount(d.threshold, locale) })}</>
              ) : (
                <Missing reason={t("floorMissing")} />
              )
            }
          />
          <Row
            label={t("history")}
            value={
              d.baseline_sample_count != null ? (
                <>
                  {t("historyBuckets", { count: formatCount(d.baseline_sample_count, locale) })}
                  <span className="muted"> {t("historyBelow")}</span>
                </>
              ) : (
                <Missing reason={t("historyMissing")} />
              )
            }
          />
          <Row
            label={t("consecutive")}
            value={formatCount(anomaly.consecutive_buckets, locale)}
          />
        </dl>
        {caveat && <p className="subtitle">{caveat}</p>}
      </div>
    );
  }

  return (
    <div className="stack-6">
      {reason && <p className="waf-reason">{reason}</p>}

      <dl className="dimension-meta">
        <Row label={ratio ? t("seasonalRange") : t("expectedRange")} value={range} />
        {/* One-sided: the detectors fire on an increase only. */}
        <Row
          label={t("threshold")}
          value={d.threshold != null ? `+${formatMetric(d.threshold, 2, locale)}` : "—"}
        />
        <Row label={t("samples")} value={formatCount(d.baseline_sample_count, locale)} />
        <Row label={t("completeness")} value={formatShare(d.baseline_completeness, locale)} />
        <Row
          label={t("baselineVersion")}
          value={d.baseline_version != null ? versionLabel(d.baseline_version) : "—"}
        />
        <Row
          label={t("normalizerVersion")}
          value={d.normalizer_version != null ? versionLabel(d.normalizer_version) : "—"}
        />
        <Row label={t("robustZ")} value={robustZ} />
        <Row
          label={t("consecutive")}
          value={formatCount(anomaly.consecutive_buckets, locale)}
        />
      </dl>

      {d.robust_score_status && (
        <p className="subtitle">
          <strong>{t("scoreStatus", { status: scoreStatusCode(i18n, d.robust_score_status) })}</strong>{" "}
          {robustScoreMeaning(d.robust_score_status, i18n)}
        </p>
      )}
      {caveat && <p className="subtitle">{caveat}</p>}
    </div>
  );
}
