// Realistic IPS API shapes for tests, mirroring app/schemas/anomaly.py and
// app/schemas/ips_behavior.py (T-085). Nothing here is invented beyond values.

import type {
  AnomalySummary,
  IpsAnomalyDetail,
  IpsBaselineCell,
  IpsDetectionDetail,
  IpsEntityMetricBucket,
  IpsEntityRef,
  IpsSourceMetricBucket,
} from "@/lib/api";

export const IPS_SOURCE_ID = "1b5c0000-0000-4000-8000-000000000501";
export const IPS_ENTITY_ID = "e1500000-0000-4000-8000-00000000000b";

export function portEntity(over: Partial<IpsEntityRef> = {}): IpsEntityRef {
  return {
    id: IPS_ENTITY_ID,
    dimension: "DESTINATION_PORT",
    value_key: "445/tcp",
    display_name: null,
    ...over,
  };
}

export function ipsListItem(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "i-1",
    subject_kind: "IPS_ENTITY",
    log_source_id: IPS_SOURCE_ID,
    log_source_name: "lab-ips-01",
    ips_entity: portEntity(),
    anomaly_type: "IPS_PORT_RATE_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 940,
    expected_value: 120,
    deviation_ratio: 7.83,
    robust_z: 18.2,
    absolute_delta: 820,
    consecutive_buckets: 3,
    confidence: 0.9,
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

export function ipsDetection(over: Partial<IpsDetectionDetail> = {}): IpsDetectionDetail {
  return {
    reason: "940 events in the 09:00 bucket against a seasonal median of 120",
    expected_low: 70,
    expected_high: 210,
    threshold: 3.5,
    baseline_sample_count: 28,
    baseline_completeness: 0.92,
    baseline_version: 4,
    observed_events: 940,
    expected_events: 120,
    observed_distinct_ports: null,
    expected_distinct_ports: null,
    observed_proportion: null,
    expected_proportion: null,
    observed_numerator: null,
    observed_denominator: null,
    percentage_point_change: null,
    observed_measurement: "EXACT",
    absolute_delta: 820,
    caveat: "Volume, not intent.",
    ratio: 7.83,
    ratio_basis: null,
    robust_score_status: "OK",
    robust_z: 18.2,
    fallback_bound: null,
    ...over,
  };
}

export function ipsDetail(over: Partial<IpsAnomalyDetail> = {}): IpsAnomalyDetail {
  const item = ipsListItem();
  return {
    ...item,
    subject_kind: "IPS_ENTITY",
    ips_entity: portEntity(),
    baseline_version: 4,
    policy_version: 1,
    transitions: [
      {
        from_state: "NORMAL",
        to_state: "CANDIDATE",
        occurred_at: "2026-10-12T09:05:00Z",
        bucket_start: "2026-10-12T09:00:00Z",
        reason: "first abnormal bucket",
        actor: "detector",
        observed_value: 940,
        expected_value: 120,
      },
    ],
    explanation_package: null,
    detection: ipsDetection(),
    ...over,
  };
}

/** A source-level incident: concentration by default (a share, with its counts). */
export function ipsSourceDetail(over: Partial<IpsAnomalyDetail> = {}): IpsAnomalyDetail {
  return ipsDetail({
    subject_kind: "IPS_SOURCE",
    ips_entity: null,
    anomaly_type: "IPS_SOURCE_CONCENTRATION_SPIKE",
    observed_value: 0.62,
    expected_value: 0.18,
    detection: ipsDetection({
      reason: "One source IP carried 62% of source-IP events",
      expected_low: 0.1,
      expected_high: 0.27,
      observed_events: null,
      expected_events: null,
      observed_proportion: 0.62,
      expected_proportion: 0.18,
      observed_numerator: 620,
      observed_denominator: 1000,
      percentage_point_change: 44.0,
    }),
    ...over,
  });
}

export function ipsEntityBucket(over: Partial<IpsEntityMetricBucket> = {}): IpsEntityMetricBucket {
  return {
    bucket_start: "2026-10-12T09:00:00Z",
    bucket_seconds: 300,
    events: 120,
    measurement: "EXACT",
    coverage_ratio: 0.98,
    coverage_lower_bound: false,
    admission_truncated: false,
    in_maintenance: false,
    ...over,
  };
}

export function ipsSourceBucket(over: Partial<IpsSourceMetricBucket> = {}): IpsSourceMetricBucket {
  return {
    bucket_start: "2026-10-12T09:00:00Z",
    bucket_seconds: 300,
    total_events: 1500,
    total_records: 300,
    in_maintenance: false,
    dimensions: [
      { dimension: "SOURCE_IP", measurement: "EXACT", coverage_ratio: 1, coverage_lower_bound: false },
      { dimension: "PORT_FANOUT", measurement: "EXACT", coverage_ratio: 1, coverage_lower_bound: false },
      { dimension: "ACTION", measurement: "EXACT", coverage_ratio: 1, coverage_lower_bound: false },
    ],
    source_events: 1000,
    distinct_sources: 40,
    distinct_sources_lower_bound: false,
    top1_events: 200,
    top5_events: 450,
    max_distinct_ports: 12,
    blocked_events: 300,
    allowed_events: 600,
    unmapped_events: 100,
    ...over,
  };
}

/** A seasonal cell. ISO weekday 1-7 (Monday = 1), UTC hour. 2026-10-12 is a Monday. */
export function ipsCell(over: Partial<IpsBaselineCell> = {}): IpsBaselineCell {
  return {
    metric_name: "entity_events",
    weekday: 1,
    hour: 9,
    median: 120,
    mad: 20,
    p05: 70,
    p95: 210,
    numerator_sum: null,
    denominator_sum: null,
    sample_count: 28,
    is_reliable: true,
    completeness: 0.92,
    baseline_version: 4,
    profile_version: 2,
    normalizer_version: 1,
    excluded_sample_count: 1,
    metric_semantics: null,
    window_start: null,
    window_end: null,
    computed_at: null,
    ...over,
  };
}
