import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  type ScheduledSearch,
  type SearchExecution,
  type SearchResultTrend,
} from "@/lib/api";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { SearchResultsWidget } from "./SearchResultsWidget";

vi.mock("echarts/core", () => ({
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
  use: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ LineChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

class TestResizeObserver {
  observe() {}
  disconnect() {}
}

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1", name: "Repeated failures", description: null, owner: null, category: null,
    mitre_techniques: [], aql_query: "SELECT 1", query_version: 1, schedule_cron: "0 * * * *",
    schedule_timezone: "UTC", severity: "MEDIUM", threshold_value: null, threshold_operator: "GT",
    timeout_seconds: 300, max_time_range_hours: 24, max_result_rows: 1000,
    visualization_type: "LINE", enabled: true, last_run_at: "2026-07-20T10:00:00Z",
    next_run_at: "2026-07-20T11:00:00Z", consecutive_failures: 0, ...over,
  };
}

const runtime: WidgetRuntime = {
  rangeOptions: null,
  override: null,
  dashboardRange: "24h",
  setRange: () => undefined,
  onExpand: null,
  expanded: false,
  onConfigure: () => undefined,
};

function renderWidget(config: { search_id: string | null; metric_key: string | null }) {
  return render(
    <WidgetRuntimeContext.Provider value={runtime}>
      <SearchResultsWidget id="w_search1" config={config} range={null} expanded={false} />
    </WidgetRuntimeContext.Provider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  vi.spyOn(api, "search").mockResolvedValue(search());
  vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
  vi.spyOn(api, "searchVersions").mockResolvedValue([
    { version: 1, aql_query: "SELECT 1", changed_by: null, change_note: null, created_at: "2026-07-01T00:00:00Z" },
  ]);
  vi.spyOn(api, "searchMetricKeys").mockResolvedValue({
    search_id: "s-1", total: 0, count: 0, keys: [],
  });
  vi.spyOn(api, "searchResults").mockResolvedValue({
    search_id: "s-1", metric_key: "total", threshold_value: null, threshold_operator: "GT",
    count: 0, points: [],
  });
  vi.spyOn(api, "searchLatestGroups").mockResolvedValue({
    search_id: "s-1", execution_id: null, started_at: null, status: null,
    truncated: false, values_are_lower_bounds: false, empty: "no_execution", total: 0, rows: [],
  });
});

describe("SearchResultsWidget", () => {
  it("asks for a search and offers Configure when none is set", () => {
    renderWidget({ search_id: null, metric_key: null });
    expect(screen.getByText("Choose a search.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Configure" })).toBeInTheDocument();
    expect(document.querySelector("[data-component='result-trend']")).toBeNull();
  });

  it("says the search was deleted and offers Configure", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(404, "search not found"));
    renderWidget({ search_id: "missing", metric_key: "total" });
    expect(await screen.findByText("This search was deleted.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Configure" })).toBeInTheDocument();
  });

  it("mounts ResultTrend for a search that exists", async () => {
    renderWidget({ search_id: "s-1", metric_key: "total" });
    await waitFor(() =>
      expect(document.querySelector("[data-component='result-trend']")).not.toBeNull(),
    );
    expect(screen.getByRole("link", { name: "Search" })).toHaveAttribute("href", "/searches/s-1");
  });

  it("names read:searches when the search read is refused", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(403, "no"));
    renderWidget({ search_id: "s-1", metric_key: null });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("read:searches");
    expect(alert).toHaveTextContent(/administrator/i);
  });

  it("passes the widget body's height to the chart", async () => {
    const execution: SearchExecution = {
      id: "e-1", search_id: "s-1", query_version: 1, status: "COMPLETED",
      trigger: "SCHEDULED", triggered_by: null, ariel_search_id: null, ariel_status: null,
      started_at: "2026-07-20T10:00:00Z", completed_at: "2026-07-20T10:00:20Z",
      duration_ms: 1200, result_count: 12, truncated: false, error_type: null,
      error_message: null, retry_count: 0, threshold_breached: false,
    };
    const trend: SearchResultTrend = {
      search_id: "s-1", metric_key: "total", threshold_value: null, threshold_operator: "GT",
      count: 1,
      points: [{
        bucket_start: "2026-07-20T10:00:00Z", metric_key: "total", value: 12, dimensions: {},
        execution_id: "e-1", execution_status: "COMPLETED", duration_ms: 1200, result_count: 12,
        threshold_breached: false, query_version: 1, query_version_id: "v-1",
      }],
    };
    vi.spyOn(api, "searchExecutions").mockResolvedValue([execution]);
    vi.spyOn(api, "searchResults").mockResolvedValue(trend);
    renderWidget({ search_id: "s-1", metric_key: "total" });

    const chart = await screen.findByRole("img", { name: "Search result trend" });
    const body = chart.closest(".tw-body-fill");
    expect(body).not.toBeNull();
    expect(chart.closest(".sr-widget")).not.toBeNull();
    expect(body?.contains(chart)).toBe(true);

    const css = readFileSync(resolve(__dirname, "../app/searches/[id]/search-detail.css"), "utf8");
    const root = css.match(/\.sr-widget \{[^}]+\}/)?.[0] ?? "";
    const widgetChart = css.match(/\.sr-widget \.chart \{[^}]+\}/)?.[0] ?? "";
    expect(root).toContain("flex: 1 1 auto");
    expect(root).toContain("min-height: 0");
    expect(widgetChart).toContain("flex: 1 1 var(--home-chart-h)");
    expect(widgetChart).toContain("height: auto");
    expect(widgetChart).toContain("min-height: var(--home-chart-h)");
    expect(widgetChart).not.toMatch(/\n\s*height:\s*var\(--home-chart-h\)/);

    const globals = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");
    expect(globals).toMatch(/\.chart \{\s*width: 100%;\s*height: 320px;\s*\}/);
  });
});
