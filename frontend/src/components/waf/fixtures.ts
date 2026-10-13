// Realistic WAF API shapes for tests, including hostile ones: a long path, a
// missing virtual host, a lower bound, a partial bucket. Shapes mirror
// app/schemas/anomaly.py (T-057); nothing here is invented beyond values.

import type {
  AnomalySummary,
  WafAnomalyDetail,
  WafDetectionDetail,
  WafEndpointInventory,
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
