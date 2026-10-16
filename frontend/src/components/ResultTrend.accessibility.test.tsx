import { render, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ResultTrend } from "./ResultTrend";
import { api, type SearchResultTrend } from "@/lib/api";

const setOption = vi.fn();
vi.mock("echarts/core", () => ({
  init: () => ({ setOption, resize: vi.fn(), dispose: vi.fn() }),
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

const TREND: SearchResultTrend = {
  search_id: "search-1",
  metric_key: "total",
  threshold_value: null,
  threshold_operator: "GT",
  count: 1,
  points: [
    {
      bucket_start: "2026-10-16T06:00:00Z",
      metric_key: "total",
      value: 12,
      dimensions: {},
      execution_id: "execution-1",
      execution_status: "COMPLETED",
      duration_ms: 800,
      result_count: 12,
      threshold_breached: false,
      query_version: 1,
      query_version_id: "version-1",
    },
  ],
};

beforeEach(() => {
  setOption.mockClear();
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
  vi.spyOn(api, "searchMetricKeys").mockResolvedValue({
    search_id: "search-1", total: 1, count: 1,
    keys: [{ metric_key: "total", point_count: 1, last_bucket_start: "2026-10-16T06:00:00Z" }],
  });
});

it("turns off the canvas animation when reduced motion is requested", async () => {
  vi.spyOn(api, "searchResults").mockResolvedValue(TREND);
  render(<ResultTrend searchId="search-1" />);

  await waitFor(() => expect(setOption).toHaveBeenCalledOnce());
  expect(setOption.mock.calls[0][0]).toMatchObject({ animation: false });
  expect(window.matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
});
