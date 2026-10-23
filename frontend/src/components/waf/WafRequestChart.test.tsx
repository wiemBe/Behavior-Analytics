import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { wafBucket } from "./fixtures";
import { WafRequestChart } from "./WafRequestChart";
import { RATIO_SPECS } from "./wafSeries";
import { wafBaselineCell } from "./fixtures";

// jsdom has no canvas, so ECharts is stubbed; what is under test is the option
// the component hands it. Hoisted because `echarts.use` runs at import time.
const { setOption, dispose, init, use } = vi.hoisted(() => {
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

describe("WafRequestChart: seasonal band", () => {
  // 2026-10-12 is a Monday: weekday 1.
  const H = (h: number, m = 0) => `2026-10-12T${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:00Z`;
  const buckets = [
    wafBucket({ bucket_start: H(9) }),
    wafBucket({ bucket_start: H(10) }),
    wafBucket({ bucket_start: H(11) }),
  ];
  const lookup = {
    cells: [
      wafBaselineCell({ weekday: 1, hour: 9, median: 40, p05: 20, p95: 60 }),
      wafBaselineCell({ weekday: 1, hour: 10, median: 80, p05: 50, p95: 120 }),
      // Hour 11 is not reliable: no band there.
      wafBaselineCell({ weekday: 1, hour: 11, is_reliable: false }),
    ],
    metric: "request_count",
    normalizerVersion: 2,
  };

  function seasonal(over: Partial<Parameters<typeof WafRequestChart>[0]> = {}) {
    return chart(buckets, { baseline: lookup, ...over });
  }

  it("follows the cell of each bucket's weekday and hour, not one flat range", () => {
    seasonal();
    // Hourly buckets are separated by uncollected spans, which are points with no band.
    expect(series("Expected low").data.map((d) => d[1])).toEqual([20, null, 50, null, null]);
    expect(series("Expected range").data.map((d) => d[1])).toEqual([40, null, 70, null, null]);
    expect(series("Expected requests").data.map((d) => d[1])).toEqual([40, null, 80, null, null]);
  });

  it("leaves a bucket with a missing or unreliable cell without a band, never a zero band", () => {
    seasonal();
    const low = series("Expected low").data.map((d) => d[1]);
    const range = series("Expected range").data.map((d) => d[1]);
    expect(low[4]).toBeNull();
    expect(range[4]).toBeNull();
    expect(low).not.toContain(0);
    expect(series("Expected range").connectNulls).toBe(false);
  });

  it("labels the seasonal band Expected range, and keeps the key to marks it drew", () => {
    seasonal();
    const key = screen.getByRole("list", { name: "Chart key" });
    expect(key).toHaveTextContent("Expected range");
    expect(key).not.toHaveTextContent("at detection");
    expect(key).toHaveTextContent("Expected median");
  });

  it("falls back to the flat range at detection, labelled so, when no baseline was loaded", () => {
    chart(buckets, { baseline: null });
    expect(series("Expected low").data.map((d) => d[1])).toEqual([22, 22, 22, 22, 22]);
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent("Expected range at detection");
  });

  it("draws no band at all when no bucket has a reliable cell", () => {
    chart(buckets, { baseline: { cells: [], metric: "request_count", normalizerVersion: 2 } });
    expect(series("Expected low").data).toEqual([]);
    expect(series("Expected range").data).toEqual([]);
    expect(screen.getByRole("list", { name: "Chart key" })).not.toHaveTextContent("Expected range");
  });
});

describe("WafRequestChart: ratio detector", () => {
  const spec = RATIO_SPECS.WAF_BLOCK_RATIO_SPIKE;
  const T0 = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

  function ratioChart(buckets: ReturnType<typeof wafBucket>[], over = {}) {
    return chart(buckets, { ratio: spec, expected: 0.02, expectedLow: 0.01, expectedHigh: 0.05, ...over });
  }

  it("plots the proportion in percent on an axis that names its unit", () => {
    ratioChart([wafBucket({ bucket_start: T0(0), blocked_count: 10, action_events: 40 })]);
    expect(option().yAxis[0].name).toBe("Blocked share of requests (%)");
    expect(series("Requests").data.map((d) => d[1])).toEqual([25]);
  });

  it("plots a bucket whose breakdown was not collected, partial or empty as a gap, never 0", () => {
    ratioChart([
      wafBucket({ bucket_start: T0(0), action_measurement: "NOT_COLLECTED", blocked_count: null, action_events: null }),
      wafBucket({ bucket_start: T0(5), completeness: "PARTIAL", partial: true }),
      wafBucket({ bucket_start: T0(10), blocked_count: 0, action_events: 0 }),
      wafBucket({ bucket_start: T0(15), blocked_count: 0, action_events: 40 }),
    ]);
    expect(series("Requests").data.map((d) => d[1])).toEqual([null, null, null, 0]);
  });

  it("draws the expected proportion at detection as a flat line in percent", () => {
    ratioChart([wafBucket()]);
    expect(series("Expected requests").data[0][1]).toBeCloseTo(2);
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent("Expected at detection");
  });

  it("names the proportion in the key and keeps coverage as the secondary panel in percent", () => {
    ratioChart([wafBucket({ endpoint_coverage_ratio: 0.5 })]);
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent("Blocked share of requests");
    expect(option().yAxis[1].name).toBe("Coverage (%)");
    expect(series("Endpoint coverage").data[0][1]).toBe(50);
  });

  it("states ratio gaps as not 0 %", () => {
    ratioChart([wafBucket()]);
    expect(screen.getByText(/They are not 0 %/)).toBeInTheDocument();
  });
});
