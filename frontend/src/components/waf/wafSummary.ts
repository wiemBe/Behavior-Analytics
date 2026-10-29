// The one-line WAF incident summary.
//
// Deterministic, and built only from values the API returned: no model, no
// reconstruction of detection logic, no causation. The server-rendered
// `explanation` is shown separately in the detection block; this sentence only
// restates the headline numbers in the right unit for the detector.

import type { WafAnomalyDetail } from "@/lib/api";

import { formatPercent } from "@/lib/format";
import { formatRequests, requestsUnit } from "./wafFormat";
import { ENGLISH_WAF, type BehaviorI18n } from "./wafI18n";
import { endpointLabel, isLowerBound } from "./wafLabels";

export interface WafSummary {
  text: string;
  /** A stated limitation on the figures, or null. */
  caveat: string | null;
}

/**
 * Detector types whose headline figure is a proportion of the endpoint's requests, with the
 * catalog key (`waf.summary.ratio.<key>`) of the sentence that states it.
 */
const RATIO_NOUNS: Record<string, string> = {
  WAF_BLOCK_RATIO_SPIKE: "block",
  WAF_ERROR_RATIO_SPIKE: "serverError",
  WAF_CLIENT_ERROR_RATIO_SPIKE: "clientError",
  WAF_CLIENT_CONCENTRATION_SPIKE: "concentration",
};

export function isRatioType(type: string): boolean {
  return type in RATIO_NOUNS;
}

/**
 * The new-endpoint detector counts requests since the endpoint first appeared and expects none.
 * It is judged by a request-count materiality floor (`threshold`), not by a robust z-score
 * against a seasonal baseline: it has no robust score, no seasonal band and no surprise
 * threshold, and its count is not a per-bucket rate.
 */
export function isNewEndpointType(type: string): boolean {
  return type === "WAF_NEW_ENDPOINT_ACCESS";
}

export function summarizeWafAnomaly(
  anomaly: WafAnomalyDetail,
  bucketSeconds: number | null,
  i18n: BehaviorI18n = ENGLISH_WAF,
): WafSummary {
  const { t, locale } = i18n;
  const d = anomaly.detection;
  const endpoint = endpointLabel(anomaly.waf_endpoint);
  const measurement = d?.request_count_measurement ?? anomaly.observed_measurement ?? null;
  const lower = isLowerBound(measurement);
  const caveat = lower ? t("waf.summary.lowerBound") : null;

  if (isRatioType(anomaly.anomaly_type)) {
    const observed = d?.observed_proportion ?? anomaly.observed_value;
    const expected = d?.expected_proportion ?? anomaly.expected_value;
    const counts =
      d?.observed_numerator != null && d.observed_denominator != null
        ? t("waf.summary.counts", {
            numerator: formatRequests(d.observed_numerator, measurement, locale),
            denominator: formatRequests(d.observed_denominator, null, locale),
          })
        : "";
    return {
      text: t(`waf.summary.ratio.${RATIO_NOUNS[anomaly.anomaly_type]}`, {
        endpoint,
        observed: formatPercent(observed, measurement, 1, locale),
        counts,
        expected: formatPercent(expected, null, 1, locale),
      }),
      caveat,
    };
  }

  const observed = d?.observed_requests ?? anomaly.observed_value;
  const unit = requestsUnit(bucketSeconds, i18n);
  if (isNewEndpointType(anomaly.anomaly_type)) {
    return {
      text: t("waf.summary.newEndpoint", {
        endpoint,
        observed: formatRequests(observed, measurement, locale),
      }),
      caveat,
    };
  }
  if (anomaly.anomaly_type === "WAF_REQUEST_RATE_SPIKE") {
    const expected = d?.expected_requests ?? anomaly.expected_value;
    return {
      text: t("waf.summary.requestRate", {
        endpoint,
        observed: formatRequests(observed, measurement, locale),
        expected: formatRequests(expected, null, locale),
        unit,
      }),
      caveat,
    };
  }
  // A type this build has no phrasing for: the server's own sentence, or the
  // identity alone. Never a guessed figure.
  return {
    text: anomaly.explanation ?? t("waf.summary.flagged", { endpoint, type: anomaly.anomaly_type }),
    caveat,
  };
}
