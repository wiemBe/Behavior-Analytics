// Builders for the catalog widget tests: complete, valid responses with named overrides.

import type {
  CatchallSender,
  CoverageSummary,
  OutageClassSummary,
  OutageFleetSummary,
  SearchCadenceItem,
  WafSourceBehavior,
} from "@/lib/api";
import { fleetClass } from "@/lib/home/fixtures";

export function coverage(over: Partial<CoverageSummary> = {}): CoverageSummary {
  return {
    generated_at: "2026-10-14T13:00:00Z",
    total_techniques: 200,
    by_status: { COVERED: 50, PARTIAL: 30, MISSING: 100, NOT_EVALUATED: 20 },
    covered_ratio: 0.25,
    average_coverage_score: 0.3,
    mapping_provenance: { explicit: 10, inferred: 5 },
    ...over,
  };
}

export function cadence(over: Partial<SearchCadenceItem> = {}): SearchCadenceItem {
  return {
    family: "PHASE_A_VOLUME",
    cadence_seconds: 900,
    allowed_cadences_seconds: [300, 900],
    bucket_seconds: 300,
    settle_seconds: 60,
    last_slot_start: null,
    next_dispatch_at: "2026-10-14T14:15:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    updated_by: null,
    last_run: {
      at: "2026-10-14T13:55:00Z",
      outcome: "ok",
      searches_sent: 4,
      buckets_collected: 4,
      split_count: 0,
    },
    ...over,
  };
}

export function sender(over: Partial<CatchallSender> = {}): CatchallSender {
  return {
    id: "c1",
    catchall_log_source_id: "l1",
    catchall_log_source_name: "SIM Generic Log DSM-7",
    identifier: "10.1.2.3",
    first_seen_at: "2026-10-14T09:00:00Z",
    last_seen_at: "2026-10-14T13:00:00Z",
    events_24h: 1200,
    active: true,
    review_status: "NEW",
    reviewed_by: null,
    reviewed_at: null,
    ...over,
  };
}

export function outageClass(
  source_class: OutageClassSummary["source_class"],
  over: Partial<OutageClassSummary> = {},
): OutageClassSummary {
  return { ...fleetClass(source_class), outage_mode: "ALERT", outage_overrides: 0, ...over };
}

export function outage(
  classes: OutageClassSummary[],
  over: Partial<OutageFleetSummary> = {},
): OutageFleetSummary {
  return {
    window: "24h",
    window_start: "2026-10-13T14:00:00Z",
    window_end: "2026-10-14T14:00:00Z",
    bucket_seconds: 300,
    classes,
    internal: { sources: { enabled: 0, disabled: 0 } },
    new_sources: 0,
    conflict_sources: 0,
    unclassified_sources: 0,
    outage_default: "ALERT",
    default_silence_grace_buckets: 1,
    ...over,
  };
}

export function wafSource(over: Partial<WafSourceBehavior> = {}): WafSourceBehavior {
  return {
    log_source_id: "w1",
    log_source_name: "waf-edge-01",
    product_label: null,
    enabled: true,
    profile_version: 2,
    validation_status: "VALID",
    validated_profile_version: 2,
    validated_at: "2026-10-10T00:00:00Z",
    validation_current: true,
    virtual_host_available: true,
    latest_bucket: {
      bucket_start: "2026-10-14T13:55:00Z",
      bucket_seconds: 300,
      completeness: "COMPLETE",
      endpoint_coverage_ratio: 0.92,
      coverage_measurement: "EXACT",
      request_count_measurement: "EXACT",
      total_events: 4000,
      in_maintenance: false,
      groups_truncated: false,
      admission_truncated: false,
    },
    active_endpoints: 120,
    total_endpoints: 150,
    active_endpoint_ceiling: 500,
    admission: {
      buckets_in_range: 10,
      buckets_admission_truncated: 0,
      rejected_over_quota: 0,
      below_admission_threshold: 0,
      untracked_requests: 0,
    },
    ...over,
  };
}
