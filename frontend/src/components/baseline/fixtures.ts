// Training-exclusion rows, anomalies and sessions for the tests of SOC-13d.

import type { SessionValue } from "@/components/auth/session";
import type { AnomalyDetail, CallerInfo, TrainingExclusion } from "@/lib/api";

export const SOURCE = "11111111-1111-1111-1111-111111111111";
export const ANOMALY = "22222222-2222-2222-2222-222222222222";

export function exclusion(over: Partial<TrainingExclusion> = {}): TrainingExclusion {
  return {
    id: "33333333-3333-3333-3333-333333333333",
    origin_family: "log_source",
    origin_anomaly_id: ANOMALY,
    subject_kind: "log_source",
    subject_id: SOURCE,
    scope_key: "log_source:volume",
    starts_at: "2026-11-04T08:00:00Z",
    ends_at: "2026-11-04T10:00:00Z",
    reason: "Collector restarted during the incident",
    created_by: "k.osei",
    created_at: "2026-11-04T10:30:00Z",
    revoked_by: null,
    revoked_at: null,
    revocation_reason: null,
    state: "PENDING_REBUILD",
    ...over,
  };
}

/** A resolved Phase A incident: detected 08:07, resolved 09:52; the bucket is 5 minutes, so the
 *  default interval is 08:05 to 09:55 UTC. */
export function resolved(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
  return {
    id: ANOMALY,
    log_source_id: SOURCE,
    log_source_name: "LAB Firewall",
    anomaly_type: "VOLUME_SPIKE",
    state: "RESOLVED",
    severity: "HIGH",
    observed_value: 6,
    expected_value: 2,
    deviation_ratio: 3,
    robust_z: 13.5,
    absolute_delta: 1200,
    consecutive_buckets: 2,
    confidence: 0.82,
    detected_at: "2026-11-04T08:07:00Z",
    opened_at: "2026-11-04T08:10:00Z",
    anomaly_start: "2026-11-04T08:07:00Z",
    anomaly_end: "2026-11-04T09:45:00Z",
    resolved_at: "2026-11-04T09:52:00Z",
    duration_seconds: 5100,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: null,
    baseline_version: 1,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: null,
    ...over,
  } as AnomalyDetail;
}

export function session(permissions: string[], status: SessionValue["status"] = "ready"): SessionValue {
  const me: CallerInfo = {
    subject: "k.osei",
    email: null,
    permissions: [],
    effective_permissions: permissions,
  } as CallerInfo;
  return {
    status,
    me: status === "ready" ? me : null,
    error: null,
    reload: async () => {},
    signOut: async () => null,
  };
}

export const CAN = session(["read:anomalies", "anomaly:triage"]);
export const READ_ONLY = session(["read:anomalies"]);
