// What was judged: the detector's own working for an IPS incident.
//
// Projected by the API from the stored evidence by whitelist
// (`IpsDetectionDetailOut`); the UI renders it and never recomputes it. A field
// the detector did not compute is an em dash with its reason, which is not a zero.
// There is no contributor list here: the API serves none for IPS, by design.

import type { IpsAnomalyDetail } from "@/lib/api";
import {
  formatCount,
  formatMetric,
  formatShare,
  robustScoreMeaning,
} from "@/lib/behavior";

import { detectorSpec, expectsNone, ipsFigures } from "./ipsDetectors";
import { bucketWidth, formatFigure, unitText } from "./ipsFormat";

import "./ips.css";

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
  const spec = detectorSpec(anomaly.anomaly_type);
  const f = ipsFigures(anomaly, d);
  const unit = unitText(f.unit, bucketWidth(bucketSeconds));
  const robustZ =
    d.robust_score_status === "DEGENERATE" ? (
      <Missing reason="not used: MAD was zero" />
    ) : d.robust_score_status === "UNAVAILABLE" ? (
      <Missing reason="not computed" />
    ) : d.robust_z != null ? (
      formatMetric(d.robust_z)
    ) : (
      <Missing reason="no robust score was computed" />
    );
  const hasRange = d.expected_low != null && d.expected_high != null;
  const range = !hasRange ? (
    <Missing reason={spec?.baselineMetric ? "not recorded for this incident" : "no seasonal baseline applies to this detector"} />
  ) : (
    <>
      {formatFigure(f.unit, d.expected_low)} – {formatFigure(f.unit, d.expected_high)}
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
            label="Materiality floor"
            value={
              d.threshold != null ? (
                <>
                  material at ≥ {formatCount(d.threshold)} events
                </>
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
      {reason && <p className="ips-reason">{reason}</p>}

      <dl className="dimension-meta">
        <Row
          label={f.unit === "share" ? "Seasonal p05–p95 at detection" : "Expected range at detection"}
          value={range}
        />
        <Row
          label="Robust z threshold"
          value={
            d.threshold != null ? (
              // One-sided: the detectors fire on an increase only.
              `+${formatMetric(d.threshold)}`
            ) : (
              <Missing reason="no threshold was recorded" />
            )
          }
        />
        <Row
          label="Baseline samples"
          value={
            d.baseline_sample_count != null ? (
              formatCount(d.baseline_sample_count)
            ) : (
              <Missing reason="no baseline sample count was recorded" />
            )
          }
        />
        <Row
          label="Baseline completeness"
          value={
            d.baseline_completeness != null ? (
              formatShare(d.baseline_completeness)
            ) : (
              <Missing reason="no baseline completeness was recorded" />
            )
          }
        />
        <Row
          label="Baseline version"
          value={
            d.baseline_version != null ? (
              `v${d.baseline_version}`
            ) : (
              <Missing reason="no baseline version was recorded" />
            )
          }
        />
        <Row
          label="Robust z"
          value={robustZ}
        />
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
