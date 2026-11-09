// Test fixtures for the rule-match anomaly surfaces (SOC-6e). Shapes mirror
// backend/app/schemas/rule_anomaly.py; the sentences are the backend catalog's English text.

import type {
  AnomalySummary,
  MatchAnomalyRow,
  RuleAnomalyDetail,
  RuleMatchAnomalies,
  RuleMatchSection,
  RuleMatchSectionIncident,
} from "@/lib/api";

export const RULE_ID = "22222222-2222-2222-2222-222222222222";
export const ANOMALY_ID = "99999999-0000-4000-8000-000000000001";

export function span(over: Partial<NonNullable<RuleMatchAnomalies["span"]>> = {}) {
  return {
    days: 7,
    span_start: "2026-11-02T10:00:00Z",
    span_end: "2026-11-09T10:00:00Z",
    window_seconds: 3600,
    expected_windows: 168,
    complete_windows: 168,
    truncated_windows: 0,
    failed_windows: 0,
    missing_windows: 0,
    complete: true,
    window_status: "complete",
    reason: null,
    ...over,
  };
}

export function row(over: Partial<MatchAnomalyRow> = {}): MatchAnomalyRow {
  return {
    anomaly_id: ANOMALY_ID,
    rule_id: RULE_ID,
    name: "Multiple login failures from a single source",
    qradar_id: 100123,
    is_building_block: false,
    detector: "RULE_STORM",
    observed: 4800,
    expected: 310.5,
    state: "OPEN",
    started: "2026-11-09T08:00:00Z",
    row_state: "OK",
    window_status: "complete",
    ...over,
  };
}

export function block(over: Partial<RuleMatchAnomalies> = {}): RuleMatchAnomalies {
  return {
    instance_id: "11111111-1111-1111-1111-111111111111",
    state: "OK",
    span: span(),
    items: [row()],
    total: 1,
    items_truncated: false,
    reason: null,
    ...over,
  };
}

export const SPAN_REASON =
  "The 7-day span is not fully measured (3 missing, 2 truncated, 1 failed, 0 of another width, of 168 windows). The judged window runs from 2026-11-09 09:00 UTC to 2026-11-09 10:00 UTC.";

export function incident(over: Partial<RuleMatchSectionIncident> = {}): RuleMatchSectionIncident {
  return {
    anomaly_id: ANOMALY_ID,
    detector: "RULE_STORM",
    observed: 4800,
    expected: 310.5,
    baseline_low: 120,
    baseline_high: 640,
    consecutive_buckets: null,
    ratio: 15.46,
    started: "2026-11-09T08:00:00Z",
    ended: null,
    state: "OPEN",
    ...over,
  };
}

export function section(over: Partial<RuleMatchSection> = {}): RuleMatchSection {
  return {
    rule_id: RULE_ID,
    state: "OK",
    reason: "Judged through 2026-11-09 10:00 UTC. No open match anomaly.",
    reason_code: "rule.match_anomaly.judged_clear",
    reason_params: { judged_through: "2026-11-09T10:00:00Z" },
    match_evidence: null,
    inventory_synced_at: null,
    modified_at: null,
    judged_through: "2026-11-09T10:00:00Z",
    window_start: null,
    window_end: null,
    counts: null,
    incidents: [],
    ...over,
  };
}

export function ruleListItem(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    id: ANOMALY_ID,
    log_source_id: null,
    log_source_name: null,
    anomaly_type: "RULE_STORM",
    state: "OPEN",
    severity: "MEDIUM",
    observed_value: 4800,
    expected_value: 310.5,
    deviation_ratio: 15.46,
    robust_z: null,
    absolute_delta: 4489.5,
    consecutive_buckets: 1,
    confidence: null,
    detected_at: "2026-11-09T08:00:00Z",
    opened_at: "2026-11-09T08:00:00Z",
    anomaly_start: "2026-11-09T08:00:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: null,
    subject_kind: "RULE",
    rule: {
      rule_id: RULE_ID,
      name: "Multiple login failures from a single source",
      qradar_id: 100123,
      is_building_block: false,
    },
    triage: { status: "NEW", owner: null, disposition: null, updated_at: null },
    ...over,
  };
}

export function ruleDetail(over: Partial<RuleAnomalyDetail> = {}): RuleAnomalyDetail {
  return {
    ...ruleListItem(),
    subject_kind: "RULE",
    rule: {
      rule_id: RULE_ID,
      name: "Multiple login failures from a single source",
      qradar_id: 100123,
      is_building_block: false,
    },
    baseline_version: null,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: null,
    ...over,
  } as RuleAnomalyDetail;
}
