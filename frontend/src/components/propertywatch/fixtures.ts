// Realistic fixtures for the property-watch tests, including hostile ones. They mirror the
// payload shapes of `app/schemas/property_watch.py`; a field that is not measured is `null`.

import type {
  AnomalySummary,
  PwAnomalyDetail,
  PwDetectionDetail,
  WatchAql,
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
    threshold_rules: [],
    alerting_mode: "shadow",
    cadence_seconds: 3600,
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
      value_id: "id-sb",
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

/** A spike's detection block as PW-5c serves it: the band, the figures, no new-value fields. */
export function spikeDetection(over: Partial<PwDetectionDetail> = {}): PwDetectionDetail {
  return {
    reason: "420 events is above the median 100.0 (+320, 4.20x); robust z=6.10 (threshold +3.5)",
    expected_low: 80,
    expected_high: 140,
    threshold: 3.5,
    baseline_sample_count: 36,
    baseline_version: 2,
    watch_version: 3,
    observed_events: 420,
    expected_median: 100,
    metric_semantics: "events per bucket",
    observed_measurement: "EXACT",
    absolute_delta: 320,
    ratio: 4.2,
    ratio_basis: null,
    robust_score_status: "OK",
    robust_z: 6.1,
    fallback_bound: null,
    classification: null,
    history_buckets_verified: null,
    events_since_first_seen: null,
    buckets_with_traffic: null,
    claim: null,
    ...over,
  };
}

/** A new value's detection block: counts only, no band. */
export function newValueDetection(over: Partial<PwDetectionDetail> = {}): PwDetectionDetail {
  return spikeDetection({
    reason: "at least 31 events across 3 buckets since first appearance",
    expected_low: null,
    expected_high: null,
    threshold: 20,
    baseline_sample_count: 2016,
    observed_events: null,
    expected_median: null,
    observed_measurement: "LOWER_BOUND",
    absolute_delta: null,
    ratio: null,
    robust_score_status: null,
    robust_z: null,
    classification: "PW_NEW_VALUE",
    history_buckets_verified: 2016,
    events_since_first_seen: 31,
    buckets_with_traffic: 3,
    claim: "the value stayed below 5 events in every bucket of the verified history",
    ...over,
  });
}

/** The detail read of a PW incident, as `GET /anomalies/{id}` serves it. */
export function pwDetail(over: Partial<PwAnomalyDetail> = {}): PwAnomalyDetail {
  return {
    ...incident(),
    baseline_version: 2,
    policy_version: 1,
    transitions: [
      { from_state: null, to_state: "CANDIDATE", occurred_at: iso(7), bucket_start: iso(7), reason: "above band", actor: "engine", observed_value: 420, expected_value: 100 },
      { from_state: "CANDIDATE", to_state: "OPEN", occurred_at: iso(9), bucket_start: iso(9), reason: "3 consecutive buckets", actor: "engine", observed_value: 420, expected_value: 100 },
    ],
    explanation_package: null,
    detection: spikeDetection(),
    ...over,
  } as PwAnomalyDetail;
}

export function aql(over: Partial<WatchAql> = {}): WatchAql {
  return {
    watch_id: WATCH_ID,
    watch_version: 3,
    window_start: "2026-10-18T07:00:00Z",
    window_end: "2026-10-18T08:00:00Z",
    bucket_seconds: 300,
    current: [
      { search: "GROUPED", text: "SELECT \"username\" AS v, COUNT(*) AS c FROM events GROUP BY v ORDER BY c DESC LIMIT 50" },
      { search: "TOTALS", text: "SELECT COUNT(*) AS total FROM events" },
    ],
    current_unavailable: null,
    last_bucket: {
      bucket_start: iso(11),
      collected_at: iso(11),
      watch_version: 3,
      queries: [{ search: "GROUPED", text: "SELECT \"username\" AS v, COUNT(*) AS c FROM events GROUP BY v LIMIT 50" }],
    },
    ...over,
  };
}
