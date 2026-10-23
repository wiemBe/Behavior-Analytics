// "Data to" on the polling pages (UI-7.2): each page tells the top bar how far its data reaches,
// from a field it already reads, or null when its responses carry none.

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, fleetApi, type RuleSummary } from "@/lib/api";
import { analytics, anomaly, behavior, fleet, offenseRow, page, summary } from "@/lib/home/fixtures";

import AnomaliesPage from "./anomalies/page";
import BehaviorPage from "./behavior/page";
import FleetPage from "./fleet/page";
import OffensesPage from "./offenses/page";
import RulesPage from "./rules/page";

const reported = vi.hoisted(() => ({ calls: [] as Array<string | null> }));
vi.mock("@/components/shell/AutoRefresh", () => ({
  useReportDataAge: (iso: string | null) => {
    reported.calls.push(iso);
  },
}));

const last = () => reported.calls.at(-1);
const sources = [
  behavior({ log_source_id: "a", last_bucket_at: "2026-10-22T10:00:00Z" }),
  behavior({ log_source_id: "b", last_bucket_at: "2026-10-22T11:35:00Z" }),
  behavior({ log_source_id: "c", last_bucket_at: null }),
];
const rule = (over: Partial<RuleSummary>): RuleSummary => ({
  id: "r",
  qradar_id: 1,
  name: "rule",
  enabled: true,
  is_building_block: false,
  rule_type: "EVENT",
  origin: "USER",
  owner: null,
  health_status: "HEALTHY",
  health_evaluated_at: null,
  health_reason: null,
  last_fired_at: null,
  offense_contribution_count: 0,
  event_contribution_count: 0,
  mitre_techniques: [],
  ...over,
});

beforeEach(() => {
  reported.calls = [];
  vi.restoreAllMocks();
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue(sources);
  vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary({ monitored_sources: 3 }));
  vi.spyOn(api, "anomalies").mockResolvedValue({ ...page([anomaly()]), limit: 25 });
  vi.spyOn(fleetApi, "summary").mockResolvedValue(fleet([]));
  vi.spyOn(api, "offenses").mockResolvedValue({ ...page([offenseRow()]), limit: 25 });
  vi.spyOn(api, "offenseAggregates").mockResolvedValue(analytics().aggregates);
  vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([]);
  vi.spyOn(api, "rules").mockResolvedValue({
    items: [
      rule({ id: "1", health_evaluated_at: "2026-10-22T09:00:00Z" }),
      rule({ id: "2", health_evaluated_at: "2026-10-22T11:30:00Z" }),
      rule({ id: "3", health_evaluated_at: null }),
    ],
    total: 3,
    limit: 25,
    offset: 0,
  });
});

describe("Data to on the polling pages", () => {
  it("/behavior reports the newest last bucket of its sources", async () => {
    render(await BehaviorPage());
    expect(last()).toBe("2026-10-22T11:35:00.000Z");
  });

  it("/behavior reports nothing when no source has a bucket", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([behavior({ last_bucket_at: null })]);
    render(await BehaviorPage());
    expect(last()).toBeNull();
  });

  it("/anomalies reports the Phase A reach only when the list is pinned to log sources", async () => {
    render(await AnomaliesPage({ searchParams: Promise.resolve({ subject_kind: "LOG_SOURCE" }) }));
    expect(last()).toBe("2026-10-22T11:35:00.000Z");
    reported.calls = [];
    // An unfiltered list also holds WAF, IPS and watch rows that this field does not describe.
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(last()).toBeNull();
    reported.calls = [];
    render(await AnomaliesPage({ searchParams: Promise.resolve({ subject_kind: "WAF_ENDPOINT" }) }));
    expect(last()).toBeNull();
  });

  it("/rules reports the newest health evaluation on the page", async () => {
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(last()).toBe("2026-10-22T11:30:00.000Z");
  });

  it("/fleet reports null: its window_end is the request's own now", async () => {
    render(await FleetPage({ searchParams: Promise.resolve({}) }));
    expect(reported.calls.length).toBeGreaterThan(0);
    expect(reported.calls.every((c) => c === null)).toBe(true);
  });

  it("/offenses reports null: no read says how far the snapshots reach", async () => {
    render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(reported.calls.length).toBeGreaterThan(0);
    expect(reported.calls.every((c) => c === null)).toBe(true);
  });
});
