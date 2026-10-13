import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { wafBucket } from "./fixtures";
import { WafRequestChart } from "./WafRequestChart";

// jsdom has no canvas, so ECharts is stubbed; what is under test is the option
// the component hands it. Hoisted because `echarts.use` runs at import time.
const { setOption, resize, dispose, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return { setOption, resize, dispose, init: vi.fn(() => ({ setOption, resize, dispose })), use: vi.fn() };
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

interface Axis {
  name?: string;
  gridIndex: number;
  max?: number;
}
interface Series {
  name: string;
  data: Array<[number, number | null]>;
  connectNulls?: boolean;
  showSymbol?: boolean;
  yAxisIndex?: number;
  stack?: string;
  markArea?: { data: unknown[] };
}
interface Option {
  useUTC: boolean;
  xAxis: Axis[];
  yAxis: Axis[];
  series: Series[];
}

const option = () => setOption.mock.calls[0][0] as Option;
const series = (name: string) => option().series.find((s) => s.name === name)!;

const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

function chart(buckets = [wafBucket()], over: Partial<Parameters<typeof WafRequestChart>[0]> = {}) {
  return render(
    <WafRequestChart buckets={buckets} expected={41.5} expectedLow={22} expectedHigh={68} {...over} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("WafRequestChart", () => {
  it("initializes one chart, observes resize through disposal, and none when empty", () => {
    const view = chart();
    expect(init).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(dispose).toHaveBeenCalledTimes(1);

    vi.clearAllMocks();
    chart([]);
    expect(init).not.toHaveBeenCalled();
    expect(screen.getByText(/absence of collection, not an observation of zero requests/)).toBeInTheDocument();
  });

  it("plots a complete bucket's count and a measured zero as a value", () => {
    chart([wafBucket({ bucket_start: T(0), request_count: 0 }), wafBucket({ bucket_start: T(5), request_count: 41 })]);
    expect(series("Requests").data.map((d) => d[1])).toEqual([0, 41]);
  });

  it("plots a PARTIAL bucket and a null count as null, never as zero", () => {
    chart([
      wafBucket({ bucket_start: T(0), completeness: "PARTIAL", partial: true, request_count: 12 }),
      wafBucket({ bucket_start: T(5), request_count: null }),
    ]);
    expect(series("Requests").data.map((d) => d[1])).toEqual([null, null]);
  });

  it("never connects the line across a gap, and shades the not-collected interval", () => {
    chart([wafBucket({ bucket_start: T(0) }), wafBucket({ bucket_start: T(30) })]);
    expect(series("Requests").connectNulls).toBe(false);
    expect(series("Requests").data.map((d) => d[1])).toEqual([41, null, 41]);
    expect(series("Requests").markArea!.data).toHaveLength(1);
  });

  it("draws the expected range as one stacked band, not as two lines", () => {
    chart();
    const low = series("Expected low");
    const range = series("Expected range");
    expect(low.stack).toBe("expected-range");
    expect(range.stack).toBe("expected-range");
    expect(low.data[0][1]).toBe(22);
    expect(range.data[0][1]).toBe(46);
  });

  it("draws no band and no expected line when no range was recorded", () => {
    chart([wafBucket()], { expected: null, expectedLow: null, expectedHigh: null });
    expect(series("Expected low").data).toEqual([]);
    expect(series("Expected range").data).toEqual([]);
    expect(series("Expected requests").data).toEqual([]);
    expect(screen.queryByText("Expected range at detection")).not.toBeInTheDocument();
  });

  it("puts coverage on its own axis, in percent, beneath the requests", () => {
    chart([wafBucket({ endpoint_coverage_ratio: 0.5 })]);
    const o = option();
    expect(o.yAxis[0].name).toBe("Requests per 5 min (count)");
    expect(o.yAxis[1].name).toBe("Coverage (%)");
    expect(o.yAxis[1].max).toBe(100);
    expect(series("Endpoint coverage").yAxisIndex).toBe(1);
    expect(series("Endpoint coverage").data[0][1]).toBe(50);
  });

  it("leaves coverage null where it was not recorded, never 0", () => {
    chart([wafBucket({ endpoint_coverage_ratio: null })]);
    expect(series("Endpoint coverage").data[0][1]).toBeNull();
  });

  it("names the unit on the time axis and plots in UTC", () => {
    chart();
    expect(option().useUTC).toBe(true);
    expect(option().xAxis[1].name).toBe("Time (UTC)");
  });

  it("puts no marker on every point", () => {
    chart();
    for (const s of option().series) expect(s.showSymbol).not.toBe(true);
  });

  it("falls back to 'per bucket' on the axis when the width is unknown", () => {
    chart([wafBucket({ bucket_seconds: 0 })]);
    expect(option().yAxis[0].name).toBe("Requests per bucket (count)");
  });

  it("states lower-bound buckets in the note, and keys only the marks it drew", () => {
    chart([wafBucket({ request_count_measurement: "LOWER_BOUND", endpoint_coverage_ratio: 0.9 })]);
    expect(screen.getByText(/lower bounds/)).toBeInTheDocument();
    const key = screen.getByRole("list", { name: "Chart key" });
    expect(key).toHaveTextContent("Endpoint coverage");
    expect(key).not.toHaveTextContent("Anomalous interval");
    expect(key).not.toHaveTextContent("Not fully collected");
  });

  it("exposes a named image and a prose summary", () => {
    chart([wafBucket()], { textSummary: "Requests to GET /x across 1 stored buckets." });
    expect(screen.getByRole("img", { name: /Requests against the expected range/ })).toBeInTheDocument();
    expect(screen.getByText(/across 1 stored buckets/)).toBeInTheDocument();
  });
});
