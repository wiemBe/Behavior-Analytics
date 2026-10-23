// The WAF incident's measurements as one comparison block: observed against
// expected, how far apart, for how long.
//
// Same shape as Phase A's `IncidentMetrics` (guidelines §8): four figures, not
// four cards, with the observed value heaviest. What differs is the unit. A
// volume detector measures requests per bucket; a ratio detector measures a
// proportion of the endpoint's requests. Every figure carries its unit, a
// LOWER_BOUND reads "≥ n", and a null is an em dash.

import type { WafAnomalyDetail } from "@/lib/api";
import {
  NOT_MEASURED,
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
} from "@/lib/behavior";

import { formatPercent } from "@/lib/format";
import { formatPoints, formatRequests, requestsUnit, SINCE_UNIT } from "./wafFormat";
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
  const d = anomaly.detection;
  const ratio = isRatioType(anomaly.anomaly_type);
  const noneExpected = isNewEndpointType(anomaly.anomaly_type);
  const measurement = d?.request_count_measurement ?? anomaly.observed_measurement ?? null;
  const lower = isLowerBound(measurement);
  const running = anomaly.anomaly_end == null;
  // A new endpoint's count is cumulative since it first appeared, not per bucket.
  const unit = noneExpected ? SINCE_UNIT : requestsUnit(bucketSeconds);
  const band =
    !noneExpected && d?.expected_low != null && d.expected_high != null
      ? `${formatRequests(d.expected_low)} – ${formatRequests(d.expected_high)}`
      : null;

  const observed = ratio
    ? formatPercent(d?.observed_proportion ?? anomaly.observed_value, measurement)
    : formatRequests(d?.observed_requests ?? anomaly.observed_value, measurement);
  const expected = ratio
    ? formatPercent(d?.expected_proportion ?? anomaly.expected_value)
    : formatRequests(d?.expected_requests ?? anomaly.expected_value);

  return (
    <section className="measure" aria-label="Incident measurements">
      <dl className="measure-grid">
        <div className="measure-item measure-lead">
          <dt>Observed</dt>
          <dd className="measure-value">
            {observed}
            <span className="measure-unit">{ratio ? "of requests" : unit}</span>
          </dd>
          <dd className="measure-note">
            {lower
              ? "At least this; the true value may be higher"
              : noneExpected
                ? "Cumulative since the endpoint first appeared, not a per-bucket rate"
                : "During the anomalous interval"}
            {ratio && d?.observed_numerator != null && d.observed_denominator != null && (
              <>
                {" · "}
                <span className="num">
                  {formatRequests(d.observed_numerator, measurement)} of{" "}
                  {formatRequests(d.observed_denominator)}
                </span>{" "}
                requests
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>Expected</dt>
          <dd className="measure-value">
            {noneExpected ? (
              "none"
            ) : (
              <>
                {expected}
                <span className="measure-unit">{ratio ? "of requests" : unit}</span>
              </>
            )}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              "The endpoint has no verified history, so no seasonal baseline applies"
            ) : (
              <>
                Seasonal baseline, this weekday and hour
                {band && !ratio && (
                  <>
                    {" · range "}
                    <span className="num">{band}</span>
                    <span className="muted"> {unit}</span>
                  </>
                )}
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>Deviation</dt>
          <dd className="measure-value">
            {noneExpected
              ? NOT_MEASURED
              : ratio
                ? formatPoints(d?.percentage_point_change)
                : formatRatio(anomaly.deviation_ratio)}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              "Not defined: nothing was expected"
            ) : ratio ? (
              <>
                <span className="num">{formatRatio(d?.ratio ?? anomaly.deviation_ratio)}</span>{" "}
                expected
              </>
            ) : (
              <>
                <span className="num">
                  {formatDelta(d?.absolute_delta_requests ?? anomaly.absolute_delta)}
                </span>{" "}
                requests against expected
              </>
            )}
          </dd>
        </div>

        <div className="measure-item measure-item-minor">
          <dt>Duration</dt>
          <dd className="measure-value">{formatDuration(anomaly.duration_seconds)}</dd>
          <dd className="measure-note">{running ? "Still running" : "Interval length"}</dd>
        </div>
      </dl>

      <dl className="measure-foot measure-foot-waf">
        <div>
          <dt>Detector confidence</dt>
          <dd className="num">{formatMetric(anomaly.confidence)}</dd>
          <dd className="muted">
            {" "}
            (0 to 1)
            {!noneExpected &&
              hasConfidenceLimitation(d?.robust_score_status) &&
              " · limited because baseline variability is zero"}
          </dd>
        </div>
        {/* The new-endpoint detector does not record coverage; a bare dash would read as a figure. */}
        {!noneExpected && (
          <div>
            <dt>Endpoint coverage</dt>
            <dd className="num">{formatPercent(d?.endpoint_coverage_ratio)}</dd>
            <dd className="muted"> of the source&apos;s events carried a parseable endpoint</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
