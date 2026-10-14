import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuleStatisticsPage from "./page";
import Loading from "./loading";
import RuleStatisticsError from "./error";
import {
  ApiError,
  ruleStatisticsApi,
  type RuleStatistics,
} from "@/lib/api";

const REASON = "Only 167 of 168 windows in the span are complete (0 missing, 1 truncated, 0 failed).";

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

beforeEach(() => vi.restoreAllMocks());

describe("rule statistics route states", () => {
  it("announces loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading rule statistics");
  });

  it("explains a request failure and what to do", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Rule statistics could not be loaded");
    expect(alert).toHaveTextContent(/check that it is running, then reload/i);
    expect(alert).not.toHaveTextContent("secret");
  });

  it("has a rendering error state with a retry", () => {
    render(
      <RuleStatisticsError
        error={Object.assign(new Error("secret stack"), { digest: "ref-62" })}
        reset={() => {}}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Rule statistics could not be displayed");
    expect(alert).toHaveTextContent("ref-62");
    expect(alert).not.toHaveTextContent("secret stack");
  });

  it("treats no registered instance as an actionable empty state", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockRejectedValue(
      new ApiError(404, "no instance registered"),
    );
    await renderPage();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("No QRadar instance is registered");
    expect(status).toHaveTextContent(/register the QRadar instance/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("measurement honesty", () => {
  it("shows the complete measured span and window count", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
    await renderPage();
    const region = screen.getByRole("region", { name: "Measurement window" });
    expect(region).toHaveTextContent("Complete");
    expect(region).toHaveTextContent("7 days");
    expect(region).toHaveTextContent("168 of 168 windows complete");
    expect(region).toHaveTextContent("Times are UTC");
  });

  it("shows PARTIAL and carries the API reason verbatim", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({
        window: {
          ...statistics().window,
          complete: false,
          complete_windows: 167,
          truncated_windows: 1,
          measured_seconds: 601200,
          reason: REASON,
        },
      }),
    );
    await renderPage();
    const region = screen.getByRole("region", { name: "Measurement window" });
    expect(region).toHaveTextContent("PARTIAL");
    expect(region).toHaveTextContent(REASON);
    expect(region.textContent?.match(/167 of 168/g)).toHaveLength(1);
    expect(region.textContent?.match(/1 truncated/g)).toHaveLength(1);
  });

  it("shows INSUFFICIENT_DATA when no complete window exists", async () => {
    const reason = "No rule-match window has been collected for this instance.";
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({
        window: {
          ...statistics().window,
          complete: false,
          complete_windows: 0,
          missing_windows: 168,
          measured_seconds: 0,
          reason,
        },
        silent: null,
        silent_reason: reason,
        top_by_events: null,
        top_by_matches: null,
        top_reason: reason,
      }),
    );
    await renderPage();
    expect(screen.getByRole("region", { name: "Measurement window" })).toHaveTextContent(
      "INSUFFICIENT_DATA",
    );
    expect(screen.getAllByText(reason)).toHaveLength(2);
    expect(screen.queryByText(/0 silent rules/i)).toBeNull();
  });
});

describe("statistics sections", () => {
  it("renders unknown type and origin explicitly", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
    await renderPage();
    expect(screen.getByRole("rowheader", { name: "Unknown type" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Unknown origin" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Override" })).toBeInTheDocument();
  });

  it("distinguishes an unmeasured silent section from a measured zero", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({ silent: null, silent_reason: "The span is incomplete." }),
    );
    await renderPage();
    expect(screen.getByText("Silence was not judged.")).toBeInTheDocument();
    expect(screen.getByText("The span is incomplete.")).toBeInTheDocument();
    expect(screen.queryByText(/0 silent rules/i)).toBeNull();
    expect(screen.queryByText(/reported as active/i)).toBeNull();
  });

  it("shows a truncated silent list and its excluded rules", async () => {
    const value = statistics();
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({
        counts: [
          {
            enabled: true,
            rule_type: "EVENT",
            origin: "SYSTEM",
            is_building_block: false,
            count: 620,
          },
        ],
        silent: {
          ...value.silent!,
          total: 550,
          items_truncated: true,
          items: Array.from({ length: 500 }, (_, i) => ({
            ...value.silent!.items[0],
            rule_id: `silent-${i}`,
            name: `Silent rule ${i + 1}`,
          })),
        },
      }),
    );
    await renderPage();
    expect(screen.getByText(/Showing 500 of 550 enabled rules/)).toBeInTheDocument();
    expect(screen.getByText(/12 enabled rules were not judged/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /not judged/i })).toHaveTextContent("Flow");
  });

  it("renders a measured zero silent count as zero", async () => {
    const value = statistics();
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({ silent: { ...value.silent!, items: [], total: 0 } }),
    );
    await renderPage();
    expect(screen.getByText(/No judged enabled rule was silent in the measured windows/)).toBeInTheDocument();
  });

  it("distinguishes an unmeasured top list from an empty measured list", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({ top_by_events: null, top_reason: "No complete match window exists." }),
    );
    await renderPage();
    expect(screen.getByText("No complete match window exists.")).toBeInTheDocument();
    expect(screen.queryByText("Matches were not measured.")).toBeNull();
    expect(screen.queryByText("No rule matched in the measured windows.")).toBeNull();
  });

  it("keeps a partial top list and puts its reason directly above the table", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({
        window: {
          ...statistics().window,
          complete: false,
          complete_windows: 167,
          truncated_windows: 1,
          measured_seconds: 601200,
          reason: REASON,
        },
        silent: null,
        silent_reason: REASON,
        top_reason: REASON,
      }),
    );
    await renderPage();
    const table = screen.getByRole("table", { name: /Rules ranked by matched events/ });
    const reason = screen.getByText(REASON, { selector: ".stats-top-reason" });
    expect(table).toBeInTheDocument();
    expect(reason.nextElementSibling).toBe(table.closest(".table-scroll"));
  });

  it("uses mutually exclusive wide columns and narrow sublines", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
    await renderPage();
    const most = screen.getByRole("table", { name: /Rules ranked by matched events/ });
    const mostRow = within(most).getByRole("rowheader", {
      name: /Multiple login failures/,
    }).closest("tr")!;
    expect(mostRow.querySelectorAll(".stats-wide-fact")).toHaveLength(2);
    expect(mostRow.querySelectorAll(".stats-narrow-fact")).toHaveLength(1);
    expect(mostRow.querySelector(".stats-narrow-fact")).not.toHaveTextContent("lower bound");

    const silent = screen.getByRole("table", { name: /Enabled rules with a measured zero/ });
    const silentRow = within(silent).getByRole("rowheader", {
      name: /Suspicious authentication sequence/,
    }).closest("tr")!;
    expect(silentRow.querySelectorAll(".stats-wide-fact")).toHaveLength(1);
    expect(silentRow.querySelectorAll(".stats-narrow-fact")).toHaveLength(1);
  });

  it("labels an untyped building-block listing consistently", async () => {
    const value = statistics();
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(
      statistics({
        top_by_events: [
          {
            ...value.top_by_events![0],
            rule_type: "BUILDINGBLOCK",
            is_building_block: true,
          },
        ],
        silent: {
          ...value.silent!,
          excluded: [{ rule_type: "BUILDINGBLOCK", evidence: "NOT_MEASURED", count: 12 }],
        },
      }),
    );
    await renderPage();
    const labels = screen.getAllByText("Type not reported");
    expect(labels).toHaveLength(3);
    for (const label of labels) {
      expect(label).toHaveAttribute(
        "title",
        "QRadar's building-block list does not say whether the block tests events or flows.",
      );
    }
  });

  it("shows every load column and explains lower-bound rows", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
    await renderPage();
    const table = screen.getByRole("table", { name: /Rules ranked by matched events/ });
    for (const heading of [
      "Rule",
      "Type",
      "Building block",
      "Matched events",
      "Match records",
      "Match rate (events/s)",
    ]) {
      expect(within(table).getByRole("columnheader", { name: heading })).toBeInTheDocument();
    }
    expect(within(table).getAllByText(/≥/)).toHaveLength(3);
    expect(screen.getByText(/Flow matches are not searched/)).toBeInTheDocument();
  });

  it("uses the API's benchmark label and unit and reports missing benchmarks", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
    await renderPage();
    expect(screen.getByText(/Lower is costlier to evaluate/)).toBeInTheDocument();
    expect(screen.getByText(/This is not a firing rate/)).toBeInTheDocument();
    expect(screen.getByText("millions of events per second (QRadar's unit)")).toBeInTheDocument();
    expect(screen.getByText("23 enabled rules have no benchmark and are left out of this ranking.")).toBeInTheDocument();
  });

  it("writes the day selection to the URL while preserving other selections", async () => {
    vi.spyOn(ruleStatisticsApi, "statistics").mockResolvedValue(statistics());
    await renderPage({ rank: "matches", origin: "SYSTEM" });
    expect(screen.getByRole("link", { name: "1 day" })).toHaveAttribute(
      "href",
      "/rules/statistics?days=1&rank=matches&origin=SYSTEM",
    );
    expect(screen.getByRole("link", { name: "30 days" })).toHaveAttribute(
      "href",
      "/rules/statistics?days=30&rank=matches&origin=SYSTEM",
    );
  });
});
