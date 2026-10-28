// ResultTrend in Turkish (I18N-12): the y-axis ticks and the run readout use the locale's digits.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createLocaleTranslator } from "@/i18n/testTranslator";
import { api, type SearchExecution } from "@/lib/api";
import { runReadout } from "@/components/searches/authoring";

import { ResultTrend } from "./ResultTrend";
import type { TrendI18n } from "./trendText";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })) };
});
vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use: vi.fn() };
});
vi.mock("echarts/charts", () => ({ LineChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const tr: TrendI18n = { t: createLocaleTranslator("tr"), locale: "tr" };

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  init.mockClear();
  setOption.mockClear();
  vi.spyOn(api, "searchMetricKeys").mockResolvedValue({ search_id: "s-1", total: 0, count: 0, keys: [] });
  vi.spyOn(api, "searchResults").mockResolvedValue({
    search_id: "s-1",
    metric_key: "total",
    threshold_value: null,
    threshold_operator: "GT",
    count: 1,
    points: [
      {
        bucket_start: "2026-07-20T10:00:00Z", metric_key: "total", value: 1204, dimensions: {},
        execution_id: "e-1", execution_status: "COMPLETED", duration_ms: 90500, result_count: 1204,
        threshold_breached: false, query_version: 1, query_version_id: "v-1",
      },
    ],
  });
});

interface Option {
  yAxis: { axisLabel: { formatter?: (value: number) => string } };
}

async function lastOption(i18n?: TrendI18n): Promise<Option> {
  render(
    <ResultTrend
      searchId="s-1"
      i18n={i18n}
      search={{ last_run_at: "2026-07-20T10:00:00Z", next_run_at: "2026-07-20T11:00:00Z", consecutive_failures: 0 }}
      executions={[{ id: "e-1", status: "COMPLETED", completed_at: "2026-07-20T10:00:00Z", started_at: "2026-07-20T10:00:00Z" } as SearchExecution]}
      now={Date.parse("2026-07-20T10:30:00Z")}
    />,
  );
  await screen.findByRole("img");
  return setOption.mock.calls.at(-1)?.[0] as Option;
}

describe("ResultTrend digits", () => {
  it("groups the y-axis ticks the Turkish way", async () => {
    const option = await lastOption(tr);
    expect(option.yAxis.axisLabel.formatter?.(1200)).toBe("1.200");
    expect(option.yAxis.axisLabel.formatter?.(3)).toBe("3");
  });

  it("leaves the English axis to ECharts' own labels", async () => {
    const option = await lastOption();
    expect(option.yAxis.axisLabel.formatter).toBeUndefined();
  });

  it("passes its locale to the failed-run readout it shows", async () => {
    render(
      <ResultTrend
        searchId="s-1"
        i18n={tr}
        search={{ last_run_at: "2026-07-20T10:00:00Z", next_run_at: "2026-07-20T11:00:00Z", consecutive_failures: 1204 }}
        executions={[{ id: "e-1", status: "FAILED", error_type: "TIMEOUT", completed_at: "2026-07-20T10:00:00Z", started_at: "2026-07-20T10:00:00Z" } as SearchExecution]}
        now={Date.parse("2026-07-20T10:30:00Z")}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("art arda hata: 1.204.");
  });

  it("reads a run's counts with the locale", () => {
    const completed = {
      status: "COMPLETED", truncated: false, threshold_breached: false, result_count: 1204,
    } as unknown as SearchExecution;
    expect(runReadout(completed, 0, tr.t, "tr").text).toContain("1.204");
    expect(runReadout(completed, 0).text).toContain("1204");
    const truncated = { ...completed, truncated: true } as SearchExecution;
    expect(runReadout(truncated, 0, tr.t, "tr").text).toContain("1.204");
    const failed = { status: "FAILED", error_type: null, truncated: false } as unknown as SearchExecution;
    expect(runReadout(failed, 1204, tr.t, "tr").text).toContain("1.204");
    expect(runReadout(failed, 1204).text).toContain("1204");
  });
});
