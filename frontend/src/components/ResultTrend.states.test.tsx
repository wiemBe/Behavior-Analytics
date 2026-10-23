import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResultTrend, scheduleIntervalMs, staleness } from "./ResultTrend";
import { ApiError, api, type SearchExecution, type SearchResultTrend } from "@/lib/api";
import { isReadField } from "@/widgets/types";
import { widgetDef } from "@/widgets/registry";

const setOption = vi.fn();
const init = vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() }));

vi.mock("echarts/core", () => ({
  init: (...args: unknown[]) => init(...(args as [])),
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

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1", search_id: "s-1", query_version: 1, status: "COMPLETED",
    trigger: "SCHEDULED", triggered_by: null, ariel_search_id: null, ariel_status: null,
    started_at: "2026-07-20T10:00:00Z", completed_at: "2026-07-20T10:00:20Z",
    duration_ms: 1200, result_count: 100, truncated: false, error_type: null,
    error_message: null, retry_count: 0, threshold_breached: false, ...over,
  };
}

function trend(over: Partial<SearchResultTrend> = {}): SearchResultTrend {
  const points = over.points ?? [{
    bucket_start: "2026-07-20T10:00:00Z", metric_key: "total", value: 100,
    dimensions: {}, execution_id: "e-1", execution_status: "COMPLETED",
    duration_ms: 1200, result_count: 100, threshold_breached: false,
    query_version: 1, query_version_id: "v-1",
  }];
  return {
    search_id: "s-1", metric_key: "total", threshold_value: null,
    threshold_operator: "GT", count: points.length, ...over, points,
  };
}

const search = {
  last_run_at: "2026-07-20T10:00:00Z",
  next_run_at: "2026-07-20T11:00:00Z",
  consecutive_failures: 0,
};

beforeEach(() => {
  init.mockClear();
  setOption.mockClear();
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  vi.spyOn(api, "searchResults").mockResolvedValue(trend());
  vi.spyOn(api, "searchMetricKeys").mockResolvedValue({
    search_id: "s-1", total: 2, count: 2,
    keys: [
      { metric_key: "total", point_count: 1, last_bucket_start: "2026-07-20T10:00:00Z" },
      { metric_key: "host-a", point_count: 1, last_bucket_start: "2026-07-20T10:00:00Z" },
    ],
  });
  vi.spyOn(api, "searchLatestGroups").mockResolvedValue({
    search_id: "s-1", execution_id: "e-1", started_at: "2026-07-20T10:00:00Z",
    status: "COMPLETED", truncated: false, values_are_lower_bounds: false, empty: null,
    total: 2,
    rows: [
      { metric_key: "host-a", dimensions: { sourceip: "10.0.0.1" }, value: 40 },
      { metric_key: "total", dimensions: {}, value: 10 },
    ],
  });
});

describe("schedule interval and staleness", () => {
  it("does not invent an interval when a schedule timestamp is missing", () => {
    expect(scheduleIntervalMs({ ...search, next_run_at: null })).toBeNull();
    expect(scheduleIntervalMs(null)).toBeNull();
  });

  it("is stale only past twice the search's own interval", () => {
    const runs = [execution()];
    const fresh = Date.parse("2026-07-20T11:30:00Z");
    const old = Date.parse("2026-07-20T14:00:00Z");
    expect(staleness(search, runs, fresh)).toBeNull();
    const stale = staleness(search, runs, old);
    expect(stale?.at).toBe("2026-07-20T10:00:20Z");
    expect(stale && stale.ageMs > 2 * stale.intervalMs).toBe(true);
  });
});

describe("ResultTrend states", () => {
  it("names a refused read and who grants it", async () => {
    vi.spyOn(api, "searchResults").mockRejectedValue(new ApiError(403, "hidden"));
    render(<ResultTrend searchId="s-1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("read:searches");
    expect(alert).toHaveTextContent(/administrator grants read:searches/i);
    expect(alert).not.toHaveTextContent("hidden");
    expect(init).not.toHaveBeenCalled();
  });

  it("says a search has never run, when it was created, and how to run it", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend({ points: [], count: 0 }));
    render(
      <ResultTrend
        searchId="s-1"
        executions={[]}
        createdAt="2026-07-01T00:00:00Z"
        search={search}
      />,
    );
    expect(await screen.findByText(/never run since it was created on 2026-07-01 00:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/Run it from this page with Run now/)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("reports a failed run in words, against the alert threshold, and what to do", async () => {
    render(
      <ResultTrend
        searchId="s-1"
        search={{ ...search, consecutive_failures: 2 }}
        executions={[execution({
          status: "TIMEOUT", error_type: "TIMEOUT", result_count: null, completed_at: null,
        })]}
      />,
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/exceeded its timeout/);
    expect(alert).toHaveTextContent("Consecutive failures recorded: 2");
    expect(alert).toHaveTextContent("after 3");
    expect(alert).toHaveTextContent("Inspect the execution, edit the query, or wait for the retry.");
    expect(init).not.toHaveBeenCalled();
  });

  it("marks a stale success with its age and the schedule interval, and still draws the series", async () => {
    render(
      <ResultTrend
        searchId="s-1"
        search={search}
        executions={[execution()]}
        now={Date.parse("2026-07-20T14:00:00Z")}
      />,
    );
    await waitFor(() => expect(init).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("status")).toHaveTextContent(/Stale: the last successful run was/);
    expect(screen.getByRole("status")).toHaveTextContent(/schedule interval 1 h/);
  });

  it("draws a bar series with the same gap a truncated run leaves in the line", async () => {
    render(
      <ResultTrend
        searchId="s-1"
        visualization="BAR"
        truncatedAt={["2026-07-20T11:00:00Z"]}
        executions={[execution()]}
      />,
    );
    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    const option = setOption.mock.calls[0][0] as {
      series: { type: string; data: [number, number | null][]; connectNulls?: boolean }[];
      yAxis: { name: string };
    };
    expect(option.series[0].type).toBe("bar");
    expect(option.series[0].connectNulls).toBe(false);
    expect(option.series[0].data.map((point) => point[1])).toContain(null);
    expect(option.series[0].data.map((point) => point[1])).not.toContain(0);
    expect(option.yAxis.name).toMatch(/result rows/i);
    expect(option.series.filter((series) => series.type === "bar")).toHaveLength(1);
  });

  it("spaces the value axis with splitNumber and hideOverlap, and does not shrink the font", async () => {
    render(<ResultTrend searchId="s-1" executions={[execution()]} />);
    await waitFor(() => expect(setOption).toHaveBeenCalled());
    const option = setOption.mock.calls.at(-1)?.[0] as {
      yAxis: {
        splitNumber?: number;
        minInterval?: number;
        axisLabel?: { hideOverlap?: boolean; fontSize?: number };
      };
    };
    expect(option.yAxis.splitNumber).toBe(3);
    expect(option.yAxis.minInterval).toBe(1);
    expect(option.yAxis.axisLabel?.hideOverlap).toBe(true);
    expect(option.yAxis.axisLabel?.fontSize).toBeUndefined();
  });

  it("initialises the chart when the series is ready before the execution list", async () => {
    let release: (rows: SearchExecution[]) => void = () => undefined;
    const pending = new Promise<SearchExecution[]>((resolve) => {
      release = resolve;
    });
    vi.spyOn(api, "searchExecutions").mockReturnValue(pending);
    vi.spyOn(api, "searchVersions").mockResolvedValue([]);
    render(<ResultTrend variant="widget" searchId="s-1" search={search} />);
    await waitFor(() => expect(api.searchResults).toHaveBeenCalled());
    expect(screen.getByText(/Loading result trend/)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
    release([execution()]);
    await waitFor(() => expect(init).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("img", { name: "Search result trend" })).toBeInTheDocument();
  });

  it("shows the latest total, and a truncated latest run as a lower bound with no verdict", async () => {
    const { unmount } = render(
      <ResultTrend searchId="s-1" visualization="SINGLE_VALUE" executions={[execution()]} />,
    );
    expect(await screen.findByText("100")).toBeInTheDocument();
    expect(screen.getByText(/result rows at/)).toHaveTextContent("result rows at 2026-07-20 10:00:00 UTC");
    expect(init).not.toHaveBeenCalled();
    unmount();

    render(
      <ResultTrend
        searchId="s-1"
        visualization="SINGLE_VALUE"
        executions={[execution({ truncated: true, result_count: 1000, threshold_breached: false })]}
      />,
    );
    expect(await screen.findByText(/at least/)).toBeInTheDocument();
    expect(screen.getByText("1000")).toBeInTheDocument();
    expect(screen.getByText(/Threshold: no verdict/)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("renders a stored pie or heatmap as a table and says so", async () => {
    const { unmount } = render(
      <ResultTrend searchId="s-1" visualization="PIE" executions={[execution()]} />,
    );
    expect(await screen.findByText(/Stored as a pie chart/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();
    expect(within(table).getByText("host-a")).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
    unmount();

    render(
      <ResultTrend searchId="s-1" visualization="HEATMAP" executions={[execution()]} />,
    );
    expect(await screen.findByText(/Stored as a heatmap/)).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("shows a truncated group's value as a lower bound and no verdict", async () => {
    vi.spyOn(api, "searchLatestGroups").mockResolvedValue({
      search_id: "s-1", execution_id: "e-1", started_at: "2026-07-20T10:00:00Z",
      status: "COMPLETED", truncated: true, values_are_lower_bounds: true, empty: null,
      total: 1, rows: [{ metric_key: "host-a", dimensions: { sourceip: "10.0.0.8" }, value: 1000 }],
    });
    render(
      <ResultTrend
        searchId="s-1"
        visualization="TABLE"
        executions={[execution({ truncated: true, result_count: 1000 })]}
      />,
    );
    expect(await screen.findByText(/at least/)).toBeInTheDocument();
    expect(screen.getByText("1000")).toBeInTheDocument();
    expect(screen.getByText(/Threshold: no verdict/)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("uses the subtitle style and left alignment for the groups caption", async () => {
    render(<ResultTrend searchId="s-1" visualization="TABLE" executions={[execution()]} />);
    const table = await screen.findByRole("table");
    const caption = within(table).getByText(/highest value first/);
    expect(caption.tagName).toBe("CAPTION");
    expect(caption).toHaveClass("subtitle", "trend-note");
    const css = readFileSync(resolve(__dirname, "../app/searches/[id]/search-detail.css"), "utf8");
    expect(css).toMatch(/caption\.subtitle\s*\{[^}]*text-align:\s*left/);
  });

  it("offers metric keys as a select, never a text field, and refetches the chosen key", async () => {
    render(<ResultTrend searchId="s-1" executions={[execution()]} />);
    const picker = await screen.findByRole("combobox", { name: "Metric" });
    expect(screen.queryByRole("textbox", { name: "Metric" })).toBeNull();
    expect(picker.querySelectorAll("option").length).toBeGreaterThan(0);
    fireEvent.change(picker, { target: { value: "host-a" } });
    await waitFor(() =>
      expect(api.searchResults).toHaveBeenCalledWith(
        "s-1", "host-a", 500, undefined, expect.any(AbortSignal),
      ),
    );
  });
});

describe("chart.search_results registry", () => {
  it("requires read:searches, reads the trend route, and enumerates both config fields", () => {
    const def = widgetDef("chart.search_results");
    expect(def?.status).toBe("live");
    expect(def?.requires).toEqual(["read:searches"]);
    expect(def?.sources).toContain("/searches/${id}/results");
    expect(def?.range).toBeNull();
    const searchField = def?.config.search_id;
    const metricField = def?.config.metric_key;
    expect(searchField && isReadField(searchField)).toBe(true);
    expect(metricField && isReadField(metricField)).toBe(true);
    if (searchField && isReadField(searchField)) expect(searchField.from.dependsOn).toBeUndefined();
    if (metricField && isReadField(metricField)) expect(metricField.from.dependsOn).toBe("search_id");
  });

  it("is the component the search page and the widget both mount", () => {
    const page = readFileSync(resolve(__dirname, "../app/searches/[id]/page.tsx"), "utf8");
    const widget = readFileSync(resolve(__dirname, "../widgets/SearchResultsWidget.tsx"), "utf8");
    expect(page).toContain('from "@/components/ResultTrend"');
    expect(widget).toContain('from "@/components/ResultTrend"');
    expect(page).toContain("<ResultTrend");
    expect(widget).toContain("<ResultTrend");
  });
});
