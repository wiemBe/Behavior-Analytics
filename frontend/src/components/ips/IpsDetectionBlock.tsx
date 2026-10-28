// What was judged: the detector's own working for an IPS incident.
//
// Projected by the API from the stored evidence by whitelist
// (`IpsDetectionDetailOut`); the UI renders it and never recomputes it. A field
// the detector did not compute is an em dash with its reason, which is not a zero.
// There is no contributor list here: the API serves none for IPS, by design.

import { useTranslations } from "next-intl";

import type { IpsAnomalyDetail } from "@/lib/api";
import {
  formatCount,
  formatMetric,
  formatShare,
  robustScoreMeaning,
} from "@/lib/behavior";

import { scoreStatusCode } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { detectorSpec, expectsNone, ipsFigures } from "./ipsDetectors";
import { bucketWidth, formatFigure, unitText } from "./ipsFormat";

import "./ips.css";

/** The dash between the two ends of an expected range. */
const RANGE_DASH = "–";

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

export function IpsDetectionBlock({
  anomaly,
  bucketSeconds = null,
}: {
  anomaly: IpsAnomalyDetail;
  /** Bucket width from the stored buckets, for the unit; null when unknown. */
  bucketSeconds?: number | null;
}) {
  const t = useTranslations("ips.detection");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const d = anomaly.detection;
  if (!d) {
    return <div className="notice">{t("noWorking")}</div>;
  }

  const reason = d.reason ?? anomaly.explanation;
  const spec = detectorSpec(anomaly.anomaly_type);
  const f = ipsFigures(anomaly, d);
  const unit = unitText(f.unit, bucketWidth(bucketSeconds), i18n);
  const robustZ =
    d.robust_score_status === "DEGENERATE" ? (
      <Missing reason={t("missing.madZero")} />
    ) : d.robust_score_status === "UNAVAILABLE" ? (
      <Missing reason={t("missing.notComputed")} />
    ) : d.robust_z != null ? (
      formatMetric(d.robust_z, 2, locale)
    ) : (
      <Missing reason={t("missing.noScore")} />
    );
  const hasRange = d.expected_low != null && d.expected_high != null;
  const range = !hasRange ? (
    <Missing
      reason={spec?.baselineMetric ? t("missing.notRecorded") : t("missing.noSeasonal")}
    />
  ) : (
    <>
      {formatFigure(f.unit, d.expected_low, undefined, i18n)} {RANGE_DASH}{" "}
      {formatFigure(f.unit, d.expected_high, undefined, i18n)}
      {unit && <span className="muted"> {unit}</span>}
    </>
  );

  if (expectsNone(anomaly.anomaly_type)) {
    // Judged by a materiality floor on the cumulative count, not by a robust score against a
    // seasonal baseline: those items do not exist for this detector, so none is drawn.
    return (
      <div className="stack-6">
        {reason && <p className="ips-reason">{reason}</p>}
        <dl className="dimension-meta">
          <Row
            label={t("floor")}
            value={
              d.threshold != null ? (
                t("materialAt", { count: formatCount(d.threshold, locale) })
              ) : (
                <Missing reason={t("missing.noFloor")} />
              )
            }
          />
          <Row
            label={t("verifiedHistory")}
            value={
              d.baseline_sample_count != null ? (
                <>
                  {t("buckets", { count: formatCount(d.baseline_sample_count, locale) })}
                  <span className="muted"> {t("belowAdmission")}</span>
                </>
              ) : (
                <Missing reason={t("missing.noHistory")} />
              )
            }
          />
          <Row
            label={t("consecutive")}
            value={formatCount(anomaly.consecutive_buckets, locale)}
          />
        </dl>
        {d.caveat && <p className="subtitle">{d.caveat}</p>}
      </div>
    );
  }

  return (
    <div className="stack-6">
      {reason && <p className="ips-reason">{reason}</p>}

      <dl className="dimension-meta">
        <Row label={f.unit === "share" ? t("rangeSeasonal") : t("rangeDetection")} value={range} />
        <Row
          label={t("robustThreshold")}
          value={
            d.threshold != null ? (
              // One-sided: the detectors fire on an increase only.
              `+${formatMetric(d.threshold, 2, locale)}`
            ) : (
              <Missing reason={t("missing.noThreshold")} />
            )
          }
        />
        <Row
          label={t("baselineSamples")}
          value={
            d.baseline_sample_count != null ? (
              formatCount(d.baseline_sample_count, locale)
            ) : (
              <Missing reason={t("missing.noSamples")} />
            )
          }
        />
        <Row
          label={t("baselineCompleteness")}
          value={
            d.baseline_completeness != null ? (
              formatShare(d.baseline_completeness, locale)
            ) : (
              <Missing reason={t("missing.noCompleteness")} />
            )
          }
        />
        <Row
          label={t("baselineVersion")}
          value={
            d.baseline_version != null ? (
              `v${d.baseline_version}`
            ) : (
              <Missing reason={t("missing.noVersion")} />
            )
          }
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
      {d.caveat && <p className="subtitle">{d.caveat}</p>}
    </div>
  );
}
