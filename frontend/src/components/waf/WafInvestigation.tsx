// The WAF investigation view: what changed on this endpoint, against what normal.
//
// Composition only. The page fetches; this arranges. Order follows Phase A's
// established hierarchy (guidelines §3): who and what, the deterministic
// summary, the measurements, the timeline, then the working. Sections that only
// exist for log-source volume -- the contributor headline, the AQL provenance --
// are absent here, not rendered empty.

import { EvidenceBanner } from "@/components/behavior/EvidenceBanner";
import { DimensionExplorer } from "@/components/behavior/DimensionExplorer";
import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import type { WafAnomalyDetail, WafMetricBucket } from "@/lib/api";
import { dimensionLabel, stateMeaning } from "@/lib/behavior";

import { WafDetectionBlock } from "./WafDetectionBlock";
import { WafIncidentHeader } from "./WafIncidentHeader";
import { WafIncidentMetrics } from "./WafIncidentMetrics";
import { WafRequestChart } from "./WafRequestChart";
import { buildWafSeries } from "./wafSeries";
import { isRatioType, summarizeWafAnomaly } from "./wafSummary";

/** The chart as prose: what a reader of the canvas would see. */
export function wafChartSummary(
  anomaly: WafAnomalyDetail,
  buckets: WafMetricBucket[],
): string {
  const points = buildWafSeries(buckets);
  const unobserved = points.filter((p) => p.observed == null).length;
  const lower = points.filter((p) => p.lowerBound).length;
  const band = isRatioType(anomaly.anomaly_type) ? null : anomaly.detection;
  const parts = [
    `Requests to ${anomaly.waf_endpoint.http_method} ${anomaly.waf_endpoint.normalized_endpoint} across ${buckets.length} stored buckets.`,
  ];
  if (band?.expected_low != null && band.expected_high != null) {
    parts.push(
      `The expected range at detection was ${band.expected_low.toFixed(1)} to ${band.expected_high.toFixed(1)} requests per bucket.`,
    );
  }
  if (unobserved > 0) {
    parts.push(
      `${unobserved} intervals were partial, missing or not stored and are drawn as gaps, not as zero requests.`,
    );
  }
  if (lower > 0) parts.push(`${lower} buckets are lower bounds.`);
  return parts.join(" ");
}

export function WafInvestigation({
  anomaly,
  buckets,
}: {
  anomaly: WafAnomalyDetail;
  /** Stored buckets around the incident, or null when they could not be loaded. */
  buckets: WafMetricBucket[] | null;
}) {
  const bucketSeconds = buckets?.[0]?.bucket_seconds ?? null;
  const summary = summarizeWafAnomaly(anomaly, bucketSeconds);
  const d = anomaly.detection;
  // The chart plots request counts. A ratio detector's expected value is a
  // proportion, which is not on that axis: drawing it there would plot 0.02
  // "requests". The ratio's own figures are in the measurements above.
  const ratio = isRatioType(anomaly.anomaly_type);
  const pkg = anomaly.explanation_package;
  const truncated = (pkg?.dimensions ?? [])
    .filter((x) => x.availability === "TRUNCATED")
    .map((x) => dimensionLabel(x.dimension));

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
            expected={ratio ? null : (d?.expected_requests ?? anomaly.expected_value)}
            expectedLow={ratio ? null : (d?.expected_low ?? null)}
            expectedHigh={ratio ? null : (d?.expected_high ?? null)}
            anomalies={
              anomaly.anomaly_start
                ? [{ start: anomaly.anomaly_start, end: anomaly.anomaly_end }]
                : []
            }
            transitions={anomaly.transitions.map((t) => ({
              at: t.occurred_at,
              label: t.to_state,
            }))}
            textSummary={wafChartSummary(anomaly, buckets)}
          />
        )}
        {ratio && buckets != null && (
          <p className="subtitle">
            This detector judges a proportion of requests, not their count. The chart shows the
            endpoint&apos;s request volume for context; the proportion and its expected value are in
            the measurements above.
          </p>
        )}
      </section>

      <section>
        <h2 className="section-title">Against what normal</h2>
        <WafDetectionBlock anomaly={anomaly} />
      </section>

      <section>
        <h2 className="section-title">Evidence</h2>
        <EvidenceBanner
          status={anomaly.evidence_status}
          error={pkg?.error ?? null}
          truncatedDimensions={truncated}
        />
        {pkg && <DimensionExplorer dimensions={pkg.dimensions} />}
      </section>

      <section>
        <h2 className="section-title">Lifecycle</h2>
        <LifecycleTimeline transitions={anomaly.transitions} policyVersion={anomaly.policy_version} />
      </section>
    </>
  );
}
