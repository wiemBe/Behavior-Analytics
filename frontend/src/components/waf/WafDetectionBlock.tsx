// Against what normal: the detector's own working for a WAF incident.
//
// Projected by the API from the stored evidence by whitelist
// (`WafDetectionDetailOut`); the UI renders it and never recomputes it. A field
// the detector did not compute is an em dash, which is not a zero.

import type { WafAnomalyDetail } from "@/lib/api";
import {
  formatCount,
  formatMetric,
  formatShare,
  robustScoreMeaning,
} from "@/lib/behavior";

import { formatPercent, formatRequests, requestsUnit } from "./wafFormat";
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
  const d = anomaly.detection;
  if (!d) {
    return (
      <div className="notice">
        No detector working was recorded for this incident, so its expected range and baseline
        cannot be shown. This is a missing record, not a normal baseline.
      </div>
    );
  }

  const reason = d.reason ?? anomaly.explanation;
  const ratio = isRatioType(anomaly.anomaly_type);
  const robustZ =
    d.robust_score_status === "DEGENERATE" ? (
      <Missing reason="not used: MAD was zero" />
    ) : d.robust_score_status === "UNAVAILABLE" ? (
      <Missing reason="not computed" />
    ) : (
      formatMetric(d.robust_z)
    );
  const hasRange = d.expected_low != null && d.expected_high != null;
  // A range carries its unit; a missing one says it was not recorded rather than
  // standing as a bare dash.
  const range = !hasRange ? (
    <span className="muted">
      —<span className="sr-only"> not recorded for this incident</span>
    </span>
  ) : ratio ? (
    <>
      {formatPercent(d.expected_low)} – {formatPercent(d.expected_high)}
      <span className="muted"> of bucket requests</span>
    </>
  ) : (
    <>
      {formatRequests(d.expected_low)} – {formatRequests(d.expected_high)}
      <span className="muted"> {requestsUnit(bucketSeconds)}</span>
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
            label="Materiality floor"
            value={
              d.threshold != null ? (
                <>material at ≥ {formatCount(d.threshold)} requests</>
              ) : (
                <Missing reason="no materiality floor was recorded" />
              )
            }
          />
          <Row
            label="Verified history"
            value={
              d.baseline_sample_count != null ? (
                <>
                  {formatCount(d.baseline_sample_count)} buckets
                  <span className="muted"> below the admission threshold</span>
                </>
              ) : (
                <Missing reason="no history length was recorded" />
              )
            }
          />
          <Row label="Consecutive abnormal buckets" value={formatCount(anomaly.consecutive_buckets)} />
        </dl>
        {d.caveat && <p className="subtitle">{d.caveat}</p>}
      </div>
    );
  }

  return (
    <div className="stack-6">
      {reason && <p className="waf-reason">{reason}</p>}

      <dl className="dimension-meta">
        <Row label={ratio ? "Seasonal p05–p95 at detection" : "Expected range at detection"} value={range} />
        {/* One-sided: the detectors fire on an increase only. */}
        <Row
          label="Robust z threshold"
          value={d.threshold != null ? `+${formatMetric(d.threshold)}` : "—"}
        />
        <Row label="Baseline samples" value={formatCount(d.baseline_sample_count)} />
        <Row label="Baseline completeness" value={formatShare(d.baseline_completeness)} />
        <Row label="Baseline version" value={d.baseline_version != null ? `v${d.baseline_version}` : "—"} />
        <Row
          label="Normalizer version"
          value={d.normalizer_version != null ? `v${d.normalizer_version}` : "—"}
        />
        <Row label="Robust z" value={robustZ} />
        <Row label="Consecutive abnormal buckets" value={formatCount(anomaly.consecutive_buckets)} />
      </dl>

      {d.robust_score_status && (
        <p className="subtitle">
          <strong>Robust score {d.robust_score_status}.</strong>{" "}
          {robustScoreMeaning(d.robust_score_status)}
        </p>
      )}
      {d.caveat && <p className="subtitle">{d.caveat}</p>}
    </div>
  );
}
