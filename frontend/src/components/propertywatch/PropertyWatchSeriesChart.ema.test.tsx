import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { iso, point, series as pwSeries, HOSTILE } from "./fixtures";
import { PropertyWatchSeriesChart } from "./PropertyWatchSeriesChart";
import { EMA_STORAGE_KEY } from "@/components/behavior/EmaToggles";

const { setOption, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize, dispose })), use: vi.fn() };
});

vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
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

type Row = number | "GAP" | "FLOOR" | "UNKNOWN";
const W = (n: number) => ({ windowStart: iso(0), windowEnd: iso(n), bucketSeconds: 300 });

/** One value's points; a "GAP" is a bucket that was not collected (no point at all). */
function line(id: string, key: string, values: Row[]) {
  const points = values.flatMap((v, i) => {
    if (v === "GAP") return [];
    if (v === "UNKNOWN") return [point(i, { reading: "UNKNOWN", events: null, measurement: null })];
    if (v === "FLOOR") return [point(i, { events: 50, measurement: "LOWER_BOUND" })];
    if (v === 0) return [point(i, { reading: "MEASURED_ZERO", events: 0, measurement: "EXACT" })];
    return [point(i, { events: v })];
  });
  return pwSeries(id, key, points);
}
const RUN: Row[] = [2, 4, 6, 10, 0, 3, 5, 7, 9, 11, 13, 15];
const WITH_GAP: Row[] = [2, 4, 6, 10, "GAP", 3, 5, 7, 9, 11, 13];

function chart(values: Row[] = RUN, over: Partial<Parameters<typeof PropertyWatchSeriesChart>[0]> = {}) {
  return render(
    <PropertyWatchSeriesChart series={[line("id-1", "alice", values)]} {...W(values.length)} {...over} />,
  );
}
const pressed = (period: number) => screen.getByRole("button", { name: `EMA ${period}` });
const dataOf = (name: string) => last().series.find((s) => s.name === name)!.data.map((d) => d[1]);
const observedOf = (o: Opt = last()) =>
  o.series[o.series.findIndex((s) => !s.name.startsWith("EMA "))];

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => `tok(${name})`,
  } as unknown as CSSStyleDeclaration);
});
afterEach(() => vi.restoreAllMocks());

describe("PropertyWatchSeriesChart EMA overlay", () => {
  it("offers the control, all off, and draws no EMA line by default", () => {
    chart();
    const group = screen.getByRole("group", { name: /moving averages/i });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["3", "5", "10", "20"]);
    for (const b of buttons) expect(b).toHaveAttribute("aria-pressed", "false");
    expect(emaLines()).toHaveLength(0);
    expect(last().series[0].name).toBe("alice");
    expect(screen.queryByText(/drawn in your browser/)).toBeNull();
    expect(screen.queryByText(/EMA \d+ ·/)).toBeNull();
  });

  it("draws exactly the enabled lines: thin, no symbols, not bridged, token colours, under the observed line", () => {
    chart();
    fireEvent.click(pressed(5));
    fireEvent.click(pressed(20));
    const lines = emaLines();
    expect(lines.map((s) => s.name)).toEqual(["EMA 5", "EMA 20"]);
    expect(lines.map((s) => s.lineStyle?.color)).toEqual(["tok(--chart-cat-3)", "tok(--chart-cat-5)"]);
    const observed = observedOf();
    for (const s of lines) {
      expect(s.connectNulls).toBe(false);
      expect(s.showSymbol).toBe(false);
      expect(s.lineStyle!.width).toBeLessThan(observed.lineStyle!.width!);
      expect(s.z).toBeLessThan(observed.z!);
    }
  });

  it("averages the observed counts and treats a measured zero as a zero", () => {
    chart();
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v.slice(0, 2)).toEqual([null, null]);
    expect(v[2]).toBeCloseTo(4, 10);
    // 10 -> 0 at index 4: alpha 0.5, so (0 + 6.5 ... ) is below the earlier value, not skipped.
    expect(v[4]).toBeLessThan(v[3] as number);
  });

  it("ends the line at a bucket that was not collected and warms up again after it", () => {
    chart(WITH_GAP);
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v[3]).not.toBeNull();
    expect(v[4]).toBeNull();
    expect(v[5]).toBeNull();
    expect(v[6]).toBeNull();
    expect(v[7]).not.toBeNull();
  });

  it("ends the line at an unknown reading", () => {
    chart([2, 4, 6, 10, "UNKNOWN", 3, 5, 7]);
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v[3]).not.toBeNull();
    expect(v[4]).toBeNull();
    expect(v[6]).toBeNull();
    expect(v[7]).not.toBeNull();
  });

  it("ends the line at a lower-bound bucket, which is not an exact count", () => {
    chart([2, 4, 6, 10, "FLOOR", 3, 5, 7, 9]);
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v[3]).not.toBeNull();
    expect(v[4]).toBeNull();
    expect(v[6]).toBeNull();
    expect(v[7]).not.toBeNull();
  });

  const TWO = () => [
    line("id-1", "alice", RUN),
    line("id-2", "bob", RUN.map((v) => (v === "GAP" ? v : (v as number) + 100))),
  ];

  it("averages the single plotted value, and the legend and tooltip name it", () => {
    chart();
    fireEvent.click(pressed(3));
    expect(emaLines().map((s) => s.name)).toEqual(["EMA 3"]);
    expect(dataOf("EMA 3")[2]).toBeCloseTo(4, 10);
    expect(screen.getByText(/EMA 3 · 15 min of alice/)).toBeInTheDocument();
    expect(last().tooltip.formatter({ dataIndex: 2 })).toContain("EMA 3 of alice: 4.0");
    expect(screen.queryByText(/drawn for one value at a time/)).toBeNull();
  });

  it("draws nothing with several values plotted, and says why, while the control keeps the choice", () => {
    window.localStorage.setItem(EMA_STORAGE_KEY, "[5, 20]");
    render(<PropertyWatchSeriesChart series={TWO()} {...W(RUN.length)} />);
    expect(emaLines()).toHaveLength(0);
    expect(screen.queryByText(/EMA \d+ ·/)).toBeNull();
    expect(screen.queryByText(/do not take part in detection/)).toBeNull();
    expect(screen.queryByText(/overlays of/)).toBeNull();
    expect(last().tooltip.formatter({ dataIndex: 2 })).not.toMatch(/EMA/);
    expect(screen.getByText(/EMA lines are drawn for one value at a time/)).toBeInTheDocument();
    // The stored choice is shown and stays reachable.
    expect(pressed(5)).toHaveAttribute("aria-pressed", "true");
    expect(pressed(5)).not.toBeDisabled();
    expect(window.localStorage.getItem(EMA_STORAGE_KEY)).toBe("[5, 20]");
  });

  it("still stores a choice toggled while several values are plotted", () => {
    render(<PropertyWatchSeriesChart series={TWO()} {...W(RUN.length)} />);
    fireEvent.click(pressed(10));
    expect(pressed(10)).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem(EMA_STORAGE_KEY)).toBe("[10]");
    expect(emaLines()).toHaveLength(0);
  });

  it("never gives the single value's line a colour an average line uses", () => {
    chart();
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(5));
    fireEvent.click(pressed(10));
    fireEvent.click(pressed(20));
    const observed = observedOf().lineStyle!.color;
    for (const s of emaLines()) expect(s.lineStyle!.color).not.toBe(observed);
  });

  it("encodes a hostile value name in the tooltip row", () => {
    render(
      <PropertyWatchSeriesChart series={[line("id-1", HOSTILE, RUN)]} {...W(RUN.length)} />,
    );
    fireEvent.click(pressed(3));
    const html = last().tooltip.formatter({ dataIndex: 2 });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("puts an EMA row in the tooltip only where it has a value", () => {
    chart(WITH_GAP);
    fireEvent.click(pressed(3));
    const tip = last().tooltip.formatter;
    expect(tip({ dataIndex: 2 })).toMatch(/EMA 3 of alice: 4\.0 events/);
    expect(tip({ dataIndex: 1 })).not.toMatch(/EMA/);
    expect(tip({ dataIndex: 4 })).not.toMatch(/EMA/);
  });

  it("keeps a legend entry with the span, and says when a period never has a run", () => {
    chart([2, 4]);
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(20));
    expect(screen.getByText(/EMA 3 · 15 min of alice/)).toBeInTheDocument();
    expect(screen.getByText(/EMA 20 · 100 min of alice/)).toBeInTheDocument();
    expect(screen.getAllByText(/not enough consecutive buckets/)).toHaveLength(2);
  });

  it("shows the display-only note and the screen-reader summary only while a period is on", () => {
    chart();
    expect(screen.queryByText(/do not take part in detection/)).toBeNull();
    fireEvent.click(pressed(5));
    expect(screen.getByText(/do not take part in detection/)).toBeInTheDocument();
    expect(screen.getByText(/overlays of alice on: 5 buckets/)).toBeInTheDocument();
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

  it("offers no control and draws no line in a widget body, whatever is remembered", () => {
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
