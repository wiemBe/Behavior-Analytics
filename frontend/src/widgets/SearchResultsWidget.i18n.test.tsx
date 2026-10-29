// chart.search_results in Turkish (D-37): the states the widget itself words. The trend inside
// it is ResultTrend's, outside this wave. The harness is the English test's.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  type ScheduledSearch,
} from "@/lib/api";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { setTestLocale } from "../../vitest.setup";

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
  setTestLocale("tr");
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

describe("SearchResultsWidget in Turkish", () => {
  it("asks for a search, offers Configure, and links to the list", () => {
    renderWidget({ search_id: null, metric_key: null });
    expect(screen.getByText("Bir arama seçin.")).toBeInTheDocument();
    expect(screen.getByText(/Bu bileşen için arama ayarlanmadı/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Arama sonuçları" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Arama" })).toHaveAttribute("href", "/searches");
  });

  it("says the search was deleted, in Turkish", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(404, "search not found"));
    renderWidget({ search_id: "missing", metric_key: "total" });
    expect(await screen.findByText("Bu arama silindi.")).toBeInTheDocument();
    expect(screen.getByText(/Bu bileşen için ayarlanan arama artık yok\./)).toBeInTheDocument();
  });

  it("says loading while the search is read", () => {
    vi.spyOn(api, "search").mockReturnValue(new Promise(() => {}));
    renderWidget({ search_id: "s-1", metric_key: "total" });
    expect(screen.getByText("Sonuç eğilimi yükleniyor…")).toBeInTheDocument();
  });

  it("names the search and its window when it exists", async () => {
    renderWidget({ search_id: "s-1", metric_key: "total" });
    expect(await screen.findByRole("heading", { name: /Repeated failures/ })).toBeInTheDocument();
    expect(screen.getByText("24 sa")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Arama" })).toHaveAttribute("href", "/searches/s-1");
  });

  it("says forbidden with the permission and who grants it", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(403, "no"));
    renderWidget({ search_id: "s-1", metric_key: null });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/izin: read:searches/)).toBeInTheDocument();
    expect(alert).toHaveTextContent("Bu izni bir yönetici verir.");
  });
});
