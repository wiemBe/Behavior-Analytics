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

import type { AnomalyDetail } from "@/lib/api";
import {
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
} from "@/lib/behavior";

export function IncidentMetrics({
  anomaly,
  confidenceLimited,
}: {
  anomaly: AnomalyDetail;
  /** The detector's score was degenerate, so its confidence is capped. */
  confidenceLimited: boolean;
}) {
  const running = anomaly.anomaly_end == null;

  return (
    <section className="measure" aria-label="Incident measurements">
      <dl className="measure-grid">
        <div className="measure-item measure-lead">
          <dt>Observed</dt>
          <dd className="measure-value">
            {formatMetric(anomaly.observed_value)}
            <span className="measure-unit">EPS</span>
          </dd>
          <dd className="measure-note">During the anomalous interval</dd>
        </div>

        <div className="measure-item">
          <dt>Expected</dt>
          <dd className="measure-value">
            {formatMetric(anomaly.expected_value)}
            <span className="measure-unit">EPS</span>
          </dd>
          <dd className="measure-note">Seasonal baseline, this weekday and hour</dd>
        </div>

        <div className="measure-item">
          <dt>Deviation</dt>
          <dd className="measure-value">{formatRatio(anomaly.deviation_ratio)}</dd>
          <dd className="measure-note">
            <span className="num">{formatDelta(anomaly.absolute_delta)}</span> events
            against expected
          </dd>
        </div>

        <div className="measure-item measure-item-minor">
          <dt>Duration</dt>
          <dd className="measure-value">{formatDuration(anomaly.duration_seconds)}</dd>
          <dd className="measure-note">{running ? "Still running" : "Interval length"}</dd>
        </div>
      </dl>

      {/* Confidence qualifies the figures above rather than being a fifth one:
          it is the detector's own score for this verdict, and its limitation
          is stated where it is read. */}
      <dl className="measure-foot">
        <dt>Detector confidence</dt>
        <dd className="num">{formatMetric(anomaly.confidence)}</dd>
        <dd className="muted">
          {" "}
          (0 to 1)
          {confidenceLimited &&
            " · Confidence limited because baseline variability is zero. See technical details."}
        </dd>
      </dl>
    </section>
  );
}
