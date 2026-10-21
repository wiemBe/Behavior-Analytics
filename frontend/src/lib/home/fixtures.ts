// Builders for the derivation tests. Each takes overrides and returns a complete,
// valid response object, so a test names only the field it is about.

import type {
  AnomalySummary,
  BehaviorSummary,
  EpsSeriesPoint,
  FleetEpsSeries,
  FleetClassSummary,
  FleetSummary,
  OffenseAnalytics,
  OffenseSummary,
  Page,
  SourceBehavior,
  SourceClass,
} from "@/lib/api";

export function fleetClass(
  source_class: SourceClass,
  over: Partial<FleetClassSummary> = {},
): FleetClassSummary {
  return {
    source_class,
    sources: { enabled: 2, disabled: 0 },
    buckets: { expected: 48, complete: 48, complete_from_history: 0, partial: 0, missing: 0 },
    events: 100,
    mean_eps: 10,
    expected_eps: null,
    baseline_sources: 0,
    baseline_share: null,
    monitored_sources: 2,
    open_incidents: {},
    top_sources: [],
    ...over,
  };
}

export function fleet(classes: FleetClassSummary[], over: Partial<FleetSummary> = {}): FleetSummary {
  return {
    window: "24h",
    window_start: "2026-10-13T14:00:00Z",
    window_end: "2026-10-14T14:00:00Z",
    bucket_seconds: 3600,
    classes,
    internal: { sources: { enabled: 0, disabled: 0 } },
    new_sources: 0,
    conflict_sources: 0,
    unclassified_sources: 0,
    ...over,
  };
}

export function noData(source_class: SourceClass): FleetClassSummary {
  return fleetClass(source_class, {
    events: null,
    mean_eps: null,
    buckets: { expected: 48, complete: 0, complete_from_history: 0, partial: 0, missing: 48 },
  });
}

export function behavior(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "s1",
    name: "src",
    criticality: "MEDIUM",
    observed_eps: 5,
    expected_eps: 5,
    expected_low: 4,
    expected_high: 6,
    deviation_ratio: 1,
    state: "NORMAL",
    baseline_sample_count: 100,
    baseline_completeness: 1,
    last_bucket_at: "2026-10-14T13:00:00Z",
    last_bucket_completeness: "COMPLETE",
    last_event_at: "2026-10-14T13:59:00Z",
    open_anomaly_count: 0,
    ...over,
  };
}

export function summary(over: Partial<BehaviorSummary> = {}): BehaviorSummary {
  return {
    open_anomalies: 0,
    spikes: 0,
    drops: 0,
    silent_sources: 0,
    candidates: 0,
    recovering: 0,
    insufficient_data_sources: 0,
    monitored_sources: 0,
    evidence_pending: 0,
    evidence_failed: 0,
    recently_resolved: [],
    highest_deviation: [],
    ...over,
  };
}

export function anomaly(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: "a1",
    log_source_id: "s1",
    log_source_name: "src",
    anomaly_type: "TRAFFIC_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 10,
    expected_value: 5,
    deviation_ratio: 2,
    robust_z: null,
    absolute_delta: null,
    consecutive_buckets: 3,
    confidence: null,
    detected_at: "2026-10-14T10:30:00Z",
    opened_at: null,
    anomaly_start: null,
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: null,
    ...over,
  };
}

export function page<T>(items: T[], total = items.length): Page<T> {
  return { items, total, limit: 200, offset: 0 };
}

export function analytics(
  agg: Partial<OffenseAnalytics["aggregates"]> = {},
  magnitude: { key: string; count: number }[] = [],
): OffenseAnalytics {
  return {
    generated_at: "2026-10-14T14:00:00Z",
    aggregates: {
      active: 0,
      critical: 0,
      unassigned: 0,
      exceeding_sla: 0,
      oldest_age_seconds: null,
      average_magnitude: null,
      sla_hours: 24,
      critical_magnitude: 7,
      ...agg,
    },
    aging: [],
    status_distribution: [],
    magnitude_distribution: magnitude,
    category_distribution: [],
    assignment_distribution: [],
    top_rules: [],
    top_usernames: [],
    top_source_ips: [],
    top_destination_ips: [],
  };
}

export function offenseRow(over: Partial<OffenseSummary> = {}): OffenseSummary {
  return {
    instance_id: "i",
    qradar_offense_id: 1,
    captured_at: "2026-10-14T13:00:00Z",
    description: null,
    status: "OPEN",
    magnitude: 5,
    severity: null,
    credibility: null,
    relevance: null,
    assigned_to: null,
    is_assigned: false,
    offense_type_name: null,
    offense_source: null,
    source_network: null,
    event_count: null,
    flow_count: null,
    source_count: null,
    destination_count: null,
    start_time: null,
    last_updated_time: null,
    close_time: null,
    age_seconds: null,
    categories: [],
    ...over,
  };
}

const EPS_T0 = Date.parse("2026-10-14T12:00:00Z");

/** A step of the fleet EPS series; `null` eps is a step that was not fully measured. */
export function epsPoint(
  i: number,
  eps: number | null,
  expected: number | null = null,
): EpsSeriesPoint {
  return {
    step_start: new Date(EPS_T0 + i * 300_000).toISOString(),
    events: null,
    eps,
    expected_eps: expected,
    cells_complete: eps === null ? 3 : 4,
    cells_expected: 4,
    sources_expected: 4,
  };
}

export function epsSeries(
  points: EpsSeriesPoint[],
  over: Partial<FleetEpsSeries> = {},
): FleetEpsSeries {
  return {
    window: "1h",
    group_by: "none",
    window_start: new Date(EPS_T0).toISOString(),
    window_end: new Date(EPS_T0 + points.length * 300_000).toISOString(),
    bucket_seconds: 300,
    step_seconds: 300,
    series: [{ key: "all", label: "All", sources_expected: 4, points }],
    coverage: {
      steps_total: points.length,
      steps_measured: points.filter((p) => p.eps !== null).length,
    },
    ...over,
  };
}
