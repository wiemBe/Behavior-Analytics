// The incident's measurements: observed against expected, how far apart, and
// for how long.
//
// This is one comparison, so it is one block. The four figures it carries are
// the page's four primary metrics, but they are not four identical cards: the
// observed value is the largest, expected and deviation sit beside it at a
// lower weight, duration is smallest, and every figure carries its unit.
//
// A null is not a zero. An unmeasured value renders as an em dash, and a
// still-running incident has no duration — it is unmeasured, not `0s`.

import { useTranslations } from "next-intl";

import type { AnomalyDetail } from "@/lib/api";
import {
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
} from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

export function IncidentMetrics({
  anomaly,
  confidenceLimited,
}: {
  anomaly: AnomalyDetail;
  /** The detector's score was degenerate, so its confidence is capped. */
  confidenceLimited: boolean;
}) {
  const t = useTranslations("anomalies.metrics");
  const units = useTranslations("units");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const running = anomaly.anomaly_end == null;
  const scale = confidenceLimited
    ? `${t("confidenceScale")} · ${t("confidenceLimited")}`
    : t("confidenceScale");

  return (
    <section className="measure" aria-label={t("title")}>
      <dl className="measure-grid">
        <div className="measure-item measure-lead">
          <dt>{t("observed")}</dt>
          <dd className="measure-value">
            {formatMetric(anomaly.observed_value, 2, locale)}
            <span className="measure-unit">{units("eps")}</span>
          </dd>
          <dd className="measure-note">{t("observedNote")}</dd>
        </div>

        <div className="measure-item">
          <dt>{t("expected")}</dt>
          <dd className="measure-value">
            {formatMetric(anomaly.expected_value, 2, locale)}
            <span className="measure-unit">{units("eps")}</span>
          </dd>
          <dd className="measure-note">{t("expectedNote")}</dd>
        </div>

        <div className="measure-item">
          <dt>{t("deviation")}</dt>
          <dd className="measure-value">{formatRatio(anomaly.deviation_ratio, locale)}</dd>
          <dd className="measure-note">
            {t.rich("deviationNote", {
              delta: () => <span className="num">{formatDelta(anomaly.absolute_delta, locale)}</span>,
            })}
          </dd>
        </div>

        <div className="measure-item measure-item-minor">
          <dt>{t("duration")}</dt>
          <dd className="measure-value">{formatDuration(anomaly.duration_seconds, i18n)}</dd>
          <dd className="measure-note">{running ? t("stillRunning") : t("intervalLength")}</dd>
        </div>
      </dl>

      {/* Confidence qualifies the figures above rather than being a fifth one:
          it is the detector's own score for this verdict, and its limitation
          is stated where it is read. */}
      <dl className="measure-foot">
        <dt>{t("confidence")}</dt>
        <dd className="num">{formatMetric(anomaly.confidence, 2, locale)}</dd>
        <dd className="muted"> {scale}</dd>
      </dl>
    </section>
  );
}
