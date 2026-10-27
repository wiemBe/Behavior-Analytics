// The WAF incident's measurements as one comparison block: observed against
// expected, how far apart, for how long.
//
// Same shape as Phase A's `IncidentMetrics` (guidelines §8): four figures, not
// four cards, with the observed value heaviest. What differs is the unit. A
// volume detector measures requests per bucket; a ratio detector measures a
// proportion of the endpoint's requests. Every figure carries its unit, a
// LOWER_BOUND reads "≥ n", and a null is an em dash.

import { useTranslations } from "next-intl";

import type { WafAnomalyDetail } from "@/lib/api";
import {
  NOT_MEASURED,
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
} from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { formatPercent } from "@/lib/format";
import { formatPoints, formatRequests, requestsUnit, sinceUnit } from "./wafFormat";
import { MID, RANGE_SEP } from "./wafI18n";
import { isLowerBound } from "./wafLabels";
import { isNewEndpointType, isRatioType } from "./wafSummary";

export function WafIncidentMetrics({
  anomaly,
  bucketSeconds,
}: {
  anomaly: WafAnomalyDetail;
  /** Bucket width from the stored buckets; null when they could not be loaded. */
  bucketSeconds: number | null;
}) {
  const t = useTranslations("waf.metrics");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const d = anomaly.detection;
  const ratio = isRatioType(anomaly.anomaly_type);
  const noneExpected = isNewEndpointType(anomaly.anomaly_type);
  const measurement = d?.request_count_measurement ?? anomaly.observed_measurement ?? null;
  const lower = isLowerBound(measurement);
  const running = anomaly.anomaly_end == null;
  // A new endpoint's count is cumulative since it first appeared, not per bucket.
  const unit = noneExpected ? sinceUnit(i18n) : requestsUnit(bucketSeconds, i18n);
  const band =
    !noneExpected && d?.expected_low != null && d.expected_high != null
      ? `${formatRequests(d.expected_low, null, locale)}${RANGE_SEP}${formatRequests(d.expected_high, null, locale)}`
      : null;

  const observed = ratio
    ? formatPercent(d?.observed_proportion ?? anomaly.observed_value, measurement, 1, locale)
    : formatRequests(d?.observed_requests ?? anomaly.observed_value, measurement, locale);
  const expected = ratio
    ? formatPercent(d?.expected_proportion ?? anomaly.expected_value, null, 1, locale)
    : formatRequests(d?.expected_requests ?? anomaly.expected_value, null, locale);

  return (
    <section className="measure" aria-label={t("title")}>
      <dl className="measure-grid">
        <div className="measure-item measure-lead">
          <dt>{t("observed")}</dt>
          <dd className="measure-value">
            {observed}
            <span className="measure-unit">{ratio ? t("ofRequests") : unit}</span>
          </dd>
          <dd className="measure-note">
            {lower
              ? t("observedLower")
              : noneExpected
                ? t("observedCumulative")
                : t("observedDuring")}
            {ratio && d?.observed_numerator != null && d.observed_denominator != null && (
              <>
                {MID}
                {t.rich("counts", {
                  num: (chunks) => <span className="num">{chunks}</span>,
                  numerator: formatRequests(d.observed_numerator, measurement, locale),
                  denominator: formatRequests(d.observed_denominator, null, locale),
                })}
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>{t("expected")}</dt>
          <dd className="measure-value">
            {noneExpected ? (
              common("none")
            ) : (
              <>
                {expected}
                <span className="measure-unit">{ratio ? t("ofRequests") : unit}</span>
              </>
            )}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              t("expectedNone")
            ) : (
              <>
                {t("expectedSeasonal")}
                {band && !ratio && (
                  <>
                    {MID}
                    {t("range")} <span className="num">{band}</span>
                    <span className="muted"> {unit}</span>
                  </>
                )}
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>{t("deviation")}</dt>
          <dd className="measure-value">
            {noneExpected
              ? NOT_MEASURED
              : ratio
                ? formatPoints(d?.percentage_point_change, i18n)
                : formatRatio(anomaly.deviation_ratio, locale)}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              t("deviationNone")
            ) : ratio ? (
              t.rich("deviationRatio", {
                num: (chunks) => <span className="num">{chunks}</span>,
                ratio: formatRatio(d?.ratio ?? anomaly.deviation_ratio, locale),
              })
            ) : (
              t.rich("deviationDelta", {
                num: (chunks) => <span className="num">{chunks}</span>,
                delta: formatDelta(d?.absolute_delta_requests ?? anomaly.absolute_delta, locale),
              })
            )}
          </dd>
        </div>

        <div className="measure-item measure-item-minor">
          <dt>{t("duration")}</dt>
          <dd className="measure-value">{formatDuration(anomaly.duration_seconds, i18n)}</dd>
          <dd className="measure-note">{running ? t("stillRunning") : t("intervalLength")}</dd>
        </div>
      </dl>

      <dl className="measure-foot measure-foot-waf">
        <div>
          <dt>{t("confidence")}</dt>
          <dd className="num">{formatMetric(anomaly.confidence, 2, locale)}</dd>
          <dd className="muted">
            {" "}
            {t("confidenceScale")}
            {!noneExpected &&
              hasConfidenceLimitation(d?.robust_score_status) &&
              `${MID}${t("confidenceLimited")}`}
          </dd>
        </div>
        {/* The new-endpoint detector does not record coverage; a bare dash would read as a figure. */}
        {!noneExpected && (
          <div>
            <dt>{t("coverage")}</dt>
            <dd className="num">{formatPercent(d?.endpoint_coverage_ratio, null, 1, locale)}</dd>
            <dd className="muted"> {t("coverageNote")}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
