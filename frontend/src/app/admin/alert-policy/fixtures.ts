// Test data for the alert policy page: rows in the API's shape and the digest statuses.

import type { AlertPolicyRow, DigestRun, DigestStatus, FleetSource, PropertyWatch } from "@/lib/api";

export const INST_A = "11111111-1111-4111-8111-111111111111";
export const SRC_FW = "33333333-3333-4333-8333-333333333333";
export const SRC_WAF = "44444444-4444-4444-8444-444444444444";
export const WATCH = "55555555-5555-4555-8555-555555555555";

let n = 0;

export function policy(over: Partial<AlertPolicyRow> = {}): AlertPolicyRow {
  n += 1;
  return {
    id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    instance_id: null,
    scope_kind: "CLASS",
    scope_value: "FIREWALL",
    family: "log_source",
    detector_type: "VOLUME_SPIKE",
    mode: "RECORD_ONLY",
    updated_by: "admin",
    created_at: "2026-11-06T07:10:00Z",
    updated_at: "2026-11-06T07:10:00Z",
    instance_name: null,
    source_name: null,
    source_class: "FIREWALL",
    ...over,
  };
}

export const MIXED: AlertPolicyRow[] = [
  policy({ id: "row-class-fw", detector_type: null, mode: "RECORD_ONLY" }),
  policy({
    id: "row-source-waf",
    scope_kind: "SOURCE",
    scope_value: SRC_WAF,
    family: "waf",
    detector_type: "WAF_REQUEST_RATE_SPIKE",
    mode: "DIGEST",
    source_name: "edge-waf",
    source_class: "WAF",
    updated_by: "analyst",
    instance_id: INST_A,
    instance_name: "lab-console",
  }),
  policy({
    id: "row-source-noev",
    scope_kind: "SOURCE",
    scope_value: SRC_FW,
    detector_type: "NO_EVENTS",
    mode: "DIGEST",
    source_name: "pa-fw-01",
  }),
  policy({
    id: "row-watch",
    scope_kind: "SOURCE",
    scope_value: WATCH,
    family: "property_watch",
    detector_type: "PW_NEW_VALUE",
    mode: "RECORD_ONLY",
    source_name: "admin-logins",
    source_class: null,
  }),
];

export function run(over: Partial<DigestRun> = {}): DigestRun {
  return {
    instance_id: INST_A,
    instance_name: "lab-console",
    digest_date: "2026-11-06",
    state: "SUCCESS",
    item_count: 4,
    recipient_count: 3,
    error_code: null,
    sent_at: "2026-11-06T07:05:00Z",
    created_at: "2026-11-06T07:05:01Z",
    ...over,
  };
}

export function digest(over: Partial<DigestStatus> & { last?: DigestRun | null } = {}): DigestStatus {
  const { last, ...rest } = over;
  const lastRun = last === undefined ? run() : last;
  return {
    digest_hour_utc: 7,
    email_configured: true,
    recipient_count: 3,
    last_run: lastRun,
    instances: [{ instance_id: INST_A, instance_name: "lab-console", last_run: lastRun }],
    ...rest,
  };
}

export function fleetSource(over: Partial<FleetSource> = {}): FleetSource {
  return {
    id: SRC_FW,
    instance_id: INST_A,
    qradar_id: 12,
    name: "pa-fw-01",
    type_name: "Palo Alto PA Series",
    source_class: "FIREWALL",
    enabled: true,
    ...over,
  } as FleetSource;
}

export function watch(over: Partial<PropertyWatch> = {}): PropertyWatch {
  return { id: WATCH, instance_id: INST_A, name: "admin-logins", ...over } as PropertyWatch;
}
