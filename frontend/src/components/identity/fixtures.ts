// Deterministic identity data for tests. Usernames are invented; no value is a real account.

import type {
  AnomalySummary,
  IdentityAnomalyDetail,
  IdentityProfile,
  IdentitySeriesPoint,
  IdentitySource,
} from "@/lib/api";

export const SOURCE_ID = "11111111-1111-4111-8111-111111111111";
export const OTHER_ID = "22222222-2222-4222-8222-222222222222";

export function profile(over: Partial<IdentityProfile> = {}): IdentityProfile {
  return {
    id: "p-1",
    log_source_id: SOURCE_ID,
    instance_id: "i-1",
    qradar_log_source_id: 101,
    log_source_name: "dc-01.example.test",
    enabled: true,
    profile_version: 2,
    product_label: "Windows Security",
    event_id_property: "WinEventID",
    username_property: "username",
    logon_type_property: null,
    enabled_event_ids: [4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102],
    validation_status: "VALID",
    validated_profile_version: 2,
    validated_at: "2026-11-13T08:00:00Z",
    validation_report: {},
    ...over,
  };
}

export function source(over: Partial<IdentitySource> = {}): IdentitySource {
  return {
    profile: profile(),
    collection: {
      watermark_at: "2026-11-13T09:55:00Z",
      last_run_at: "2026-11-13T10:00:00Z",
      lag_seconds: 300,
      consecutive_failures: 0,
      last_error: null,
    },
    last_bucket: {
      bucket_start: "2026-11-13T09:55:00Z",
      bucket_seconds: 300,
      measurement: "EXACT",
      population: 1200,
      admission_truncated: false,
    },
    ...over,
  };
}

export function point(over: Partial<IdentitySeriesPoint> = {}): IdentitySeriesPoint {
  return {
    event_id: 4625,
    bucket_start: "2026-11-13T09:55:00Z",
    event_count: 12,
    measurement: "EXACT",
    names_measurement: "EXACT",
    admission_truncated: false,
    usernames: [],
    ...over,
  };
}

export function summary(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a-1",
    log_source_id: SOURCE_ID,
    log_source_name: "dc-01.example.test",
    anomaly_type: "IDENTITY_FAILED_LOGON_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 180,
    expected_value: 20,
    deviation_ratio: 9,
    robust_z: null,
    absolute_delta: 160,
    consecutive_buckets: 2,
    confidence: null,
    detected_at: "2026-11-13T09:50:00Z",
    opened_at: "2026-11-13T09:50:00Z",
    anomaly_start: "2026-11-13T09:45:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    subject_kind: "IDENTITY_SOURCE",
    identity: { event_id: 4625, usernames: [] },
    observed_measurement: "EXACT",
    ...over,
  };
}

export function incident(over: Partial<IdentityAnomalyDetail> = {}): IdentityAnomalyDetail {
  return {
    ...summary(),
    subject_kind: "IDENTITY_SOURCE",
    baseline_version: null,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    identity: {
      event_id: 4625,
      usernames: [
        { rank: 1, username: "svc-backup", count: 90 },
        { rank: 2, username: "CORP\\alice", count: 40 },
      ],
    },
    detection: { event_id: 4625, measurement: "EXACT", ratio: 9, robust_z: null, score_status: "OK" },
    ...over,
  } as IdentityAnomalyDetail;
}
