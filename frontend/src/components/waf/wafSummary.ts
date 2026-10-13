// The one-line WAF incident summary.
//
// Deterministic, and built only from values the API returned: no model, no
// reconstruction of detection logic, no causation. The server-rendered
// `explanation` is shown separately in the detection block; this sentence only
// restates the headline numbers in the right unit for the detector.

import type { WafAnomalyDetail } from "@/lib/api";

import { formatPercent, formatRequests, requestsUnit } from "./wafFormat";
import { endpointLabel, isLowerBound } from "./wafLabels";

export interface WafSummary {
  text: string;
  /** A stated limitation on the figures, or null. */
  caveat: string | null;
}

/** Detector types whose headline figure is a proportion of the endpoint's requests. */
const RATIO_NOUNS: Record<string, string> = {
  WAF_BLOCK_RATIO_SPIKE: "The blocked share of requests",
  WAF_ERROR_RATIO_SPIKE: "The HTTP 5xx share of responses",
  WAF_CLIENT_ERROR_RATIO_SPIKE: "The HTTP 4xx share of responses",
  WAF_CLIENT_CONCENTRATION_SPIKE: "The largest source's share of requests",
};

export function isRatioType(type: string): boolean {
  return type in RATIO_NOUNS;
}

export function summarizeWafAnomaly(
  anomaly: WafAnomalyDetail,
  bucketSeconds: number | null,
): WafSummary {
  const d = anomaly.detection;
  const subject = endpointLabel(anomaly.waf_endpoint);
  const measurement = d?.request_count_measurement ?? anomaly.observed_measurement ?? null;
  const lower = isLowerBound(measurement);
  const caveat = lower
    ? "The observed figure is a lower bound: the true value is at least this."
    : null;

  if (isRatioType(anomaly.anomaly_type)) {
    const observed = d?.observed_proportion ?? anomaly.observed_value;
    const expected = d?.expected_proportion ?? anomaly.expected_value;
    const counts =
      d?.observed_numerator != null && d.observed_denominator != null
        ? ` (${formatRequests(d.observed_numerator, measurement)} of ${formatRequests(d.observed_denominator)} requests)`
        : "";
    return {
      text:
        `${RATIO_NOUNS[anomaly.anomaly_type]} for ${subject} was ` +
        `${formatPercent(observed, measurement)}${counts}, against an expected ` +
        `${formatPercent(expected)}.`,
      caveat,
    };
  }

  const observed = d?.observed_requests ?? anomaly.observed_value;
  const unit = requestsUnit(bucketSeconds);
  if (anomaly.anomaly_type === "WAF_NEW_ENDPOINT_ACCESS") {
    return {
      text:
        `${subject} received ${formatRequests(observed, measurement)} ${unit} and is absent ` +
        `from the verified request history.`,
      caveat,
    };
  }
  if (anomaly.anomaly_type === "WAF_REQUEST_RATE_SPIKE") {
    const expected = d?.expected_requests ?? anomaly.expected_value;
    return {
      text:
        `Requests to ${subject} reached ${formatRequests(observed, measurement)} ${unit}, ` +
        `against an expected ${formatRequests(expected)} ${unit}.`,
      caveat,
    };
  }
  // A type this build has no phrasing for: the server's own sentence, or the
  // identity alone. Never a guessed figure.
  return { text: anomaly.explanation ?? `${subject} was flagged by ${anomaly.anomaly_type}.`, caveat };
}
