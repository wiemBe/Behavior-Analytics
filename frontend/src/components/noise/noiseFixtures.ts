// Shared test data for the tuning page: one source's detail and the preview shapes of SOC-17b.

import type { NoiseCurrent, NoisePreview, NoiseSourceDetail } from "@/lib/api";

export const SRC = "11111111-1111-4111-8111-111111111111";

export function current(over: Partial<NoiseCurrent> = {}): NoiseCurrent {
  return {
    criticality: "HIGH",
    custom_thresholds: { open_after: 4 },
    maintenance_mode: false,
    maintenance_until: null,
    business_hours_only: false,
    expected_interval_seconds: 300,
    effective_thresholds: {
      deviation_z: { value: 3.5, source: "default" },
      spike_ratio: { value: 2, source: "default" },
      drop_ratio: { value: 0.5, source: "default" },
      min_absolute_delta_events: { value: 100, source: "default" },
      open_after: { value: 4, source: "custom" },
      direction: { value: "both", source: "default" },
    },
    lab_mode: false,
    has_by_type: false,
    ...over,
  };
}

export function detail(over: Partial<NoiseSourceDetail> = {}): NoiseSourceDetail {
  return {
    log_source_id: SRC,
    name: "fw-edge-01",
    instance_id: "i-1",
    instance_name: "qradar-prod",
    criticality: "HIGH",
    source_class: "FIREWALL",
    monitoring_enabled: true,
    judgement: "JUDGED",
    judged: true,
    reason: null,
    incidents_7d: 9,
    incidents_28d: 41,
    opened_7d: 4,
    opened_28d: 12,
    alerts_7d: 3,
    alerts_28d: 12,
    noise_closed: 7,
    noise_closed_total: 10,
    noise_share: 0.7,
    worst_ratio: 4.2,
    worst_z: 6.1,
    top_detector: "VOLUME_SPIKE",
    evaluated_through: "2026-11-06T11:55:00Z",
    detectors: [
      {
        anomaly_type: "VOLUME_SPIKE",
        incidents_7d: 6,
        incidents_28d: 30,
        opened_7d: 3,
        opened_28d: 9,
        alerts_7d: 2,
        alerts_28d: 9,
        noise_closed: 6,
        noise_closed_total: 8,
        noise_share: 0.75,
        worst_ratio: 4.2,
        worst_z: 6.1,
      },
      {
        anomaly_type: "VOLUME_DROP",
        incidents_7d: 3,
        incidents_28d: 11,
        opened_7d: 1,
        opened_28d: 3,
        alerts_7d: 1,
        alerts_28d: 3,
        noise_closed: 1,
        noise_closed_total: 2,
        noise_share: null,
        worst_ratio: 0.2,
        worst_z: null,
      },
    ],
    current: current(),
    coverage: { state: "COMPLETE", window_days: 28, covered_days: 28, retention_days: 365, reason: null },
    suppression: null,
    replay: null,
    ...over,
  };
}

const NOT_REPLAYABLE = [
  {
    anomaly_type: "PARSING_DEGRADATION",
    reason: {
      code: "noise.preview.not_replayable",
      params: {},
      text: "Not replayed: the bucket did not measure PARSE_RATIOS",
    },
  },
  {
    anomaly_type: "VOLUME_DROP",
    reason: {
      code: "noise.preview.direction_disabled",
      params: {},
      text: "Not replayed: the current direction disables this detector",
    },
  },
];

export function preview(over: Partial<NoisePreview> = {}): NoisePreview {
  return {
    state: "COMPLETE",
    available: true,
    window: {
      days: 28,
      first_bucket: "2026-10-09T00:00:00Z",
      last_bucket: "2026-11-05T23:00:00Z",
      covered_days: 28,
    },
    baseline: { computed_at: "2026-11-06T06:00:00Z", baseline_version: 42, lookback_days: 28 },
    current: {
      total_incidents: 39,
      by_detector: [
        { anomaly_type: "NO_EVENTS", incidents: 0, excluded_by_direction: false },
        { anomaly_type: "VOLUME_DROP", incidents: 9, excluded_by_direction: false },
        { anomaly_type: "VOLUME_SPIKE", incidents: 30, excluded_by_direction: false },
      ],
    },
    proposed: {
      total_incidents: 5,
      by_detector: [
        { anomaly_type: "NO_EVENTS", incidents: 0, excluded_by_direction: false },
        { anomaly_type: "VOLUME_DROP", incidents: null, excluded_by_direction: true },
        { anomaly_type: "VOLUME_SPIKE", incidents: 5, excluded_by_direction: false },
      ],
    },
    recorded: { incidents: 41, opened: 12, suppressed: 0 },
    not_replayable: NOT_REPLAYABLE,
    not_judged_buckets: 0,
    maintenance_buckets: 0,
    warnings: [],
    reason: null,
    ...over,
  };
}

export const PARTIAL_PREVIEW = preview({
  state: "PARTIAL",
  window: {
    days: 28,
    first_bucket: "2026-10-23T00:00:00Z",
    last_bucket: "2026-11-05T23:00:00Z",
    covered_days: 14,
  },
  reason: { code: "noise.preview.partial", params: {}, text: "Counts cover 14 of 28 days." },
});

export const INSUFFICIENT_PREVIEW = preview({
  state: "INSUFFICIENT_DATA",
  available: false,
  window: {
    days: 28,
    first_bucket: "2026-11-03T00:00:00Z",
    last_bucket: "2026-11-05T23:00:00Z",
    covered_days: 3,
  },
  baseline: { computed_at: null, baseline_version: null, lookback_days: 28 },
  current: null,
  proposed: null,
  recorded: { incidents: 5, opened: 2, suppressed: 0 },
  not_replayable: [],
  not_judged_buckets: null,
  maintenance_buckets: null,
  reason: {
    code: "noise.preview.insufficient_coverage",
    params: {},
    text: "Only 3 of 28 days are stored; a replay needs at least 7.",
  },
});

export const TOO_LARGE_PREVIEW = preview({
  state: "TOO_LARGE",
  available: false,
  current: null,
  proposed: null,
  recorded: null,
  not_replayable: [],
  not_judged_buckets: null,
  maintenance_buckets: null,
  reason: {
    code: "noise.preview.too_large",
    params: {},
    text: "The window holds 60,480 buckets; the replay stops at 50,000.",
  },
});
