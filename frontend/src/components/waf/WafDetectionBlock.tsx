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

import { formatRequests } from "./wafFormat";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

export function WafDetectionBlock({ anomaly }: { anomaly: WafAnomalyDetail }) {
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
  return (
    <div className="stack-6">
      {reason && <p className="waf-reason">{reason}</p>}

      <dl className="dimension-meta">
        <Row
          label="Expected range at detection"
          value={
            d.expected_low != null && d.expected_high != null
              ? `${formatRequests(d.expected_low)} – ${formatRequests(d.expected_high)}`
              : "—"
          }
        />
        <Row label="Surprise threshold" value={d.threshold != null ? `±${formatMetric(d.threshold)}` : "—"} />
        <Row label="Baseline samples" value={formatCount(d.baseline_sample_count)} />
        <Row label="Baseline completeness" value={formatShare(d.baseline_completeness)} />
        <Row label="Baseline version" value={d.baseline_version != null ? `v${d.baseline_version}` : "—"} />
        <Row
          label="Normalizer version"
          value={d.normalizer_version != null ? `v${d.normalizer_version}` : "—"}
        />
        <Row label="Robust z" value={formatMetric(d.robust_z)} />
        <Row label="Consecutive abnormal buckets" value={formatCount(anomaly.consecutive_buckets)} />
      </dl>

      {d.robust_score_status && (
        <p className="subtitle">
          <strong>Robust score {d.robust_score_status}.</strong>{" "}
          {robustScoreMeaning(d.robust_score_status)}
        </p>
      )}
    </div>
  );
}
