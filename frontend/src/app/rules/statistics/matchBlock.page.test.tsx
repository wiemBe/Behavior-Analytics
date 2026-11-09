import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuleStatisticsPage from "./page";
import { block, row, span, SPAN_REASON } from "@/components/rules/ruleMatchFixtures";
import {
  ApiError,
  ruleMatchApi,
  ruleStatisticsApi,
  type RuleStatistics,
} from "@/lib/api";


function statistics(over: Partial<RuleStatistics> = {}): RuleStatistics {
  return {
    instance_id: "11111111-1111-1111-1111-111111111111",
    counts: [
      { enabled: true, rule_type: "EVENT", origin: "SYSTEM", is_building_block: false, count: 120 },
      { enabled: false, rule_type: "EVENT", origin: "SYSTEM", is_building_block: false, count: 8 },
      { enabled: true, rule_type: null, origin: null, is_building_block: false, count: 1 },
      { enabled: true, rule_type: "BUILDINGBLOCK", origin: "USER", is_building_block: true, count: 219 },
      { enabled: true, rule_type: "COMMON", origin: "OVERRIDE", is_building_block: false, count: 4 },
    ],
    window: {
      days: 7,
      span_start: "2026-10-06T10:00:00Z",
      span_end: "2026-10-13T10:00:00Z",
      span_seconds: 604800,
      window_seconds: 3600,
      expected_windows: 168,
      complete_windows: 168,
      truncated_windows: 0,
      failed_windows: 0,
      missing_windows: 0,
      measured_seconds: 604800,
      complete: true,
      reason: null,
    },
    silent: {
      items: [
        {
          rule_id: "22222222-2222-2222-2222-222222222222",
          qradar_id: 202,
          name: "Suspicious authentication sequence",
          rule_type: "EVENT",
          origin: "USER",
          is_building_block: false,
          first_seen_at: "2026-10-12T08:00:00Z",
          last_fired_at: null,
        },
      ],
      total: 1,
      items_truncated: false,
      excluded: [{ rule_type: "FLOW", evidence: "NOT_MEASURED", count: 12 }],
      excluded_count: 12,
      excluded_reason: "FLOW rules are not searched, so their silence is unknown.",
    },
    silent_reason: null,
    top_by_events: [
      {
        rule_id: "33333333-3333-3333-3333-333333333333",
        qradar_id: 303,
        name: "Multiple login failures from a single source",
        enabled: true,
        rule_type: "COMMON",
        is_building_block: false,
        match_records: 8123,
        matched_events: 18420,
        match_rate_per_second: 0.03046,
        lower_bound: true,
      },
    ],
    top_by_matches: [],
    top_reason: null,
    costliest_by_benchmark: {
      label: "QRadar capacity benchmark: how fast the rule could be evaluated. Lower is costlier to evaluate. This is not a firing rate.",
      unit: "millions of events per second (QRadar's unit)",
      items: [
        {
          rule_id: "44444444-4444-4444-4444-444444444444",
          qradar_id: 404,
          name: "Excessive firewall denies",
          rule_type: "EVENT",
          is_building_block: false,
          average_capacity: 0.037,
        },
      ],
      no_benchmark_count: 23,
    },
    ...over,
  };
}

async function renderPage(params: Record<string, string> = {}) {
  return render(await RuleStatisticsPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
});

const FOUR_HEADED = ["Rule composition", "Most matched", "Silent enabled rules", "Costliest by benchmark"];

function headings() {
  return screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
}

describe("the rule-match block on /rules/statistics", () => {
  it("sits under the silent-rules block and asks for the page's span", async () => {
    const read = vi.spyOn(ruleMatchApi, "anomalies").mockResolvedValue(block());
    await renderPage({ days: "30" });
    expect(read).toHaveBeenCalledWith(30);
    const list = headings();
    const silent = list.indexOf("Silent enabled rules");
    expect(list[silent + 1]).toBe("Rule-match anomalies");
    expect(
      within(screen.getByRole("region", { name: "Rule-match anomalies" })).getByRole("table"),
    ).toBeInTheDocument();
  });

  it("leaves the existing blocks' call unchanged", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockResolvedValue(block());
    await renderPage();
    expect(ruleStatisticsApi.statistics).toHaveBeenCalledWith(7);
    expect(ruleStatisticsApi.statistics).toHaveBeenCalledTimes(1);
  });

  it("its own failure says so inside the block and leaves the five blocks standing", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    const region = screen.getByRole("region", { name: "Rule-match anomalies" });
    expect(within(region).getByRole("alert")).toHaveTextContent("Match anomalies could not be loaded.");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText("Rule statistics could not be loaded")).toBeNull();
    const list = headings();
    for (const name of FOUR_HEADED) expect(list).toContain(name);
    expect(screen.getByRole("region", { name: "Measurement window" })).toBeInTheDocument();
    expect(region).not.toHaveTextContent("secret");
  });

  it("shows NOT_COLLECTED without a table while the other blocks render", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockResolvedValue(
      block({ state: "NOT_COLLECTED", span: null, items: null, total: null, reason: "No match window has been recorded." }),
    );
    await renderPage();
    const region = screen.getByRole("region", { name: "Rule-match anomalies" });
    expect(region).toHaveTextContent("Rule-match collection has not run for this span.");
    expect(within(region).queryByRole("table")).toBeNull();
  });

  it("shows INSUFFICIENT_DATA with the same window counts the page's own reason uses", async () => {
    vi.spyOn(ruleMatchApi, "anomalies").mockResolvedValue(
      block({
        state: "INSUFFICIENT_DATA",
        span: span({ complete: false, window_status: "incomplete", reason: SPAN_REASON }),
        reason: SPAN_REASON,
        items: [row({ window_status: "incomplete" })],
      }),
    );
    await renderPage();
    const region = screen.getByRole("region", { name: "Rule-match anomalies" });
    expect(region).toHaveTextContent(SPAN_REASON);
    expect(region).toHaveTextContent("Span incomplete");
  });

  it("does not show the block when the statistics themselves have no instance", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockRejectedValue(new ApiError(404, "no instance"));
    vi.spyOn(ruleMatchApi, "anomalies").mockRejectedValue(new ApiError(404, "no instance"));
    await renderPage();
    expect(screen.queryByRole("region", { name: "Rule-match anomalies" })).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("No QRadar instance is registered");
  });
});
