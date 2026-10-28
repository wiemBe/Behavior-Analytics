// Realistic fixtures for the IPS administration tests: the schema as
// GET /ips/mapping-schema serves it (T-049), and builders for the shapes whose
// every field the tests vary. The lab sources are PaSeries @ pa-fw-01 (type
// 206) and CiscoFirepowerThreatDefense @ ftd-01 (type 448).

import type {
  IpsCandidateOut,
  IpsFieldReference,
  IpsMapping,
  IpsMappingSchemaOut,
  IpsProfileHistoryEntryOut,
  IpsProfileOut,
  IpsSourceStatus,
  IpsValidationFieldOut,
  IpsValidationReportOut,
  PropertyCatalogOut,
} from "@/lib/api";

const NORMALIZED = [
  "sourceip",
  "destinationip",
  "destinationport",
  "sourceport",
  "protocolid",
  "qid",
  "severity",
  "category",
];

const n = (name: string): IpsFieldReference => ({ kind: "NORMALIZED", name });
const c = (name: string): IpsFieldReference => ({ kind: "CUSTOM_PROPERTY", name });

export const DEFAULT_MAPPING: IpsMapping = {
  SOURCE_IP: n("sourceip"),
  DESTINATION_IP: n("destinationip"),
  DESTINATION_PORT: n("destinationport"),
  PROTOCOL: n("protocolid"),
  SIGNATURE: n("qid"),
  ACTION: null,
  SEVERITY: n("severity"),
};

export const IPS_SCHEMA: IpsMappingSchemaOut = {
  domain: "IPS",
  property_name_pattern: "^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$",
  min_coverage_ratio: 0.9,
  fields: [
    {
      key: "SOURCE_IP",
      label: "Source IP",
      description: "The initiating address. Feeds concentration, fan-out and contributor evidence.",
      required: true,
      used_by_collection: true,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: n("sourceip"),
    },
    {
      key: "DESTINATION_IP",
      label: "Destination IP",
      description: "The protected destination address used for destination behavior.",
      required: true,
      used_by_collection: true,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: n("destinationip"),
    },
    {
      key: "DESTINATION_PORT",
      label: "Destination port",
      description: "The protected service port used for port rate, novelty and fan-out behavior.",
      required: true,
      used_by_collection: true,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: n("destinationport"),
    },
    {
      key: "PROTOCOL",
      label: "Protocol",
      description: "Separates services that share a port number, such as TCP and UDP.",
      required: false,
      used_by_collection: true,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: n("protocolid"),
    },
    {
      key: "SIGNATURE",
      label: "Signature",
      description: "The IPS signature or QRadar QID used for signature behavior.",
      required: true,
      used_by_collection: true,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: n("qid"),
    },
    {
      key: "ACTION",
      label: "Action",
      description: "What the IPS did. Feeds the block ratio through the action value map.",
      required: false,
      used_by_collection: false,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: null,
    },
    {
      key: "SEVERITY",
      label: "Severity",
      description: "The vendor or QRadar severity retained for bounded evidence only.",
      required: false,
      used_by_collection: false,
      kinds: ["NORMALIZED", "CUSTOM_PROPERTY"],
      normalized_options: NORMALIZED,
      default: n("severity"),
    },
  ],
  value_maps: [{ field: "ACTION", outcomes: ["BLOCKED", "ALLOWED"] }],
  presets: [
    {
      key: "CISCO_FTD",
      label: "Cisco Firepower Threat Defense",
      mapping: { ...DEFAULT_MAPPING },
      action_value_map: {},
    },
    {
      key: "PALO_ALTO_THREAT",
      label: "Palo Alto THREAT",
      mapping: { ...DEFAULT_MAPPING },
      action_value_map: {},
    },
  ],
};

export function fieldReport(over: Partial<IpsValidationFieldOut> = {}): IpsValidationFieldOut {
  return {
    field: "SOURCE_IP",
    reference: n("sourceip"),
    status: "AVAILABLE",
    used_by_collection: true,
    populated_events: 9990,
    populated_ratio: 0.999,
    generic_name_events: null,
    generic_name_ratio: null,
    values_truncated: false,
    discovered_values: [],
    detail: null,
    warnings: [],
    ...over,
  };
}

export function report(over: Partial<IpsValidationReportOut> = {}): IpsValidationReportOut {
  return {
    status: "VALID",
    reasons: [],
    warnings: [],
    total_events: 10000,
    window_start: "2026-10-13T08:00:00Z",
    window_end: "2026-10-13T09:00:00Z",
    profile_version: 2,
    probe_truncated: false,
    ariel_searches: 14,
    fields: [
      fieldReport(),
      fieldReport({ field: "DESTINATION_IP", reference: n("destinationip") }),
      fieldReport({ field: "DESTINATION_PORT", reference: n("destinationport"), populated_ratio: 1 }),
      fieldReport({ field: "PROTOCOL", reference: n("protocolid"), populated_ratio: 1 }),
      fieldReport({
        field: "SIGNATURE",
        reference: n("qid"),
        populated_ratio: 1,
        generic_name_events: null,
        generic_name_ratio: null,
      }),
      fieldReport({
        field: "ACTION",
        reference: null,
        status: "NOT_MAPPED",
        used_by_collection: false,
        populated_events: null,
        populated_ratio: null,
        detail: "no QRadar field is mapped to this logical field",
        detail_code: "validation.ips.field.not_mapped",
        detail_params: {},
      }),
      fieldReport({
        field: "SEVERITY",
        reference: n("severity"),
        used_by_collection: false,
        populated_ratio: 1,
      }),
    ],
    ...over,
  };
}

export function profile(over: Partial<IpsProfileOut> = {}): IpsProfileOut {
  return {
    profile_id: "p-1",
    instance_id: "inst-1",
    log_source_id: "ls-413",
    qradar_log_source_id: 413,
    log_source_name: "CiscoFirepowerThreatDefense @ ftd-01",
    log_source_type_name: "Cisco Firepower Threat Defense",
    product_label: "FTD lab",
    preset: "CISCO_FTD",
    profile_version: 2,
    enabled: true,
    mapping: { ...DEFAULT_MAPPING },
    action_value_map: { BLOCKED: [], ALLOWED: [] },
    validation_status: "VALID",
    validated_profile_version: 2,
    validated_at: "2026-10-13T09:02:00Z",
    validation: report(),
    validation_pending: false,
    validation_requested_at: null,
    validation_requested_by: null,
    collection_eligible: true,
    ineligible_reason: null,
    updated_by: "a.rivera",
    updated_at: "2026-10-13T08:58:00Z",
    ...over,
  };
}

/** The PAN source with ACTION mapped to a custom property and a truncated value list. */
export function panProfile(over: Partial<IpsProfileOut> = {}): IpsProfileOut {
  return profile({
    log_source_id: "ls-412",
    qradar_log_source_id: 412,
    log_source_name: "PaSeries @ pa-fw-01",
    log_source_type_name: "Palo Alto PA Series",
    product_label: "PAN lab",
    preset: "PALO_ALTO_THREAT",
    profile_version: 3,
    validated_profile_version: 3,
    mapping: { ...DEFAULT_MAPPING, ACTION: c("PAN_Action") },
    action_value_map: { BLOCKED: ["reset-both"], ALLOWED: ["alert"] },
    validation_status: "INCONCLUSIVE",
    collection_eligible: false,
    enabled: false,
    ineligible_reason: "the profile is disabled",
    ineligible_reason_code: "validation.profile.ineligible.disabled",
    ineligible_reason_params: {},
    validation: report({
      status: "INCONCLUSIVE",
      profile_version: 3,
      probe_truncated: true,
      reasons: ["the bounded validation probe was truncated"],
      reason_codes: [{ code: "validation.ips.profile.truncated", params: {} }],
      fields: [
        ...report().fields.filter((f) => f.field !== "ACTION"),
        fieldReport({
          field: "ACTION",
          reference: c("PAN_Action"),
          used_by_collection: false,
          populated_ratio: 0.97,
          values_truncated: true,
          discovered_values: [
            { value: "alert", events: 5200 },
            { value: "reset-both", events: 2100 },
            { value: "drop", events: 900 },
          ],
        }),
      ],
    }),
    ...over,
  });
}

export function status(over: Partial<IpsSourceStatus> = {}): IpsSourceStatus {
  return {
    profile_id: "p-1",
    instance_id: "inst-1",
    log_source_id: "ls-413",
    qradar_log_source_id: 413,
    log_source_name: "CiscoFirepowerThreatDefense @ ftd-01",
    log_source_type_name: "Cisco Firepower Threat Defense",
    product_label: "FTD lab",
    preset: "CISCO_FTD",
    enabled: true,
    profile_version: 2,
    validation_status: "VALID",
    validated_profile_version: 2,
    validated_at: "2026-10-13T09:02:00Z",
    validation_pending: false,
    validation_requested_at: null,
    validation_requested_by: null,
    collection_eligible: true,
    ineligible_reason: null,
    updated_by: "a.rivera",
    updated_at: "2026-10-13T08:58:00Z",
    ...over,
  };
}

export function candidate(over: Partial<IpsCandidateOut> = {}): IpsCandidateOut {
  return {
    instance_id: "inst-1",
    log_source_id: "ls-412",
    qradar_id: 412,
    name: "PaSeries @ pa-fw-01",
    type_name: "Palo Alto PA Series",
    source_class: "IPS",
    ...over,
  };
}

export function catalog(over: Partial<PropertyCatalogOut> = {}): PropertyCatalogOut {
  return {
    status: "AVAILABLE",
    fetched_at: "2026-10-13T09:00:00Z",
    filtered_by_source_type: true,
    properties: [
      { name: "PAN_Action", property_type: "STRING", applies_to_source_type: true, name_allowed: true },
      { name: "PAN_ThreatID", property_type: "NUMERIC", applies_to_source_type: true, name_allowed: true },
      { name: "PAN_Zone", property_type: "STRING", applies_to_source_type: true, name_allowed: true },
      { name: "WAF_Action", property_type: "STRING", applies_to_source_type: false, name_allowed: true },
    ],
    detail: null,
    ...over,
  };
}

export const HISTORY: IpsProfileHistoryEntryOut[] = [
  {
    at: "2026-10-13T09:02:00Z",
    actor: "system",
    action: "ips.profile.validated",
    profile_version: 2,
    mapping_before: null,
    mapping_after: null,
    outcome: "SUCCESS",
    reasons: null,
  },
  {
    at: "2026-10-13T08:58:00Z",
    actor: "a.rivera",
    action: "ips.profile.mapping_changed",
    profile_version: 2,
    mapping_before: { ...DEFAULT_MAPPING, SIGNATURE: c("PAN_ThreatID") },
    mapping_after: { ...DEFAULT_MAPPING },
    outcome: "SUCCESS",
    reasons: null,
  },
];
