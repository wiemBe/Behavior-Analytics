// Fixtures for the outage policy tests: real response shapes, a pinned clock.

import type {
  FleetSource,
  OutageClassSummary,
  OutageFleetSummary,
  OutagePolicyRow,
  SourceClass,
} from "@/lib/api";

export const INSTANCE_B = "7c1e0000-0000-4000-8000-0000000000b2";
export const FW_02 = "5a1f0000-0000-4000-8000-000000000102";
export const GONE = "5a1f0000-0000-4000-8000-000000000999";

export function classSummary(
  source_class: SourceClass,
  enabled: number,
  over: Partial<OutageClassSummary> = {},
): OutageClassSummary {
  return {
    source_class,
    sources: { enabled, disabled: 0 },
    buckets: { expected: enabled * 288, complete: enabled * 288, partial: 0, missing: 0 },
    events: enabled ? 1000 : null,
    mean_eps: enabled ? 1 : null,
    expected_eps: enabled ? 1 : null,
    baseline_sources: enabled,
    baseline_share: enabled ? 1 : null,
    open_incidents: {},
    top_sources: [],
    outage_mode: "ALERT",
    outage_overrides: 0,
    monitored_sources: enabled,
    ...over,
  };
}

export function summary(
  classes: OutageClassSummary[],
  bucket_seconds = 300,
  over: Partial<OutageFleetSummary> = {},
): OutageFleetSummary {
  return {
    window: "24h",
    window_start: "2026-10-12T09:00:00Z",
    window_end: "2026-10-13T09:00:00Z",
    bucket_seconds,
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

export function row(over: Partial<OutagePolicyRow> = {}): OutagePolicyRow {
  return {
    id: "0b0b0000-0000-4000-8000-000000000001",
    instance_id: null,
    scope_kind: "CLASS",
    scope_value: "FIREWALL",
    mode: "RECORD_ONLY",
    silence_grace_buckets: null,
    updated_by: "s.okafor",
    created_at: "2026-10-09T14:22:10Z",
    updated_at: "2026-10-09T14:22:10Z",
    governed_monitored_sources: 12,
    source_name: null,
    source_class: null,
    instance_name: null,
    ...over,
  };
}

export function sourceRow(over: Partial<OutagePolicyRow> = {}): OutagePolicyRow {
  return row({
    id: "0b0b0000-0000-4000-8000-000000000002",
    scope_kind: "SOURCE",
    scope_value: FW_02,
    mode: "ALERT",
    governed_monitored_sources: null,
    source_name: "PaSeries @ pa-fw-02",
    source_class: "FIREWALL",
    ...over,
  });
}

export function fleetSource(over: Partial<FleetSource> = {}): FleetSource {
  return {
    id: FW_02,
    instance_id: "c0ffee00-0000-4000-8000-000000000001",
    qradar_id: 102,
    name: "PaSeries @ pa-fw-02",
    type_name: "Palo Alto PA Series",
    enabled: true,
    auto_discovered: false,
    first_seen_at: "2026-09-01T08:00:00Z",
    source_class: "FIREWALL",
    basis: "TYPE",
    rule_id: null,
    override_class: null,
    override_by: null,
    override_at: null,
    conflicts: [],
    classified_at: "2026-10-01T08:00:00Z",
    review_status: "REVIEWED",
    reviewed_at: null,
    reviewed_by: null,
    ...over,
  };
}

/** Twelve monitored Firewall sources, two overrides, one Linux class, one default class. */
export const SUMMARY = summary([
  classSummary("FIREWALL", 14, {
    outage_mode: "RECORD_ONLY",
    outage_overrides: 2,
    monitored_sources: 12,
  }),
  classSummary("LINUX", 8),
  classSummary("PROXY", 3),
  classSummary("DNS", 0),
]);
