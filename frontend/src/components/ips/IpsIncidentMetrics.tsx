// The IPS incident's measurements as one comparison: observed against expected,
// how far apart, for how long.
//
// Same shape as Phase A's `IncidentMetrics` (guidelines §8): four figures, not
// four cards, with the observed value heaviest. The unit is the detector's own:
// events per bucket for the rate and signature detectors, distinct destination
// ports for fan-out, a share for concentration and block ratio. A LOWER_BOUND
// reads "at least n", and a missing value is an em dash with its reason.

import { useTranslations } from "next-intl";

import type { IpsAnomalyDetail } from "@/lib/api";
import {
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
} from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { expectsNone, ipsFigures, ofText } from "./ipsDetectors";
import {
  bucketWidth,
  formatEvents,
  formatFigure,
  formatPointChange,
  unitText,
} from "./ipsFormat";

function Missing({ reason }: { reason: string }) {
  return (
    <span className="muted">
      —<span className="sr-only"> {reason}</span>
    </span>
  );
}

export function IpsIncidentMetrics({
  anomaly,
  bucketSeconds,
}: {
  anomaly: IpsAnomalyDetail;
  /** Bucket width from the stored buckets; null when they could not be loaded. */
  bucketSeconds: number | null;
}) {
  const t = useTranslations("ips.metrics");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const d = anomaly.detection;
  const f = ipsFigures(anomaly, d);
  const share = f.unit === "share";
  const lower = d?.observed_measurement === "LOWER_BOUND";
  const m = lower ? "LOWER_BOUND" : null;
  const running = anomaly.anomaly_end == null;
  const unit = unitText(f.unit, bucketWidth(bucketSeconds), i18n);
  const noneExpected = expectsNone(anomaly.anomaly_type);
  const band =
    d?.expected_low != null && d.expected_high != null
      ? `${formatFigure(f.unit, d.expected_low, undefined, i18n)} – ${formatFigure(f.unit, d.expected_high, undefined, i18n)}`
      : null;
  const deltaUnit = f.unit === "ports" ? t("deltaPorts") : t("deltaEvents");
  const unitLabel = (
    <span className="measure-unit">
      {share ? t("ofWhat", { what: ofText(anomaly.anomaly_type, i18n) ?? t("ofEvents") }) : unit}
    </span>
  );

  return (
    <section className="measure" aria-label={t("aria")}>
      <dl className="measure-grid">
        <div className="measure-item measure-lead">
          <dt>{t("observed")}</dt>
          <dd className="measure-value">
            {f.observed == null ? (
              <Missing reason={t("missing.observed")} />
            ) : (
              formatFigure(f.unit, f.observed, m, i18n)
            )}
            {unitLabel}
          </dd>
          <dd className="measure-note">
            {lower ? t("lowerNote") : noneExpected ? t("sinceNote") : t("duringNote")}
            {share && f.numerator != null && f.denominator != null && (
              <>
                {" · "}
                <span className="num">
                  {t("counts", {
                    num: formatEvents(f.numerator, m, locale),
                    den: formatEvents(f.denominator, undefined, locale),
                  })}
                </span>
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>{t("expected")}</dt>
          <dd className="measure-value">
            {noneExpected ? (
              common("none")
            ) : f.expected == null ? (
              <Missing reason={t("missing.expected")} />
            ) : (
              formatFigure(f.unit, f.expected, undefined, i18n)
            )}
            {!noneExpected && unitLabel}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              t("noneNote")
            ) : (
              <>
                {t("seasonalNote")}
                {band && (
                  <>
                    {" · "}
                    <span className="num">{t("range", { band })}</span>
                    {!share && <span className="muted"> {unit}</span>}
                  </>
                )}
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>{t("deviation")}</dt>
          <dd className="measure-value">
            {share
              ? formatPointChange(d?.percentage_point_change, i18n)
              : formatRatio(anomaly.deviation_ratio, locale)}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              t("notDefinedNote")
            ) : share ? (
              <>
                <span className="num">{formatRatio(d?.ratio ?? anomaly.deviation_ratio, locale)}</span>{" "}
                {t("expectedWord")}
              </>
            ) : (
              t.rich("deltaAgainst", {
                value: () => (
                  <span className="num">
                    {formatDelta(d?.absolute_delta ?? anomaly.absolute_delta, locale)}
                  </span>
                ),
                unit: deltaUnit,
              })
            )}
          </dd>
        </div>

        <div className="measure-item measure-item-minor">
          <dt>{t("duration")}</dt>
          <dd className="measure-value">{formatDuration(anomaly.duration_seconds, i18n)}</dd>
          <dd className="measure-note">{running ? t("running") : t("intervalLength")}</dd>
        </div>
      </dl>

      <dl className="measure-foot measure-foot-waf">
        <div>
          <dt>{t("confidence")}</dt>
          <dd className="num">{formatMetric(anomaly.confidence, 2, locale)}</dd>
          <dd className="muted">
            {" "}
            {t("confidenceRange")}
            {!noneExpected &&
              hasConfidenceLimitation(d?.robust_score_status) &&
              ` · ${t("limitedTail")}`}
          </dd>
        </div>
      </dl>
    </section>
  );
}
