import type {
  CallerInfo,
  CustomRule,
  CustomRuleBreach,
  CustomRuleCatalog,
  CustomRulePreview,
} from "@/lib/api";

export const reader: CallerInfo = {
  subject: "analyst",
  email: null,
  permissions: ["read:*"],
  effective_permissions: ["read:custom_rules"],
};

export const writer: CallerInfo = {
  subject: "detector",
  email: null,
  permissions: ["custom_rule:write"],
  effective_permissions: ["read:custom_rules", "custom_rule:write"],
};

export function catalog(overrides: Partial<CustomRuleCatalog> = {}): CustomRuleCatalog {
  return {
    metrics: [
      {
        metric: "AVERAGE_EPS",
        subject_kind: "LOG_SOURCE",
        unit: "events/s",
        is_ratio: false,
        measurement: "EXACT_ONLY",
        note: "exact only when the bucket row exists and is COMPLETE",
      },
      {
        metric: "REQUEST_COUNT",
        subject_kind: "WAF_ENDPOINT",
        unit: "requests/bucket",
        is_ratio: false,
        measurement: "LOWER_BOUND_PROVES_INCREASE",
        note: "a truncated bucket proves above only",
      },
      {
        metric: "BLOCK_RATIO",
        subject_kind: "WAF_ENDPOINT",
        unit: "share",
        is_ratio: true,
        measurement: "LOWER_BOUND_PROVES_INCREASE",
        note: "an unmapped action makes the numerator a lower bound",
      },
      {
        metric: "EVENTS",
        subject_kind: "IPS_ENTITY",
        unit: "events/bucket",
        is_ratio: false,
        measurement: "LOWER_BOUND_PROVES_INCREASE",
        note: "no row means not measured",
      },
      {
        metric: "TOTAL_EVENTS",
        subject_kind: "IPS_SOURCE",
        unit: "events/bucket",
        is_ratio: false,
        measurement: "EXACT_ONLY",
        note: "totals are complete or the row is absent",
      },
      {
        metric: "EPS",
        subject_kind: "FLEET_CLASS",
        unit: "events/s",
        is_ratio: false,
        measurement: "LOWER_BOUND_PROVES_INCREASE",
        note: "missing sources can only add events",
      },
      {
        metric: "PW_VALUE_EVENTS",
        subject_kind: "PW_VALUE",
        unit: "events/bucket",
        is_ratio: false,
        measurement: "LOWER_BOUND_PROVES_INCREASE",
        note: "exact for a pinned value when its search was whole; a lower-bound bucket proves above only",
      },
    ],
    subject_kinds: [
      { subject_kind: "LOG_SOURCE", column: "log_source_id" },
      { subject_kind: "FLEET_CLASS", column: "source_class" },
      { subject_kind: "WAF_ENDPOINT", column: "waf_endpoint_id" },
      { subject_kind: "IPS_ENTITY", column: "ips_entity_id" },
      { subject_kind: "IPS_SOURCE", column: "log_source_id" },
      { subject_kind: "PW_VALUE", column: "pw_value_id" },
    ],
    source_classes: ["WAF", "IPS", "LINUX"],
    operators: ["EQ", "GE", "GT", "LE", "LT"],
    evaluation_enabled: true,
    alerting_mode: "shadow",
    max_enabled: 100,
    enabled_count: 3,
    preview_hours_min: 1,
    preview_hours_max: 168,
    ...overrides,
  };
}

export function breach(overrides: Partial<CustomRuleBreach> = {}): CustomRuleBreach {
  return {
    id: "breach-1",
    rule_id: "rule-1",
    state: "OPEN",
    first_bucket_start: "2026-10-16T07:00:00Z",
    last_bucket_start: "2026-10-16T07:15:00Z",
    opened_at: "2026-10-16T07:05:00Z",
    recovering_at: null,
    resolved_at: null,
    worst_value: 19,
    worst_measurement: "LOWER_BOUND",
    last_value: 14,
    last_measurement: "EXACT",
    unmeasured_buckets: 2,
    subject_label: "edge-web-01",
    details: {},
    ...overrides,
  };
}

export function rule(overrides: Partial<CustomRule> = {}): CustomRule {
  return {
    id: "rule-1",
    name: "Sustained edge traffic",
    description: "An operator-authored threshold",
    subject_kind: "LOG_SOURCE",
    log_source_id: "source-1",
    source_class: null,
    waf_endpoint_id: null,
    ips_entity_id: null,
    pw_value_id: null,
    metric: "AVERAGE_EPS",
    operator: "GT",
    threshold_value: 10,
    threshold_unit: "events/s",
    sustain_buckets: 2,
    recovery_buckets: 2,
    severity: "HIGH",
    enabled: true,
    consecutive_breaching: 2,
    consecutive_under: 0,
    last_evaluated_bucket_start: "2026-10-16T07:15:00Z",
    last_evaluated_at: "2026-10-16T07:20:00Z",
    last_status: "OPEN",
    last_status_reason: null,
    active_breach: breach(),
    alerting_mode: "shadow",
    created_at: "2026-10-15T08:00:00Z",
    updated_at: "2026-10-16T07:20:00Z",
    created_by: "detector",
    updated_by: "detector",
    ...overrides,
  };
}

export function preview(overrides: Partial<CustomRulePreview> = {}): CustomRulePreview {
  return {
    threshold_value: 10,
    unit: "events/s",
    operator: "GT",
    fired_count: 1,
    measured_count: 3,
    unmeasured_count: 1,
    buckets: [
      { bucket_start: "2026-10-16T07:00:00Z", value: 8, measurement: "EXACT", completeness: "COMPLETE", in_maintenance: false },
      { bucket_start: "2026-10-16T07:05:00Z", value: null, measurement: "NOT_MEASURED", completeness: "PARTIAL", in_maintenance: false },
      { bucket_start: "2026-10-16T07:10:00Z", value: 13, measurement: "LOWER_BOUND", completeness: "PARTIAL", in_maintenance: false },
      { bucket_start: "2026-10-16T07:15:00Z", value: 14, measurement: "EXACT", completeness: "COMPLETE", in_maintenance: true },
    ],
    breach_intervals: [{ start: "2026-10-16T07:10:00Z", end: null }],
    ...overrides,
  };
}
