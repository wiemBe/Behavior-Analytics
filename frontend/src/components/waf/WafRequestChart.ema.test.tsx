import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { wafBucket } from "./fixtures";
import { WafRequestChart } from "./WafRequestChart";
import { RATIO_SPECS } from "./wafSeries";
import { EMA_STORAGE_KEY } from "@/components/behavior/EmaToggles";

const { setOption, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize, dispose })), use: vi.fn() };
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

class TestResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

interface Opt {
  yAxis: unknown;
  tooltip: { formatter: (p: unknown) => string };
  series: Array<{
    name: string;
    data: Array<[number, number | null]>;
    connectNulls?: boolean;
    showSymbol?: boolean;
    z?: number;
    xAxisIndex?: number;
    yAxisIndex?: number;
    lineStyle?: { color?: string; width?: number };
  }>;
}
const last = () => setOption.mock.calls[setOption.mock.calls.length - 1][0] as Opt;
const emaLines = (o: Opt = last()) => o.series.filter((s) => s.name.startsWith("EMA "));
// The built option without its functions, to compare two options.
const plain = (o: Opt) => JSON.stringify(o);

const T0 = Date.parse("2026-10-12T09:00:00Z");
const at = (i: number) => new Date(T0 + i * 300_000).toISOString();

type Row = number | "PARTIAL" | "FLOOR";
function rowsOf(values: Row[]) {
  return values.map((v, i) =>
    wafBucket({
      bucket_start: at(i),
      request_count: v === "PARTIAL" ? 7 : v === "FLOOR" ? 50 : v,
      request_count_measurement: v === "FLOOR" ? "LOWER_BOUND" : "EXACT",
      completeness: v === "PARTIAL" ? "PARTIAL" : "COMPLETE",
      partial: v === "PARTIAL",
    }),
  );
}
const RUN: Row[] = [2, 4, 6, 10, 0, 3, 5, 7, 9, 11, 13, 15];
const WITH_GAP: Row[] = [2, 4, 6, 10, "PARTIAL", 3, 5, 7, 9, 11, 13];

function chart(values: Row[] = RUN, over: Partial<Parameters<typeof WafRequestChart>[0]> = {}) {
  return render(
    <WafRequestChart buckets={rowsOf(values)} expected={5} expectedLow={2} expectedHigh={9} {...over} />,
  );
}
const pressed = (period: number) => screen.getByRole("button", { name: `EMA ${period}` });
const dataOf = (name: string) => last().series.find((s) => s.name === name)!.data.map((d) => d[1]);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => `tok(${name})`,
  } as unknown as CSSStyleDeclaration);
});
afterEach(() => vi.restoreAllMocks());

describe("WafRequestChart EMA overlay", () => {
  it("offers the control, all off, and draws no EMA line by default", () => {
    chart();
    const group = screen.getByRole("group", { name: /moving averages/i });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["3", "5", "10", "20"]);
    for (const b of buttons) expect(b).toHaveAttribute("aria-pressed", "false");
    expect(emaLines()).toHaveLength(0);
    expect(last().series.map((s) => s.name)).toEqual([
      "Requests",
      "Expected requests",
      "Expected low",
      "Expected range",
      "Endpoint coverage",
    ]);
    expect(screen.queryByText(/drawn in your browser/)).toBeNull();
  });

  it("draws exactly the enabled lines on the requests panel: thin, no symbols, not bridged, token colours, under the observed line", () => {
    chart();
    fireEvent.click(pressed(5));
    fireEvent.click(pressed(20));
    const lines = emaLines();
    expect(lines.map((s) => s.name)).toEqual(["EMA 5", "EMA 20"]);
    expect(lines.map((s) => s.lineStyle?.color)).toEqual(["tok(--chart-cat-3)", "tok(--chart-cat-5)"]);
    const observed = last().series.find((s) => s.name === "Requests")!;
    for (const s of lines) {
      expect(s.connectNulls).toBe(false);
      expect(s.showSymbol).toBe(false);
      expect(s.xAxisIndex).toBe(0);
      expect(s.yAxisIndex).toBe(0);
      expect(s.lineStyle!.width).toBeLessThan(observed.lineStyle!.width!);
      expect(s.z).toBeLessThan(observed.z!);
    }
  });

  it("averages the observed requests: the first value is the mean of the first run", () => {
    chart();
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v.slice(0, 2)).toEqual([null, null]);
    expect(v[2]).toBeCloseTo(4, 10);
  });

  it("ends the line at a partial bucket and warms up again after it", () => {
    chart(WITH_GAP);
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v[3]).not.toBeNull();
    expect(v[4]).toBeNull();
    expect(v[5]).toBeNull();
    expect(v[6]).toBeNull();
    expect(v[7]).not.toBeNull();
  });

  it("ends the line at a lower-bound bucket, which is not an exact count", () => {
    chart([2, 4, 6, 10, "FLOOR", 3, 5, 7, 9]);
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(dataOf("Requests")[4]).toBe(50);
    expect(v[3]).not.toBeNull();
    expect(v[4]).toBeNull();
    expect(v[6]).toBeNull();
    expect(v[7]).not.toBeNull();
  });

  it("averages the proportion, in percent, when the chart plots one, and the legend names it", () => {
    const rows = [10, 20, 30, 40].map((blocked, i) =>
      wafBucket({ bucket_start: at(i), blocked_count: blocked, action_events: 100 }),
    );
    render(
      <WafRequestChart
        buckets={rows}
        ratio={RATIO_SPECS.WAF_BLOCK_RATIO_SPIKE}
        expected={0.2}
        expectedLow={0.1}
        expectedHigh={0.3}
      />,
    );
    fireEvent.click(pressed(3));
    expect(dataOf("Requests")).toEqual([10, 20, 30, 40]);
    const v = dataOf("EMA 3");
    expect(v[2]).toBeCloseTo(20, 10);
    expect(v[3]).toBeCloseTo(30, 10);
    expect(last().tooltip.formatter({ dataIndex: 3 })).toContain("EMA 3: 30.0\u00A0%");
    expect(screen.getByText(/EMA 3 · 15 min of Blocked share of requests/)).toBeInTheDocument();
  });

  it("puts an EMA row in the tooltip only where it has a value", () => {
    chart(WITH_GAP);
    fireEvent.click(pressed(3));
    const tip = last().tooltip.formatter;
    expect(tip({ dataIndex: 2 })).toMatch(/EMA 3: 4\.0 req/);
    expect(tip({ dataIndex: 1 })).not.toMatch(/EMA/);
    expect(tip({ dataIndex: 4 })).not.toMatch(/EMA/);
  });

  it("keeps a legend entry naming the line and the span, and says when a period never has a run", () => {
    chart([2, 4]);
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(20));
    expect(screen.getByText(/EMA 3 · 15 min of Requests/)).toBeInTheDocument();
    expect(screen.getByText(/EMA 20 · 100 min of Requests/)).toBeInTheDocument();
    expect(screen.getAllByText(/not enough consecutive buckets/)).toHaveLength(2);
  });

  it("shows the display-only note and the screen-reader summary only while a period is on", () => {
    chart();
    expect(screen.queryByText(/do not take part in detection/)).toBeNull();
    fireEvent.click(pressed(5));
    expect(screen.getByText(/do not take part in detection/)).toBeInTheDocument();
    expect(screen.getByText(/overlays of Requests on: 5 buckets/)).toBeInTheDocument();
    fireEvent.click(pressed(5));
    expect(screen.queryByText(/do not take part in detection/)).toBeNull();
  });

  it("leaves the option, and the y-axis, as they were once the lines are off again", () => {
    chart(WITH_GAP);
    const off = last();
    fireEvent.click(pressed(5));
    const on = last();
    expect(JSON.stringify(on.yAxis)).toBe(JSON.stringify(off.yAxis));
    expect(on.series.filter((s) => !s.name.startsWith("EMA ")).map((s) => s.data)).toEqual(
      off.series.map((s) => s.data),
    );
    fireEvent.click(pressed(5));
    expect(plain(last())).toBe(plain(off));
  });

  it("offers no control and draws no line in the dashboard tile, whatever is remembered", () => {
    window.localStorage.setItem(EMA_STORAGE_KEY, "[5]");
    chart(RUN, { compact: true });
    expect(screen.queryByRole("group", { name: /moving averages/i })).toBeNull();
    expect(emaLines()).toHaveLength(0);
    expect(screen.queryByText(/EMA \d+ ·/)).toBeNull();
  });

  it("restores the remembered choice shared with the other charts", () => {
    window.localStorage.setItem(EMA_STORAGE_KEY, "[10]");
    chart();
    expect(pressed(10)).toHaveAttribute("aria-pressed", "true");
    expect(emaLines().map((s) => s.name)).toEqual(["EMA 10"]);
  });
});
