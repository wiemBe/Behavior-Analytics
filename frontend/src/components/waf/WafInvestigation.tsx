// The WAF investigation view: what changed on this endpoint, against what normal.
//
// Composition only. The page fetches; this arranges. Order follows Phase A's
// established hierarchy (guidelines §3): who and what, the deterministic
// summary, the measurements, the timeline, then the working. Sections that only
// exist for log-source volume -- the contributor headline, the AQL provenance --
// are absent here, not rendered empty.

import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import type { WafAnomalyDetail, WafBaselineCell, WafMetricBucket } from "@/lib/api";
import { stateMeaning } from "@/lib/behavior";

import { WafDetectionBlock } from "./WafDetectionBlock";
import { WafIncidentHeader } from "./WafIncidentHeader";
import { WafIncidentMetrics } from "./WafIncidentMetrics";
import { WafRequestChart } from "./WafRequestChart";
import type { BaselineSource } from "./wafBaseline";
import { stateLabel } from "./wafLabels";
import { buildWafRatioSeries, buildWafSeries, ratioSpecFor } from "./wafSeries";
import { isNewEndpointType, summarizeWafAnomaly } from "./wafSummary";

/** The chart as prose: what a reader of the canvas would see. */
export function wafChartSummary(
  anomaly: WafAnomalyDetail,
  buckets: WafMetricBucket[],
): string {
  const spec = ratioSpecFor(anomaly.anomaly_type);
  const points = spec ? buildWafRatioSeries(buckets, spec) : buildWafSeries(buckets);
  const unobserved = points.filter((p) => p.observed == null).length;
  const lower = points.filter((p) => p.lowerBound).length;
  const endpoint = `${anomaly.waf_endpoint.http_method} ${anomaly.waf_endpoint.normalized_endpoint}`;
  const parts = [
    spec
      ? `${spec.label} for ${endpoint} across ${buckets.length} stored buckets.`
      : `Requests to ${endpoint} across ${buckets.length} stored buckets.`,
  ];
  const d = anomaly.detection;
  if (spec) {
    if (d?.expected_proportion != null) {
      parts.push(`The expected proportion at detection was ${(d.expected_proportion * 100).toFixed(1)}%.`);
    }
  } else if (d?.expected_low != null && d.expected_high != null) {
    parts.push(
      `The expected range at detection was ${d.expected_low.toFixed(1)} to ${d.expected_high.toFixed(1)} requests per bucket.`,
    );
  }
  if (unobserved > 0) {
    parts.push(
      spec
        ? `${unobserved} intervals had no measured proportion (not collected, failed, partial, not stored or no requests to divide) and are drawn as gaps, not as 0 %.`
        : `${unobserved} intervals were partial, missing or not stored and are drawn as gaps, not as zero requests.`,
    );
  }
  if (lower > 0) parts.push(`${lower} buckets are lower bounds.`);
  return parts.join(" ");
}

export function WafInvestigation({
  anomaly,
  buckets,
  baselines = null,
}: {
  anomaly: WafAnomalyDetail;
  /** Stored buckets around the incident, or null when they could not be loaded. */
  buckets: WafMetricBucket[] | null;
  /** The endpoint's seasonal cells, or null when they could not be loaded. */
  baselines?: WafBaselineCell[] | null;
}) {
  const bucketSeconds = buckets?.[0]?.bucket_seconds ?? null;
  const summary = summarizeWafAnomaly(anomaly, bucketSeconds);
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
      <p className="subtitle">{stateMeaning(anomaly.state)}</p>

      <WafIncidentMetrics anomaly={anomaly} bucketSeconds={bucketSeconds} />

      <section>
        <h2 className="section-title">Timeline</h2>
        {buckets == null ? (
          <div className="notice" role="alert">
            Request history for this endpoint could not be loaded. The rest of this investigation
            is unaffected, but the timeline is absent rather than empty.
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
            transitions={anomaly.transitions.map((t) => ({
              at: t.occurred_at,
              label: stateLabel(t.to_state),
            }))}
            textSummary={wafChartSummary(anomaly, buckets)}
          />
        )}
      </section>

      <section>
        <h2 className="section-title">Against what normal</h2>
        <WafDetectionBlock anomaly={anomaly} bucketSeconds={bucketSeconds} />
      </section>

      <section>
        <h2 className="section-title">Lifecycle</h2>
        <LifecycleTimeline
          transitions={anomaly.transitions}
          policyVersion={anomaly.policy_version}
          stateLabel={stateLabel}
        />
      </section>
    </>
  );
}
