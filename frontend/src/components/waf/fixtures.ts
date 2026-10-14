// Realistic WAF API shapes for tests, including hostile ones: a long path, a
// missing virtual host, a lower bound, a partial bucket. Shapes mirror
// app/schemas/anomaly.py (T-057); nothing here is invented beyond values.

import type {
  AnomalySummary,
  WafAnomalyDetail,
  WafDetectionDetail,
  WafEndpointInventory,
  WafBaselineCell,
  WafEndpointRef,
  WafMetricBucket,
  WafSourceBehavior,
} from "@/lib/api";

export const WAF_SOURCE_ID = "5a1f0000-0000-4000-8000-000000000312";
export const WAF_ENDPOINT_ID = "e0000000-0000-4000-8000-00000000000a";

export function endpointRef(over: Partial<WafEndpointRef> = {}): WafEndpointRef {
  return {
    id: WAF_ENDPOINT_ID,
    normalized_endpoint: "/portal/login",
    http_method: "GET",
    virtual_host: "portal.lab.example",
    virtual_host_available: true,
    normalizer_version: 2,
    ...over,
  };
}

export function wafListItem(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "w-1",
    subject_kind: "WAF_ENDPOINT",
    log_source_id: WAF_SOURCE_ID,
    log_source_name: "lab-waf-behavior-01",
    waf_endpoint: endpointRef(),
    anomaly_type: "WAF_REQUEST_RATE_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 412,
    expected_value: 41.5,
    deviation_ratio: 9.93,
    robust_z: 21.4,
    absolute_delta: 370.5,
    consecutive_buckets: 3,
    confidence: 0.91,
    detected_at: "2026-10-12T09:05:00Z",
    opened_at: "2026-10-12T09:15:00Z",
    anomaly_start: "2026-10-12T09:00:00Z",
    anomaly_end: "2026-10-12T09:30:00Z",
    resolved_at: null,
    duration_seconds: 1800,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    observed_measurement: "EXACT",
    explanation: null,
    ...over,
  };
}

export function wafDetection(over: Partial<WafDetectionDetail> = {}): WafDetectionDetail {
  return {
    reason: "412 requests in the 09:00 bucket against a baseline median of 41.5",
    expected_low: 22,
    expected_high: 68,
    threshold: 3.5,
    baseline_sample_count: 31,
    baseline_completeness: 0.94,
    baseline_version: 6,
    normalizer_version: 2,
    observed_requests: 412,
    expected_requests: 41.5,
    absolute_delta_requests: 370.5,
    endpoint_coverage_ratio: 0.972,
    request_count_measurement: "EXACT",
    observed_proportion: null,
    expected_proportion: null,
    observed_numerator: null,
    observed_denominator: null,
    percentage_point_change: null,
    ratio: 9.93,
    ratio_basis: null,
    robust_score_status: "OK",
    robust_z: 21.4,
    fallback_bound: null,
    ...over,
  };
}

/**
 * The new-endpoint detector's real payload (`detect_new_endpoint_access`): a cumulative count
 * on the row, `expected_value` 0.0, `threshold` the request-count materiality floor,
 * `sample_count` the verified history buckets, and no band, robust score, baseline completeness
 * or version. The projection does not read its nested `observed` block, so the request fields
 * are null and the row carries the count.
 */
export function wafNewEndpointDetection(over: Partial<WafDetectionDetail> = {}): WafDetectionDetail {
  return {
    reason:
      "GET /portal/login received 320 requests across 4 buckets since it first appeared at " +
      "2026-10-12T08:40:00+00:00; in the verified history it was never requested 20 or more " +
      "times in one bucket",
    expected_low: null,
    expected_high: null,
    threshold: 50,
    baseline_sample_count: 288,
    baseline_completeness: null,
    baseline_version: null,
    normalizer_version: 2,
    observed_requests: null,
    expected_requests: null,
    absolute_delta_requests: null,
    endpoint_coverage_ratio: null,
    request_count_measurement: null,
    observed_proportion: null,
    expected_proportion: null,
    observed_numerator: null,
    observed_denominator: null,
    percentage_point_change: null,
    ratio: null,
    ratio_basis: null,
    robust_score_status: null,
    robust_z: null,
    fallback_bound: null,
    ...over,
  };
}

/** A ratio detector's real payload: proportions and counts, a pooled expected share, a band. */
export function wafRatioDetection(over: Partial<WafDetectionDetail> = {}): WafDetectionDetail {
  return wafDetection({
    reason:
      "318 of 1000 requests carrying a WAF action to GET /portal/login were blocked requests " +
      "(31.8%), against an expected 2.1% (+29.7 pp, 15.1x); robust z=12.40 (threshold +3.5)",
    expected_low: 0.004,
    expected_high: 0.06,
    observed_requests: null,
    expected_requests: null,
    absolute_delta_requests: null,
    endpoint_coverage_ratio: null,
    request_count_measurement: null,
    observed_proportion: 0.318,
    expected_proportion: 0.021,
    observed_numerator: 318,
    observed_denominator: 1000,
    percentage_point_change: 29.7,
    ratio: 15.1429,
    robust_z: 12.4,
    ...over,
  });
}

export function wafDetail(over: Partial<WafAnomalyDetail> = {}): WafAnomalyDetail {
  const { id, ...rest } = wafListItem();
  return {
    ...rest,
    id,
    subject_kind: "WAF_ENDPOINT",
    waf_endpoint: endpointRef(),
    baseline_version: 6,
    policy_version: 1,
    transitions: [
      {
        from_state: "NORMAL",
        to_state: "CANDIDATE",
        occurred_at: "2026-10-12T09:05:00Z",
        bucket_start: "2026-10-12T09:00:00Z",
        reason: "first abnormal bucket",
        actor: "detector",
        observed_value: 412,
        expected_value: 41.5,
      },
      {
        from_state: "CANDIDATE",
        to_state: "OPEN",
        occurred_at: "2026-10-12T09:15:00Z",
        bucket_start: "2026-10-12T09:10:00Z",
        reason: "3 consecutive abnormal buckets",
        actor: "detector",
        observed_value: 398,
        expected_value: 41.5,
      },
    ],
    explanation_package: null,
    detection: wafDetection(),
    ...over,
  };
}

export function wafBucket(over: Partial<WafMetricBucket> = {}): WafMetricBucket {
  return {
    bucket_start: "2026-10-12T09:00:00Z",
    bucket_seconds: 300,
    request_count: 41,
    request_count_measurement: "EXACT",
    completeness: "COMPLETE",
    partial: false,
    endpoint_coverage_ratio: 0.97,
    in_maintenance: false,
    normalizer_version: 2,
    distinct_client_count: 14,
    action_measurement: "EXACT",
    response_code_measurement: "EXACT",
    client_measurement: "EXACT",
    blocked_count: 2,
    action_events: 40,
    response_code_events: 40,
    client_error_count: 3,
    server_error_count: 1,
    client_events: 41,
    top1_client_requests: 9,
    ...over,
  };
}

/** A seasonal cell. ISO weekday 1-7 (Monday = 1), UTC hour. */
export function wafBaselineCell(over: Partial<WafBaselineCell> = {}): WafBaselineCell {
  return {
    metric_name: "request_count",
    weekday: 1,
    hour: 9,
    median: 41.5,
    mad: 6,
    p05: 22,
    p95: 68,
    sample_count: 31,
    is_reliable: true,
    completeness: 0.94,
    baseline_version: 6,
    normalizer_version: 2,
    excluded_sample_count: 3,
    metric_semantics: null,
    ...over,
  };
}

export function wafSource(over: Partial<WafSourceBehavior> = {}): WafSourceBehavior {
  return {
    log_source_id: WAF_SOURCE_ID,
    log_source_name: "lab-waf-behavior-01",
    product_label: "Imperva WAF",
    enabled: true,
    profile_version: 3,
    validation_status: "VALID",
    validated_profile_version: 3,
    validated_at: "2026-10-10T08:00:00Z",
    validation_current: true,
    virtual_host_available: true,
    latest_bucket: {
      bucket_start: "2026-10-13T08:55:00Z",
      bucket_seconds: 300,
      completeness: "COMPLETE",
      endpoint_coverage_ratio: 0.972,
      coverage_measurement: "EXACT",
      request_count_measurement: "EXACT",
      total_events: 5184,
      in_maintenance: false,
      groups_truncated: false,
      admission_truncated: false,
    },
    active_endpoints: 132,
    total_endpoints: 148,
    active_endpoint_ceiling: 500,
    admission: {
      buckets_in_range: 288,
      buckets_admission_truncated: 0,
      rejected_over_quota: 0,
      below_admission_threshold: 41,
      untracked_requests: 312,
    },
    ...over,
  };
}

export function wafEndpoint(over: Partial<WafEndpointInventory> = {}): WafEndpointInventory {
  return {
    id: WAF_ENDPOINT_ID,
    normalized_endpoint: "/portal/login",
    http_method: "GET",
    virtual_host: "portal.lab.example",
    normalizer_version: 2,
    first_seen_at: "2026-09-02T10:00:00Z",
    last_seen_at: "2026-10-13T08:55:00Z",
    is_active: true,
    retired_at: null,
    tracked_since: "2026-09-02T10:00:00Z",
    baseline: { cells_total: 168, reliable_cells: 131 },
    baseline_rebuilt_at: "2026-10-13T03:00:00Z",
    state: "NORMAL",
    active_anomaly_count: 0,
    ...over,
  };
}
