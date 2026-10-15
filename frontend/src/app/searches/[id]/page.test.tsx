import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SearchDetailPage from "./page";
import { ApiError, api, type ScheduledSearch, type SearchExecution, type SearchVersion } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - any of the three reads fails: the "could not be loaded" alert with a recovery line;
//  - the normal detail: the page header (name + category/severity/version/next run),
//    the Run-now control, the Schedule / Current AQL / Execution history /
//    Query version history / Result trend sections, and the stored AQL;
//  - no executions yet: the execution-history empty notice;
//  - a truncated run: its count is a lower bound ("at least 500"), its threshold reads
//    "no verdict", and the explanatory notes appear — a lower bound is never rendered
//    as a measured number;
//  - honesty: a null category reads "uncategorised", a null next-run time is omitted,
//    and null version fields are em dashes with a compact UTC time.
// The page's own components (ScheduleControl, RunSearchButton, ResultTrend) are
// rendered as-is; only `next/navigation` and ECharts are stubbed, and the trend's read
// is mocked to its empty state.

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

const setOption = vi.fn();
const init = vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() }));
vi.mock("echarts/core", () => ({ init: (...args: unknown[]) => init(...(args as [])), use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1",
    name: "Repeated authentication failures",
    description: "Counts failures per source",
    owner: "SOC",
    category: "Credential Access",
    mitre_techniques: ["T1110"],
    aql_query: 'SELECT count(*) FROM events WHERE username IS NOT NULL',
    query_version: 2,
    schedule_cron: "0 */4 * * *",
    severity: "HIGH",
    threshold_value: 10,
    threshold_operator: "GT",
    timeout_seconds: 300,
    max_time_range_hours: 24,
    max_result_rows: 500,
    visualization_type: "table",
    enabled: true,
    last_run_at: "2026-10-15T04:00:00Z",
    next_run_at: "2026-10-15T08:00:00Z",
    consecutive_failures: 0,
    ...over,
  };
}

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1",
    search_id: "s-1",
    query_version: 2,
    status: "COMPLETED",
    trigger: "SCHEDULED",
    triggered_by: null,
    ariel_search_id: null,
    ariel_status: null,
    started_at: "2026-10-15T04:00:00Z",
    completed_at: "2026-10-15T04:00:41Z",
    duration_ms: 41200,
    result_count: 500,
    truncated: true,
    error_type: null,
    error_message: null,
    retry_count: 0,
    threshold_breached: false,
    ...over,
  };
}

const versions: SearchVersion[] = [
  {
    version: 1,
    aql_query: 'SELECT count(*) FROM events',
    changed_by: null,
    change_note: null,
    created_at: "2026-10-15T04:00:00Z",
  },
];

async function renderPage(id = "s-1") {
  return render(await SearchDetailPage({ params: Promise.resolve({ id }) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "search").mockResolvedValue(search());
  vi.spyOn(api, "searchVersions").mockResolvedValue(versions);
  vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
  vi.spyOn(api, "searchResults").mockResolvedValue({
    search_id: "s-1",
    metric_key: "total",
    threshold_value: null,
    threshold_operator: "GT",
    count: 0,
    points: [],
  });
});

describe("search detail states", () => {
  it("explains a failed read and how to recover", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage("s-9");
    expect(screen.getByRole("heading", { name: "Search detail", level: 1 })).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Search s-9 could not be loaded.");
    expect(alert).toHaveTextContent(/Reload the page to try again\./);
    expect(alert).not.toHaveTextContent("secret");
  });

  it("says the search has not run yet", async () => {
    await renderPage();
    expect(
      screen.getByText(/No executions yet\. The first appears after this search runs/),
    ).toBeInTheDocument();
  });
});

describe("search detail contents", () => {
  it("names the search and orients the reader under it", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { name: "Repeated authentication failures", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Credential Access · severity HIGH · v2 · next run 2026-10-15 08:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeInTheDocument();
  });

  it("orders the sections and shows the stored AQL", async () => {
    await renderPage();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Schedule",
      "Current AQL (v2)",
      "Execution history",
      "Query version history",
      "Result trend",
    ]);
    expect(
      screen.getByText('SELECT count(*) FROM events WHERE username IS NOT NULL'),
    ).toBeInTheDocument();
    expect(screen.getByText("Every 4 h", { selector: "strong" })).toBeInTheDocument();
  });

  it("lists the query versions with compact UTC times", async () => {
    await renderPage();
    // Only the version table exists: the execution history is empty in this test.
    const table = screen.getByRole("table");
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("v1");
    expect(within(row).getAllByText("—")).toHaveLength(2);
    expect(row).toHaveTextContent("10-15 04:00 UTC");
  });

  it("omits the next run and names an uncategorised search", async () => {
    vi.spyOn(api, "search").mockResolvedValue(search({ category: null, next_run_at: null }));
    await renderPage();
    expect(screen.getByText(/uncategorised · severity HIGH · v2$/)).toBeInTheDocument();
    expect(screen.queryByText(/next run/)).toBeNull();
  });
});

describe("search execution history", () => {
  it("shows a truncated run as a lower bound with no verdict", async () => {
    vi.spyOn(api, "searchExecutions").mockResolvedValue([execution()]);
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByRole("columnheader", { name: "Started (UTC)" })).toBeInTheDocument();
    expect(within(table).getByText(/Times are UTC/)).toBeInTheDocument();
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("10-15 04:00 UTC");
    expect(row).toHaveTextContent("41.2 s");
    expect(within(row).getByText(/at least/)).toBeInTheDocument();
    expect(row).toHaveTextContent("500");
    expect(within(row).getByText("no verdict")).toBeInTheDocument();
    // The lower-bound provenance is stated, not left to the reader.
    const notes = screen.getByRole("list", { name: "Truncated runs" });
    expect(notes).toHaveTextContent(/lower bound/);
    expect(notes).toHaveTextContent(/may still be above it/);
  });
});
