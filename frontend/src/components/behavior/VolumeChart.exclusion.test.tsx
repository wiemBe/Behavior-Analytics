import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MetricBucket } from "@/lib/api";

import { VolumeChart } from "./VolumeChart";

const { setOption, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })),
    use: vi.fn(),
  };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const at = (m: number) => new Date(Date.UTC(2026, 9, 12, 9, m)).toISOString();
const bucket = (m: number, over: Partial<MetricBucket> = {}): MetricBucket => ({
  bucket_start: at(m),
  bucket_seconds: 300,
  event_count: 600,
  average_eps: 2,
  peak_eps: null,
  completeness: "COMPLETE",
  last_event_at: null,
  ...over,
});

interface Option {
  series: Array<{
    name: string;
    markArea?: {
      data: Array<
        [{ name: string; xAxis: number; itemStyle: { borderType: string }; label?: { show: boolean; formatter?: string } }, { xAxis: number }]
      >;
    };
  }>;
}
const option = () => setOption.mock.calls[0][0] as Option;
const areas = () => option().series[0].markArea?.data ?? [];

const buckets = [bucket(0), bucket(5), bucket(10), bucket(15), bucket(20), bucket(25)];
const span = (from: number, to: number) => ({ start: at(from), end: at(to) });

function chart(exclusions?: Array<{ start: string; end: string }>) {
  return render(
    <VolumeChart buckets={buckets} expected={2} expectedLow={1} expectedHigh={3} exclusions={exclusions} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.ResizeObserver = class {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  } as unknown as typeof ResizeObserver;
});

describe("VolumeChart: analyst training exclusions", () => {
  it("shades an excluded interval as a labelled area, outlined solid", () => {
    chart([span(5, 15)]);
    expect(areas()).toHaveLength(1);
    const [from, to] = areas()[0];
    expect(from.name).toBe("Excluded from baseline training");
    expect(from.xAxis).toBe(Date.parse(at(5)));
    expect(to.xAxis).toBe(Date.parse(at(15)));
    expect(from.itemStyle.borderType).toBe("solid");
    expect(from.label).toMatchObject({ show: true, formatter: "Excluded from baseline training" });
  });

  it("merges overlapping intervals so one stretch is shaded once", () => {
    chart([span(0, 10), span(5, 15)]);
    expect(areas()).toHaveLength(1);
    expect(areas()[0][1].xAxis).toBe(Date.parse(at(15)));
  });

  it("names the band in the key, in words, only when one was drawn", () => {
    chart([span(5, 15)]);
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent("Excluded from baseline training");
  });

  it("is unchanged without an exclusion", () => {
    chart();
    expect(option().series[0].markArea).toBeUndefined();
    expect(screen.getByRole("list", { name: "Chart key" })).not.toHaveTextContent("Excluded");
  });

  it("keeps the expected range a single band beside the exclusion", () => {
    chart([span(5, 15)]);
    expect(option().series.filter((s) => s.name === "Expected range")).toHaveLength(1);
  });
});
