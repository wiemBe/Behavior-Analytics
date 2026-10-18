// Realistic fixtures for the property-watch tests, including hostile ones. They mirror the
// payload shapes of `app/schemas/property_watch.py`; a field that is not measured is `null`.

import type {
  AnomalySummary,
  PagedPwValues,
  PropertyWatch,
  PropertyWatchCatalog,
  PwBucket,
  PwSeriesPoint,
  PwValue,
  PwValueSeries,
  WatchScope,
} from "@/lib/api";

export const WATCH_ID = "6f1c0f9e-0000-4000-8000-000000000001";
export const T0 = Date.parse("2026-10-18T08:00:00Z");
export const STEP = 300_000;
export const iso = (n: number) => new Date(T0 + n * STEP).toISOString();

export function watch(over: Partial<PropertyWatch> = {}): PropertyWatch {
  return {
    id: WATCH_ID,
    instance_id: "11111111-0000-4000-8000-000000000001",
    name: "VPN usernames",
    description: null,
    scope_kind: "LOG_SOURCE_GROUP",
    log_source_id: null,
    scope_type_id: null,
    scope_group_id: 12,
    scope: { kind: "LOG_SOURCE_GROUP", name: "DMZ firewalls", detail: "All / DMZ firewalls", qradar_id: 12, member_count: 37 },
    properties: [{ kind: "NORMALIZED", name: "username" }],
    pinned_values: [["svc-backup"]],
    detectors: ["PW_VALUE_SPIKE", "PW_NEW_VALUE"],
    severity: "MEDIUM",
    watch_version: 3,
    enabled: true,
    status: "OK",
    status_reason: "The last collected bucket was judged.",
    collected_through: iso(11),
    evaluated_through: iso(11),
    last_run_at: iso(11),
    coverage: { bucket_start: iso(11), total_events: 1000, parsed_events: 960, ratio: 0.96, measurement: "EXACT" },
    open_incidents: 0,
    detector_states: [],
    alerting_mode: "shadow",
    created_at: "2026-10-10T09:00:00Z",
    updated_at: "2026-10-11T09:00:00Z",
    created_by: "efe",
    updated_by: "efe",
    ...over,
  };
}

export function catalog(over: Partial<PropertyWatchCatalog> = {}): PropertyWatchCatalog {
  return {
    collection_enabled: true,
    alerting_mode: "shadow",
    max_enabled: 10,
    enabled_count: 2,
    search_budget: 20,
    budget_used: 4,
    normalized_fields: ["username"],
    reference_kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
    detectors: [
      "PW_VALUE_SPIKE",
      "PW_VALUE_DROP",
      "PW_VALUE_DISAPPEARED",
      "PW_NEW_VALUE",
      "PW_NEW_COMBINATION",
    ],
    max_properties: 2,
    max_values_per_bucket: 50,
    max_new_values_per_bucket: 20,
    max_active_values_per_watch: 200,
    max_pinned_values: 10,
    value_max_length: 128,
    max_sources_per_search: 100,
    max_chunks_per_watch: 4,
    min_coverage: 0.9,
    hours_min: 1,
    hours_max: 168,
    hours_default: 24,
    preview_searches: 2,
    preview_rate_limit: "5/minute",
    ...over,
  };
}

export function bucket(n: number, over: Partial<PwBucket> = {}): PwBucket {
  return {
    bucket_start: iso(n),
    bucket_seconds: 300,
    watch_version: 3,
    total_events: 1000,
    parsed_events: 960,
    unparsed_events: 40,
    untracked_events: 120,
    totals_complete: true,
    values_measurement: "EXACT",
    pinned_measurement: "EXACT",
    observed_values: 30,
    tracked_values_observed: 30,
    admitted_values: 30,
    below_admission_threshold: 0,
    rejected_over_quota: 0,
    admission_truncated: false,
    scope_member_count: 37,
    scope_changed: false,
    in_maintenance: false,
    collected_at: iso(n),
    ...over,
  };
}

export function value(id: string, key: string, over: Partial<PwValue> = {}): PwValue {
  return {
    id,
    watch_version: 3,
    value_key: key,
    components: [key],
    truncated: false,
    pinned: false,
    active: true,
    first_seen_at: "2026-10-12T10:00:00Z",
    last_seen_at: iso(11),
    tracked_since: "2026-10-12T10:00:00Z",
    tracked_until: null,
    latest_bucket_start: iso(11),
    reading: "COUNT",
    events: 120,
    measurement: "EXACT",
    baseline_state: "RELIABLE",
    expected_low: 80,
    expected_median: 100,
    expected_high: 140,
    reliable_cells: 168,
    cells_total: 168,
    ...over,
  };
}

export function paged(items: PwValue[]): PagedPwValues {
  return { items, total: items.length, limit: 50, offset: 0 };
}

export function point(n: number, over: Partial<PwSeriesPoint> = {}): PwSeriesPoint {
  return {
    bucket_start: iso(n),
    reading: "COUNT",
    events: 100,
    measurement: "EXACT",
    baseline_state: "RELIABLE",
    expected_low: 80,
    expected_median: 100,
    expected_high: 140,
    ...over,
  };
}

export function series(id: string, key: string, points: PwSeriesPoint[], pinned = false): PwValueSeries {
  return { value_id: id, value_key: key, pinned, points };
}

export function incident(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    subject_kind: "PW_VALUE",
    log_source_id: null,
    log_source_name: null,
    anomaly_type: "PW_VALUE_SPIKE",
    state: "OPEN",
    severity: "MEDIUM",
    observed_value: 420,
    expected_value: 100,
    deviation_ratio: 4.2,
    robust_z: 6.1,
    absolute_delta: 320,
    consecutive_buckets: 3,
    confidence: 0.9,
    detected_at: iso(8),
    opened_at: iso(9),
    anomaly_start: iso(7),
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    explanation: null,
    suppressed: false,
    observed_measurement: "EXACT",
    pw_value: {
      watch_id: WATCH_ID,
      watch_name: "VPN usernames",
      watch_version: 3,
      scope_kind: "LOG_SOURCE_GROUP",
      properties: [{ kind: "NORMALIZED", name: "username" }],
      value_key: "svc-backup",
      components: ["svc-backup"],
    },
    ...over,
  } as AnomalySummary;
}

/** Text that would run script if any layer rendered it as markup. */
export const HOSTILE = '<img src=x onerror="alert(1)">';

export function scope(over: Partial<WatchScope> = {}): WatchScope {
  return {
    watch_id: WATCH_ID,
    watch_version: 3,
    kind: "LOG_SOURCE_GROUP",
    resolved: true,
    state: null,
    reason: null,
    member_count: 37,
    members: ["fw-dmz-01", "fw-dmz-02"],
    members_total: 37,
    groups_walked: [
      { qradar_id: 12, name: "DMZ firewalls" },
      { qradar_id: 13, name: "DMZ edge" },
    ],
    inventory_synced_at: "2026-10-18T07:00:00Z",
    inventory_age_seconds: 7200,
    changes: [
      { bucket_start: iso(6), member_count_before: 37, member_count_after: 40, in_grace_window: true },
    ],
    ...over,
  };
}
