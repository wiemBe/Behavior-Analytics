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

/** A row the engine suppressed in a maintenance window: the pill, and the stored reason
 *  with its code beside it (POL-2). The IPS engine stores the WAF sentence. */
export function ipsSuppressedDetail(over: Partial<IpsAnomalyDetail> = {}): IpsAnomalyDetail {
  return ipsDetail({
    suppressed: true,
    suppression_reason: "maintenance window",
    suppression_reason_code: "waf.suppression.maintenance_window",
    suppression_reason_params: {},
    ...over,
  });
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

// ---- The same payloads as a current backend writes them: each sentence beside its code (MSG-6b).
// The plain fixtures above stay as a row stored before codes serves them (English text, no code),
// so the tests that pin that contract keep their meaning; these carry the code and raw values.

/** The wire form of a share and of a change in points, as the API sends them beside a sentence. */
const percentParam = (ratio: number, precision = 1) => ({ kind: "percent", ratio, precision });
const pointsParam = (points: number, precision = 1) => ({
  kind: "points",
  points,
  precision,
  signed: true,
});
const Z = { code: "waf.basis.z", params: { score: 38.26, threshold: 3.5 } };
const PORT_445 = {
  code: "ips.subject.named",
  params: { kind: { code: "ips.label.destination_port", params: {} }, name: "445/tcp" },
};

/** The detector's caveat of an entity-rate spike, stored beside its code. */
const PORT_RATE_CAVEAT =
  "More events on a port describe traffic volume, not intent: a scan, a deployment and a " +
  "misconfigured client look alike here.";

/**
 * A port spike whose count is a lower bound and reaches 1,000: the lower-bound wrap names its
 * scope through the input's role, and the count prints grouped.
 */
export function ipsCodedDetection(over: Partial<IpsDetectionDetail> = {}): IpsDetectionDetail {
  return ipsDetection({
    reason:
      "at least 12,345 events (a lower bound: volume: the count is a lower bound: an increase " +
      "can be proven, normal behaviour cannot) for destination port 445/tcp is above the " +
      "baseline median 1000.0 (12.35x expected, +11,345 events/bucket); robust z=38.26 " +
      "(threshold +3.5)",
    reason_code: "ips.spike.fired_lower",
    reason_params: {
      observed: 12345,
      unit: { code: "ips.noun.events", params: {} },
      scope: {
        code: "ips.scope.role",
        params: {
          role: { code: "ips.role.volume", params: {} },
          reason: { code: "waf.scope.lower_bound_increase", params: {} },
        },
      },
      subject: PORT_445,
      expected: 1000,
      ratio_text: { code: "waf.rate.ratio_multiple", params: { ratio: 12.345 } },
      delta: 11345,
      basis: Z,
    },
    observed_events: 12345,
    expected_events: 1000,
    expected_low: 800,
    expected_high: 1500,
    observed_measurement: "LOWER_BOUND",
    absolute_delta: 11345,
    ratio: 12.345,
    robust_z: 38.26,
    caveat: PORT_RATE_CAVEAT,
    caveat_code: "ips.caveat.port_rate",
    caveat_params: {},
    ...over,
  });
}

/** A block-ratio shift: a share, points, a basis and the block-ratio caveat. */
export function ipsCodedBlockDetection(
  over: Partial<IpsDetectionDetail> = {},
): IpsDetectionDetail {
  return ipsDetection({
    reason:
      "60 of 100 events carrying an action were blocked (60.0 %), against an expected " +
      "10.0 % from 100 historical events (+50.0 pp, 6.0x); robust z=16.86 " +
      "(threshold +3.5)",
    reason_code: "ips.block.fired",
    reason_params: {
      count: 60,
      total: 100,
      share: percentParam(0.6),
      expected: percentParam(0.1),
      history: 100,
      rise: pointsParam(50),
      multiple: 6,
      basis: { code: "waf.basis.z", params: { score: 16.86, threshold: 3.5 } },
    },
    expected_low: 0.05,
    expected_high: 0.15,
    observed_events: null,
    expected_events: null,
    observed_proportion: 0.6,
    expected_proportion: 0.1,
    observed_numerator: 60,
    observed_denominator: 100,
    percentage_point_change: 50,
    absolute_delta: 0.5,
    ratio: 6,
    robust_z: 16.86,
    caveat:
      "Blocking is judged through the profile's action map. A higher block ratio describes the " +
      "IPS's decisions; it does not establish that an attack succeeded.",
    caveat_code: "ips.caveat.block_ratio",
    caveat_params: {},
    ...over,
  });
}

/** A new-port incident: its three timestamps travel as ISO text. */
export function ipsCodedNewPortDetection(
  over: Partial<IpsDetectionDetail> = {},
): IpsDetectionDetail {
  return ipsDetection({
    reason:
      "destination port 445/tcp carried 3250 events across 4 buckets since it first appeared " +
      "at 2026-10-12T08:40:00+00:00; in the verified history 2026-10-05T08:40:00+00:00 to " +
      "2026-10-12T08:40:00+00:00 it stayed below 3 events in every bucket",
    reason_code: "ips.novelty.fired",
    reason_params: {
      port: "445/tcp",
      events: 3250,
      buckets: 4,
      first_seen: "2026-10-12T08:40:00+00:00",
      history_start: "2026-10-05T08:40:00+00:00",
      history_end: "2026-10-12T08:40:00+00:00",
      admission: 3,
    },
    expected_low: null,
    expected_high: null,
    threshold: 20,
    baseline_sample_count: 168,
    baseline_completeness: null,
    baseline_version: null,
    observed_events: 3250,
    expected_events: null,
    caveat:
      "A newly busy destination port may be a deployment as easily as a probe. The " +
      "contributors are the bucket's busiest source IPs, not necessarily this port's.",
    caveat_code: "ips.caveat.new_port",
    caveat_params: {},
    robust_score_status: null,
    robust_z: null,
    ratio: null,
    ...over,
  });
}
