import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuleDetailPage from "./page";
import { CAPACITY_UNIT } from "@/components/rules/CapacityHistory";
import {
  api,
  ruleStatisticsApi,
  type CapacitySample,
  type RuleDetail,
} from "@/lib/api";

function rule(): RuleDetail {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    qradar_id: 77,
    name: "Multiple login failures from a single source",
    enabled: true,
    is_building_block: false,
    rule_type: "EVENT",
    origin: "USER",
    owner: "SOC",
    health_status: "HEALTHY",
    health_evaluated_at: "2026-10-13T09:00:00Z",
    last_fired_at: "2026-10-13T08:45:00Z",
    offense_contribution_count: 4,
    event_contribution_count: 120,
    mitre_techniques: ["T1110"],
    description: null,
    categories: [],
    average_capacity: 0.75,
    generates_offense: true,
    response_actions: [],
    qradar_created_at: null,
    qradar_modified_at: null,
    first_seen_at: "2026-09-01T00:00:00Z",
    soc_notes: null,
    expected_daily_firings: null,
    health_monitoring_enabled: true,
    dependencies: [],
  };
}

const samples: CapacitySample[] = [
  {
    capacity_timestamp: "2026-10-12T09:00:00Z",
    average_capacity: 0.75,
    base_capacity: 1.25,
    base_host_id: 53,
    recorded_at: "2026-10-12T09:05:00Z",
  },
  {
    capacity_timestamp: "2026-09-30T09:00:00Z",
    average_capacity: null,
    base_capacity: null,
    base_host_id: null,
    recorded_at: "2026-09-30T09:05:00Z",
  },
];

async function renderPage() {
  return render(
    await RuleDetailPage({
      params: Promise.resolve({ id: "11111111-1111-1111-1111-111111111111" }),
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "rule").mockResolvedValue(rule());
  vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([]);
});

describe("rule capacity history", () => {
  it("shows an honest empty state", async () => {
    vi.spyOn(ruleStatisticsApi, "capacityHistory").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("QRadar has not re-benchmarked this rule since collection began.")).toBeInTheDocument();
    expect(screen.getByText(/This is not an error/)).toBeInTheDocument();
  });

  it("shows newest-first samples with UTC, units and host id", async () => {
    vi.spyOn(ruleStatisticsApi, "capacityHistory").mockResolvedValue(samples);
    await renderPage();
    const table = screen.getByRole("table", { name: /Capacity benchmarks for this rule/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("2026-10-12 09:00:00 UTC");
    expect(rows[0]).toHaveTextContent("0.75");
    expect(rows[0]).toHaveTextContent("1.25");
    expect(rows[0]).toHaveTextContent("53");
    expect(rows[1]).toHaveTextContent("—");
    const section = screen.getByRole("heading", { name: "Capacity history" }).closest("section")!;
    expect(section.textContent?.split(CAPACITY_UNIT)).toHaveLength(2);
    expect(within(table).getByRole("columnheader", { name: "Average capacity" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Base capacity" })).toBeInTheDocument();
    expect(screen.getByText(/lower figure is costlier/i)).toBeInTheDocument();
    expect(screen.getByText(/not a firing rate/i)).toBeInTheDocument();
  });

  it("keeps the rule visible and explains a failed history request", async () => {
    vi.spyOn(ruleStatisticsApi, "capacityHistory").mockRejectedValue(new Error("down"));
    await renderPage();
    expect(screen.getByRole("heading", { name: rule().name })).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("capacity history could not be loaded");
    expect(alert).toHaveTextContent(/reload the page/i);
    expect(alert).not.toHaveTextContent("down");
  });
});
