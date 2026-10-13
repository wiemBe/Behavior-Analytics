// Realistic fixtures for the field-mapping tests: the WAF schema as the API
// serves it (docs/FIELD-MAPPING.md §FM.3), and builders for the shapes whose
// every field the tests vary.

import type {
  FieldValidationOut,
  MappingSchemaOut,
  PropertyCatalogOut,
  ValidationReportOut,
  WafProfileHistoryEntryOut,
  WafProfileOut,
  WafSourceStatus,
} from "@/lib/api";

export const SCHEMA: MappingSchemaOut = {
  domain: "WAF",
  property_name_pattern: "^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$",
  min_coverage_ratio: 0.9,
  fields: [
    {
      key: "URL",
      label: "URL path",
      description:
        "The request path. Normalized into the endpoint every WAF metric is counted for.",
      required: true,
      used_by_collection: true,
      kinds: ["CUSTOM_PROPERTY"],
      normalized_options: [],
      default: "URL",
    },
    {
      key: "METHOD",
      label: "HTTP method",
      description: "Part of the endpoint key, e.g. GET /login.",
      required: true,
      used_by_collection: true,
      kinds: ["CUSTOM_PROPERTY"],
      normalized_options: [],
      default: "HTTP_Method",
    },
    {
      key: "VIRTUAL_HOST",
      label: "Virtual host",
      description:
        "Separates applications that share a path. Unmapped, endpoints combine their traffic.",
      required: false,
      used_by_collection: false,
      kinds: ["CUSTOM_PROPERTY"],
      normalized_options: [],
      default: null,
    },
    {
      key: "ACTION",
      label: "Action",
      description: "What the WAF did. Feeds the block ratio, through the action value map.",
      required: false,
      used_by_collection: true,
      kinds: ["CUSTOM_PROPERTY"],
      normalized_options: [],
      default: null,
    },
    {
      key: "RESPONSE_CODE",
      label: "HTTP status",
      description: "Feeds the 4xx and 5xx ratios.",
      required: false,
      used_by_collection: true,
      kinds: ["CUSTOM_PROPERTY"],
      normalized_options: [],
      default: null,
    },
  ],
  value_maps: [{ field: "ACTION", outcomes: ["BLOCKED", "ALLOWED"] }],
};

export const FULL_MAPPING = {
  URL: "URL",
  METHOD: "HTTP_Method",
  VIRTUAL_HOST: null,
  ACTION: "WAF_Action",
  RESPONSE_CODE: "WAF_ResponseCode",
};

export function fieldReport(over: Partial<FieldValidationOut> = {}): FieldValidationOut {
  return {
    field: "URL",
    property_name: "URL",
    status: "AVAILABLE",
    used_by_collection: true,
    populated_events: 9800,
    populated_ratio: 0.98,
    distinct_values: 412,
    plausible_ratio: 0.99,
    sampled_events: 9700,
    values_truncated: false,
    discovered_values: [],
    detail: null,
    ...over,
  };
}

export function report(over: Partial<ValidationReportOut> = {}): ValidationReportOut {
  return {
    status: "VALID",
    reasons: [],
    total_events: 10000,
    window_start: "2026-10-12T22:00:00Z",
    window_end: "2026-10-12T23:00:00Z",
    profile_version: 4,
    fields: [
      fieldReport(),
      fieldReport({ field: "METHOD", property_name: "HTTP_Method", distinct_values: 7 }),
      fieldReport({
        field: "ACTION",
        property_name: "WAF_Action",
        distinct_values: 3,
        discovered_values: [
          { value: "BLOCK", events: 1800 },
          { value: "ALLOW", events: 7900 },
          { value: "ALERT", events: 120 },
        ],
      }),
    ],
    ...over,
  };
}

export function profile(over: Partial<WafProfileOut> = {}): WafProfileOut {
  return {
    profile_id: "p-1",
    instance_id: "inst-1",
    log_source_id: "ls-1",
    qradar_log_source_id: 212,
    log_source_name: "WAF edge - eu-west",
    log_source_type_name: "Generic WAF DSM",
    product_label: "Acme WAF",
    profile_version: 4,
    enabled: true,
    mapping: { ...FULL_MAPPING },
    action_value_map: { BLOCKED: ["BLOCK"], ALLOWED: ["ALLOW"] },
    validation_status: "VALID",
    validated_profile_version: 4,
    validated_at: "2026-10-12T23:05:00Z",
    validation: report(),
    validation_pending: false,
    validation_requested_at: null,
    validation_requested_by: null,
    collection_eligible: true,
    ineligible_reason: null,
    updated_by: "a.rivera",
    updated_at: "2026-10-12T22:58:00Z",
    ...over,
  };
}

export function status(over: Partial<WafSourceStatus> = {}): WafSourceStatus {
  return {
    profile_id: "p-1",
    instance_id: "inst-1",
    log_source_id: "ls-1",
    qradar_log_source_id: 212,
    log_source_name: "WAF edge - eu-west",
    product_label: "Acme WAF",
    enabled: true,
    profile_version: 4,
    validation_status: "VALID",
    validated_profile_version: 4,
    validated_at: "2026-10-12T23:05:00Z",
    validation_fields: [],
    validation_pending: false,
    validation_requested_at: null,
    validation_requested_by: null,
    collection_eligible: true,
    ineligible_reason: null,
    ...over,
  };
}

export function catalog(over: Partial<PropertyCatalogOut> = {}): PropertyCatalogOut {
  return {
    status: "AVAILABLE",
    fetched_at: "2026-10-12T23:00:00Z",
    filtered_by_source_type: true,
    properties: [
      { name: "HTTP_Method", property_type: "STRING", applies_to_source_type: true, name_allowed: true },
      { name: "URL", property_type: "STRING", applies_to_source_type: true, name_allowed: true },
      { name: "WAF_Action", property_type: "STRING", applies_to_source_type: true, name_allowed: true },
      { name: "WAF_ResponseCode", property_type: "NUMERIC", applies_to_source_type: true, name_allowed: true },
      { name: "WAF_URL", property_type: "STRING", applies_to_source_type: false, name_allowed: true },
      { name: "bad;name", property_type: "STRING", applies_to_source_type: false, name_allowed: false },
    ],
    detail: null,
    ...over,
  };
}

export const HISTORY: WafProfileHistoryEntryOut[] = [
  {
    at: "2026-10-12T23:05:00Z",
    actor: "system",
    action: "waf.profile.validated",
    profile_version: 4,
    mapping_before: null,
    mapping_after: null,
    outcome: "VALID",
    reasons: null,
  },
  {
    at: "2026-10-12T22:58:00Z",
    actor: "a.rivera",
    action: "waf.profile.mapping_changed",
    profile_version: 4,
    mapping_before: { ...FULL_MAPPING, URL: "request_uri" },
    mapping_after: { ...FULL_MAPPING },
    outcome: null,
    reasons: null,
  },
];
