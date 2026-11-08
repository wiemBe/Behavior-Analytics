// Quiet-period rows for the tests, shaped like the backend's `SuppressionOut`.

import type { CallerInfo, Suppression, SuppressionFor } from "@/lib/api";
import type { SessionValue } from "@/components/auth/session";

export const SOURCE = "11111111-1111-1111-1111-111111111111";

export function row(over: Partial<Suppression> = {}): Suppression {
  return {
    id: "0a7daa20-ff91-4d48-8af6-3293bb6c6769",
    family: "log_source",
    subject_id: SOURCE,
    detector_type: "VOLUME_SPIKE",
    kind: "ANALYST",
    reason: "change window for the firewall",
    owner: "k.osei",
    starts_at: "2026-11-06T07:00:00Z",
    ends_at: "2026-11-13T07:00:00Z",
    ended_at: null,
    ended_by: null,
    reference: null,
    created_by: "k.osei",
    created_at: "2026-11-06T07:00:00Z",
    updated_at: "2026-11-06T07:00:00Z",
    subject_name: "edge-01",
    read_only: false,
    hit_count: 12,
    ...over,
  };
}

export function maintenance(over: Partial<Suppression> = {}): Suppression {
  return row({
    id: null,
    detector_type: null,
    kind: "MAINTENANCE",
    reason: "disk replacement",
    owner: "ops",
    starts_at: null,
    ends_at: null,
    created_by: null,
    created_at: null,
    updated_at: null,
    read_only: true,
    hit_count: null,
    ...over,
  });
}

export function forAnomaly(over: Partial<SuppressionFor> = {}): SuppressionFor {
  return {
    family: "log_source",
    anomaly_id: "22222222-2222-2222-2222-222222222222",
    detector_type: "VOLUME_SPIKE",
    suppression: row(),
    hit_count: 12,
    breakthrough_alerts: [],
    ...over,
  };
}

export function session(permissions: string[], status: SessionValue["status"] = "ready"): SessionValue {
  const me = { subject: "k.osei", email: null, permissions, effective_permissions: permissions } as CallerInfo;
  return {
    status,
    me: status === "ready" ? me : null,
    error: null,
    reload: async () => {},
    signOut: async () => null,
  };
}
