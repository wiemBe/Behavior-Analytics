import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResultTrend } from "./ResultTrend";
import { ApiError, api, type ResultMetricPoint, type SearchResultTrend } from "@/lib/api";

// jsdom has no layout engine, so ECharts is stubbed. What matters here is the
// contract the component has with it: init once, resize on observation, and
// dispose exactly once on unmount.
const setOption = vi.fn();
const resize = vi.fn();
const dispose = vi.fn();
const init = vi.fn(() => ({ setOption, resize, dispose }));

vi.mock("echarts/core", () => ({
  init: (...args: unknown[]) => init(...(args as [])),
  use: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

let observerCallback: (() => void) | null = null;
const observe = vi.fn();
const disconnect = vi.fn();

class TestResizeObserver {
  constructor(cb: () => void) {
    observerCallback = cb;
  }
  observe = observe;
  unobserve = vi.fn();
  disconnect = disconnect;
}

function point(over: Partial<ResultMetricPoint> = {}): ResultMetricPoint {
  return {
    bucket_start: "2026-07-20T10:00:00Z", metric_key: "total", value: 100,
    dimensions: {}, execution_id: "e-1", execution_status: "COMPLETED",
    duration_ms: 1200, result_count: 100, threshold_breached: false,
    query_version: 1, query_version_id: "v-1", ...over,
  };
}

function trend(over: Partial<SearchResultTrend> = {}): SearchResultTrend {
  const points = over.points ?? [point()];
  return {
    search_id: "s-1", metric_key: "total", threshold_value: null,
    threshold_operator: "GT", count: points.length, ...over, points,
  };
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  observerCallback = null;
  init.mockClear();
  setOption.mockClear();
  resize.mockClear();
  dispose.mockClear();
  observe.mockClear();
  disconnect.mockClear();
});

describe("ResultTrend", () => {
  it("renders the chart once data arrives", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend());
    render(<ResultTrend searchId="s-1" />);

    expect(screen.getByText(/loading result trend/i)).toBeInTheDocument();
    await waitFor(() => expect(init).toHaveBeenCalledTimes(1));
    expect(setOption).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("img", { name: "Search result trend" })).toBeInTheDocument();
  });

  it("handles an empty dataset without initialising a chart", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend({ points: [], count: 0 }));
    render(<ResultTrend searchId="s-1" />);

    expect(await screen.findByText(/no result metrics recorded yet/i)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("shows a safe message when the API fails", async () => {
    vi.spyOn(api, "searchResults").mockRejectedValue(new ApiError(500, "internal"));
    render(<ResultTrend searchId="s-1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server could not complete the request. Try again shortly.",
    );
    expect(init).not.toHaveBeenCalled();
  });

  it("disposes the ECharts instance on unmount", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend());
    const { unmount } = render(<ResultTrend searchId="s-1" />);

    await waitFor(() => expect(init).toHaveBeenCalledTimes(1));
    unmount();

    expect(dispose).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it("resizes through the ResizeObserver", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend());
    render(<ResultTrend searchId="s-1" />);

    await waitFor(() => expect(observe).toHaveBeenCalledTimes(1));
    observerCallback?.();
    expect(resize).toHaveBeenCalledTimes(1);
  });

  it("breaks the line for a failed run rather than plotting it as zero", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(
      trend({
        points: [
          point({ value: 100 }),
          point({ execution_status: "FAILED", value: 0, duration_ms: null, result_count: null }),
          point({ value: 120 }),
        ],
      }),
    );
    render(<ResultTrend searchId="s-1" />);

    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    const option = setOption.mock.calls[0][0] as {
      series: { data: (number | null)[]; connectNulls?: boolean }[];
    };
    // A zero here would invent a traffic cliff that never happened.
    expect(option.series[0].data).toEqual([100, null, 120]);
    expect(option.series[0].connectNulls).toBe(false);
  });

  it("annotates query-version boundaries", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(
      trend({
        points: [
          point({ query_version: 1 }),
          point({ query_version: 2, query_version_id: "v-2" }),
          point({ query_version: 2, query_version_id: "v-2" }),
        ],
      }),
    );
    render(<ResultTrend searchId="s-1" />);

    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    const option = setOption.mock.calls[0][0] as {
      series: { markLine?: { data: { xAxis: number; name: string }[] } }[];
    };
    // Exactly one boundary: at the transition, not on every v2 point.
    expect(option.series[0].markLine?.data).toEqual([{ xAxis: 1, name: "v2" }]);
    expect(
      screen.getByText(/dashed lines mark query-version changes/i),
    ).toBeInTheDocument();
  });

  it("draws a threshold reference line when the search has one", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend({ threshold_value: 50 }));
    render(<ResultTrend searchId="s-1" />);

    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    const option = setOption.mock.calls[0][0] as { series: { name: string }[] };
    expect(option.series.map((s) => s.name)).toContain("threshold");
    expect(screen.getByText(/threshold GT 50/)).toBeInTheDocument();
  });

  it("draws a truncated run as a gap: no value, no zero, no bridge", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(
      trend({
        points: [
          point({ bucket_start: "2026-07-20T10:00:00Z", value: 100 }),
          point({ bucket_start: "2026-07-20T12:00:00Z", value: 120 }),
        ],
      }),
    );
    render(<ResultTrend searchId="s-1" truncatedAt={["2026-07-20T11:00:00Z"]} />);

    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    const option = setOption.mock.calls[0][0] as {
      series: { data: (number | null)[]; connectNulls?: boolean }[];
      xAxis: { data: string[] };
    };
    expect(option.series[0].data).toEqual([100, null, 120]);
    expect(option.series[0].data).not.toContain(0);
    expect(option.series[0].connectNulls).toBe(false);
    expect(option.xAxis.data).toHaveLength(3);
    expect(screen.getByText(/1 truncated run left a gap/i)).toBeInTheDocument();
  });

  it("keeps the version-boundary marker on the right point after a gap", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(
      trend({
        points: [
          point({ bucket_start: "2026-07-20T10:00:00Z", query_version: 1 }),
          point({ bucket_start: "2026-07-20T12:00:00Z", query_version: 2, query_version_id: "v-2" }),
        ],
      }),
    );
    render(<ResultTrend searchId="s-1" truncatedAt={["2026-07-20T11:00:00Z"]} />);

    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    const option = setOption.mock.calls[0][0] as {
      series: { markLine?: { data: { xAxis: number }[] } }[];
    };
    // The gap occupies index 1, so v2 starts at index 2.
    expect(option.series[0].markLine?.data[0].xAxis).toBe(2);
  });

  it("says nothing about gaps when no run was truncated", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue(trend());
    render(<ResultTrend searchId="s-1" />);
    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/truncated run/i)).not.toBeInTheDocument();
  });

  describe("chart presentation", () => {
    type Opt = {
      xAxis: { name: string; data: string[]; axisLabel: { show?: boolean } };
      yAxis: { name: string; min: number };
      series: {
        name: string;
        showSymbol?: boolean;
        markPoint?: { data: { coord: number[]; symbol: string }[] };
      }[];
    };
    const lastOption = () => setOption.mock.calls[0][0] as Opt;

    it("labels the x-axis with UTC times and says UTC in its title", async () => {
      vi.spyOn(api, "searchResults").mockResolvedValue(
        trend({ points: [point({ bucket_start: "2026-07-20T10:05:00Z" })] }),
      );
      render(<ResultTrend searchId="s-1" />);
      await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
      expect(lastOption().xAxis.axisLabel.show).not.toBe(false);
      expect(lastOption().xAxis.data).toEqual(["07-20 10:05"]);
      expect(lastOption().xAxis.name).toMatch(/UTC/);
    });

    it("names the value axis as result rows and starts it at zero", async () => {
      vi.spyOn(api, "searchResults").mockResolvedValue(trend());
      render(<ResultTrend searchId="s-1" />);
      await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
      expect(lastOption().yAxis.name).toMatch(/result rows/i);
      expect(lastOption().yAxis.min).toBe(0);
    });

    it("draws a clean line with no marker on every point", async () => {
      vi.spyOn(api, "searchResults").mockResolvedValue(
        trend({
          points: [
            point({ bucket_start: "2026-07-20T10:00:00Z" }),
            point({ bucket_start: "2026-07-20T11:00:00Z" }),
          ],
        }),
      );
      render(<ResultTrend searchId="s-1" />);
      await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
      expect(lastOption().series[0].showSymbol).toBe(false);
      expect(lastOption().series[0].markPoint).toBeUndefined();
    });

    it("marks only a point isolated between gaps, which a line cannot show", async () => {
      vi.spyOn(api, "searchResults").mockResolvedValue(
        trend({
          points: [
            point({ bucket_start: "2026-07-20T10:00:00Z" }),
            point({ bucket_start: "2026-07-20T11:00:00Z" }),
            point({ bucket_start: "2026-07-20T13:00:00Z" }),
          ],
        }),
      );
      render(<ResultTrend searchId="s-1" truncatedAt={["2026-07-20T12:00:00Z"]} />);
      await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
      const marks = lastOption().series[0].markPoint?.data ?? [];
      expect(marks).toHaveLength(1);
      expect(marks[0].coord[0]).toBe(3);
    });

    it("takes every colour from the tokens, never from a literal", async () => {
      vi.spyOn(window, "getComputedStyle").mockReturnValue({
        getPropertyValue: (name: string) => `token(${name})`,
      } as unknown as CSSStyleDeclaration);
      vi.spyOn(api, "searchResults").mockResolvedValue(
        trend({
          threshold_value: 50,
          points: [
            point({ bucket_start: "2026-07-20T10:00:00Z", query_version: 1 }),
            point({ bucket_start: "2026-07-20T11:00:00Z", query_version: 2, query_version_id: "v-2" }),
            point({ bucket_start: "2026-07-20T12:00:00Z", execution_status: "FAILED" }),
          ],
        }),
      );
      render(<ResultTrend searchId="s-1" />);
      await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1));
      const json = JSON.stringify(lastOption());
      expect(json).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
      expect(json).toContain("token(--chart-observed)");
      expect(json).toContain("token(--crit)");
      expect(json).toContain("token(--warn)");
    });
  });
});
