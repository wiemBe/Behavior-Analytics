// The IPS incident's measurements as one comparison: observed against expected,
// how far apart, for how long.
//
// Same shape as Phase A's `IncidentMetrics` (guidelines §8): four figures, not
// four cards, with the observed value heaviest. The unit is the detector's own:
// events per bucket for the rate and signature detectors, distinct destination
// ports for fan-out, a share for concentration and block ratio. A LOWER_BOUND
// reads "at least n", and a missing value is an em dash with its reason.

import type { IpsAnomalyDetail } from "@/lib/api";
import {
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
  hasConfidenceLimitation,
} from "@/lib/behavior";

import { detectorSpec, expectsNone, ipsFigures } from "./ipsDetectors";
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
  const d = anomaly.detection;
  const spec = detectorSpec(anomaly.anomaly_type);
  const f = ipsFigures(anomaly, d);
  const share = f.unit === "share";
  const lower = d?.observed_measurement === "LOWER_BOUND";
  const m = lower ? "LOWER_BOUND" : null;
  const running = anomaly.anomaly_end == null;
  const unit = unitText(f.unit, bucketWidth(bucketSeconds));
  const noneExpected = expectsNone(anomaly.anomaly_type);
  const band =
    d?.expected_low != null && d.expected_high != null
      ? `${formatFigure(f.unit, d.expected_low)} – ${formatFigure(f.unit, d.expected_high)}`
      : null;
  const deltaUnit = f.unit === "ports" ? "ports" : "events";
  const unitLabel = (
    <span className="measure-unit">{share ? `of ${spec?.of ?? "events"}` : unit}</span>
  );

  return (
    <section className="measure" aria-label="Incident measurements">
      <dl className="measure-grid">
        <div className="measure-item measure-lead">
          <dt>Observed</dt>
          <dd className="measure-value">
            {f.observed == null ? (
              <Missing reason="not recorded for this incident" />
            ) : (
              formatFigure(f.unit, f.observed, m)
            )}
            {unitLabel}
          </dd>
          <dd className="measure-note">
            {lower
              ? "At least this; the true value may be higher"
              : noneExpected
                ? "Cumulative since the port first appeared, not a per-bucket rate"
                : "During the anomalous interval"}
            {share && f.numerator != null && f.denominator != null && (
              <>
                {" · "}
                <span className="num">
                  {formatEvents(f.numerator, m)} of {formatEvents(f.denominator)}
                </span>{" "}
                events
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>Expected</dt>
          <dd className="measure-value">
            {noneExpected ? (
              "none"
            ) : f.expected == null ? (
              <Missing reason="no expected value was recorded" />
            ) : (
              formatFigure(f.unit, f.expected)
            )}
            {!noneExpected && unitLabel}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              "The port has no verified history, so no seasonal baseline applies"
            ) : (
              <>
                Seasonal baseline, this weekday and hour
                {band && (
                  <>
                    {" · range "}
                    <span className="num">{band}</span>
                    {!share && <span className="muted"> {unit}</span>}
                  </>
                )}
              </>
            )}
          </dd>
        </div>

        <div className="measure-item">
          <dt>Deviation</dt>
          <dd className="measure-value">
            {share
              ? formatPointChange(d?.percentage_point_change)
              : formatRatio(anomaly.deviation_ratio)}
          </dd>
          <dd className="measure-note">
            {noneExpected ? (
              "Not defined: nothing was expected"
            ) : share ? (
              <>
                <span className="num">{formatRatio(d?.ratio ?? anomaly.deviation_ratio)}</span>{" "}
                expected
              </>
            ) : (
              <>
                <span className="num">{formatDelta(d?.absolute_delta ?? anomaly.absolute_delta)}</span>{" "}
                {deltaUnit} against expected
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
      </dl>
    </section>
  );
}
