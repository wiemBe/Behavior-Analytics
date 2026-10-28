// The WAF investigation view: what changed on this endpoint, against what normal.
//
// Composition only. The page fetches; this arranges. Order follows Phase A's
// established hierarchy (guidelines §3): who and what, the deterministic
// summary, the measurements, the timeline, then the working. Sections that only
// exist for log-source volume -- the contributor headline, the AQL provenance --
// are absent here, not rendered empty.
//
// The page passes its `BehaviorI18n` down: the summary, the state words and the
// chart's prose are built by the helpers in the interface language.

import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import type { WafAnomalyDetail, WafBaselineCell, WafMetricBucket } from "@/lib/api";
import { stateMeaningIn } from "@/lib/behavior";
import { fixed, type BehaviorI18n } from "@/lib/behaviorI18n";
import { formatCount, formatShare } from "@/lib/format";
import { useTranslations } from "next-intl";

import { WafDetectionBlock } from "./WafDetectionBlock";
import { WafIncidentHeader } from "./WafIncidentHeader";
import { WafIncidentMetrics } from "./WafIncidentMetrics";
import { WafRequestChart } from "./WafRequestChart";
import type { BaselineSource } from "./wafBaseline";
import { stateLabel, statePillText } from "./wafLabels";
import { buildWafRatioSeries, buildWafSeries, ratioLabel, ratioSpecFor } from "./wafSeries";
import { isNewEndpointType, summarizeWafAnomaly } from "./wafSummary";
import { ENGLISH_WAF } from "./wafI18n";

/** The chart as prose: what a reader of the canvas would see. */
export function wafChartSummary(
  anomaly: WafAnomalyDetail,
  buckets: WafMetricBucket[],
  i18n: BehaviorI18n,
): string {
  const { t, locale } = i18n;
  const spec = ratioSpecFor(anomaly.anomaly_type);
  const points = spec ? buildWafRatioSeries(buckets, spec) : buildWafSeries(buckets);
  const unobserved = points.filter((p) => p.observed == null).length;
  const lower = points.filter((p) => p.lowerBound).length;
  const endpoint = `${anomaly.waf_endpoint.http_method} ${anomaly.waf_endpoint.normalized_endpoint}`;
  const parts = [
    spec
      ? t("waf.investigation.ratioLead", {
          label: ratioLabel(spec, i18n),
          endpoint,
          count: formatCount(buckets.length, locale),
        })
      : t("waf.investigation.volumeLead", {
          endpoint,
          count: formatCount(buckets.length, locale),
        }),
  ];
  const d = anomaly.detection;
  if (spec) {
    if (d?.expected_proportion != null) {
      parts.push(
        t("waf.investigation.expectedProportion", { share: formatShare(d.expected_proportion, locale) }),
      );
    }
  } else if (d?.expected_low != null && d.expected_high != null) {
    parts.push(
      t("waf.investigation.expectedRange", {
        // The English page historically stated both bounds to one decimal. Keep that precision
        // while changing only the decimal punctuation with the interface locale.
        low: fixed(d.expected_low, 1, locale),
        high: fixed(d.expected_high, 1, locale),
      }),
    );
  }
  if (unobserved > 0) {
    parts.push(
      spec
        ? t("waf.investigation.gapsRatio", { count: formatCount(unobserved, locale) })
        : t("waf.investigation.gapsVolume", { count: formatCount(unobserved, locale) }),
    );
  }
  if (lower > 0) {
    parts.push(t("waf.investigation.lowerBounds", { count: formatCount(lower, locale) }));
  }
  return parts.join(" ");
}

export function WafInvestigation({
  anomaly,
  buckets,
  baselines = null,
  i18n = ENGLISH_WAF,
}: {
  anomaly: WafAnomalyDetail;
  /** Stored buckets around the incident, or null when they could not be loaded. */
  buckets: WafMetricBucket[] | null;
  /** The endpoint's seasonal cells, or null when they could not be loaded. */
  baselines?: WafBaselineCell[] | null;
  /** The interface language of the summary, the state words and the chart's prose. */
  i18n?: BehaviorI18n;
}) {
  const t = useTranslations("waf.investigation");
  const bucketSeconds = buckets?.[0]?.bucket_seconds ?? null;
  const summary = summarizeWafAnomaly(anomaly, bucketSeconds, i18n);
  const d = anomaly.detection;
  // A proportion detector charts its own proportion; volume and novelty
  // detectors chart requests.
  const ratio = ratioSpecFor(anomaly.anomaly_type);
  // Only a detector judged against a seasonal cell has a seasonal band: a
  // novelty detector's premise is that there is no history to compare with.
  const seasonal = ratio != null || anomaly.anomaly_type === "WAF_REQUEST_RATE_SPIKE";
  const baseline: BaselineSource | null =
    baselines && seasonal
      ? {
          cells: baselines,
          metric: ratio ? ratio.baselineMetric : "request_count",
          normalizerVersion: d?.normalizer_version ?? anomaly.waf_endpoint.normalizer_version,
        }
      : null;
  // A new endpoint expects none: the chart draws no expected line, not a line at 0. Its axis
  // stays per bucket, because the series really is per bucket.
  const noneExpected = isNewEndpointType(anomaly.anomaly_type);
  const expected = noneExpected
    ? null
    : ratio
    ? (d?.expected_proportion ?? anomaly.expected_value)
    : (d?.expected_requests ?? anomaly.expected_value);

  return (
    <>
      <WafIncidentHeader anomaly={anomaly} />

      <p className="incident-summary">{summary.text}</p>
      {summary.caveat && <p className="subtitle">{summary.caveat}</p>}
      <p className="subtitle">{stateMeaningIn(anomaly.state, i18n)}</p>

      <WafIncidentMetrics anomaly={anomaly} bucketSeconds={bucketSeconds} />

      <section>
        <h2 className="section-title">{t("timeline")}</h2>
        {buckets == null ? (
          <div className="notice" role="alert">
            {t("timelineFailed")}
          </div>
        ) : (
          <WafRequestChart
            buckets={buckets}
            ratio={ratio}
            baseline={baseline}
            expected={expected}
            expectedLow={d?.expected_low ?? null}
            expectedHigh={d?.expected_high ?? null}
            anomalies={
              anomaly.anomaly_start
                ? [{ start: anomaly.anomaly_start, end: anomaly.anomaly_end }]
                : []
            }
            transitions={anomaly.transitions.map((tr) => ({
              at: tr.occurred_at,
              label: stateLabel(tr.to_state, i18n),
            }))}
            textSummary={wafChartSummary(anomaly, buckets, i18n)}
          />
        )}
      </section>

      <section>
        <h2 className="section-title">{t("against")}</h2>
        <WafDetectionBlock anomaly={anomaly} bucketSeconds={bucketSeconds} />
      </section>

      <section>
        <h2 className="section-title">{t("lifecycle")}</h2>
        <LifecycleTimeline
          transitions={anomaly.transitions}
          policyVersion={anomaly.policy_version}
          stateLabel={(state) => statePillText(state, i18n)}
        />
      </section>
    </>
  );
}
