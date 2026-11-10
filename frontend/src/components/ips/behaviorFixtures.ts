// Realistic IPS behavior list/inventory shapes for tests, mirroring app/schemas/ips_behavior.py
// (T-085). Nothing here is invented beyond values.

import type {
  IpsAdmission,
  IpsDimensionCoverage,
  IpsDimensionName,
  IpsEntityInventory,
  IpsEntityPressure,
  IpsLatestBucket,
  IpsSourceBehavior,
} from "@/lib/api";

export const IPS_BEHAVIOR_SOURCE_ID = "1b5c0000-0000-4000-8000-000000000501";

export function dimension(
  name: IpsDimensionName,
  measurement = "EXACT",
): IpsDimensionCoverage {
  return {
    dimension: name,
    measurement,
    coverage_ratio: measurement === "EXACT" || measurement === "LOWER_BOUND" ? 0.97 : null,
    coverage_lower_bound: measurement === "LOWER_BOUND" ? true : measurement === "EXACT" ? false : null,
  };
}

export function admission(name: IpsDimensionName, over: Partial<IpsAdmission> = {}): IpsAdmission {
  return {
    dimension: name,
    admitted: 40,
    below_admission_threshold: 12,
    rejected_over_quota: 0,
    untracked_entities: 0,
    untracked_events: 0,
    admission_truncated: false,
    ...over,
  };
}

export function latestBucket(over: Partial<IpsLatestBucket> = {}): IpsLatestBucket {
  return {
    bucket_start: "2026-10-13T08:55:00Z",
    bucket_seconds: 300,
    total_events: 5184,
    total_records: 1022,
    in_maintenance: false,
    dimensions: [
      dimension("DESTINATION_PORT"),
      dimension("DESTINATION_IP"),
      dimension("SIGNATURE"),
      dimension("SOURCE_IP"),
      dimension("PORT_FANOUT"),
      dimension("ACTION"),
    ],
    admission: [
      admission("DESTINATION_PORT"),
      admission("DESTINATION_IP"),
      admission("SIGNATURE"),
    ],
    ...over,
  };
}

export function pressure(name: IpsDimensionName, active = 40, total = 52): IpsEntityPressure {
  return { dimension: name, active, total, active_ceiling: 500 };
}

export function ipsSource(over: Partial<IpsSourceBehavior> = {}): IpsSourceBehavior {
  return {
    log_source_id: IPS_BEHAVIOR_SOURCE_ID,
    log_source_name: "lab-ips-01",
    product_label: "Cisco Firepower Threat Defense",
    enabled: true,
    profile_version: 3,
    validation_status: "VALID",
    validated_profile_version: 3,
    validated_at: "2026-10-10T08:00:00Z",
    validation_current: true,
    active_anomaly_count: 0,
    worst_active_severity: null,
    latest_bucket: latestBucket(),
    entities: [
      pressure("DESTINATION_PORT", 40, 52),
      pressure("DESTINATION_IP", 130, 148),
      pressure("SIGNATURE", 61, 70),
    ],
    ...over,
  };
}

export function ipsEntity(over: Partial<IpsEntityInventory> = {}): IpsEntityInventory {
  return {
    id: "e1500000-0000-4000-8000-00000000000b",
    dimension: "DESTINATION_PORT",
    value_key: "445/tcp",
    display_name: null,
    first_seen_at: "2026-10-01T10:00:00Z",
    last_seen_at: "2026-10-13T08:55:00Z",
    active: true,
    retired_at: null,
    baseline: { cells_total: 168, reliable_cells: 131 },
    state: "NORMAL",
    active_anomaly_count: 0,
    ...over,
  };
}
