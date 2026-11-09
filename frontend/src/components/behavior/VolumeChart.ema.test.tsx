import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VolumeChart } from "./VolumeChart";
import { EMA_STORAGE_KEY } from "./EmaToggles";
import type { MetricBucket } from "@/lib/api";

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

const T0 = Date.parse("2026-07-20T10:00:00Z");

function bucketsOf(
  values: Array<number | "PARTIAL">,
  seconds = 300,
): MetricBucket[] {
  return values.map((v, i) => ({
    bucket_start: new Date(T0 + i * seconds * 1000).toISOString(),
    bucket_seconds: seconds,
    event_count: 1,
    average_eps: v === "PARTIAL" ? 0.1 : v,
    peak_eps: 1,
    completeness: v === "PARTIAL" ? "PARTIAL" : "COMPLETE",
    last_event_at: null,
  }));
}

const RUN = bucketsOf([2, 4, 6, 10, 0, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35]);
const WITH_GAP = bucketsOf([2, 4, 6, 10, "PARTIAL", 3, 5, 7, 9, 11, 13]);

interface Opt {
  yAxis: unknown;
  tooltip: { formatter: (p: unknown) => string };
  series: Array<{
    name: string;
    data: Array<[number, number | null]>;
    connectNulls?: boolean;
    showSymbol?: boolean;
    z?: number;
    lineStyle?: { color?: string; width?: number };
  }>;
}

function lastOption(): Opt {
  return setOption.mock.calls[setOption.mock.calls.length - 1][0] as Opt;
}

function chart(buckets: MetricBucket[] = RUN) {
  return render(<VolumeChart buckets={buckets} expected={2} expectedLow={1} expectedHigh={3} />);
}

function pressed(period: number) {
  return screen.getByRole("button", { name: `EMA ${period}` });
}

function emaSeries(opt: Opt = lastOption()) {
  return opt.series.filter((s) => s.name.startsWith("EMA "));
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
  // jsdom resolves no custom properties: give the chart tokens distinct values.
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => `tok(${name})`,
  } as unknown as CSSStyleDeclaration);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("EMA control", () => {
  it("is a labelled group of four unpressed buttons, all off by default", () => {
    chart();
    const group = screen.getByRole("group", { name: /moving averages/i });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["3", "5", "10", "20"]);
    for (const b of buttons) expect(b).toHaveAttribute("aria-pressed", "false");
    expect(emaSeries()).toHaveLength(0);
  });

  it("toggles aria-pressed and the chart's lines", () => {
    chart();
    fireEvent.click(pressed(5));
    expect(pressed(5)).toHaveAttribute("aria-pressed", "true");
    expect(pressed(20)).toHaveAttribute("aria-pressed", "false");
    expect(emaSeries().map((s) => s.name)).toEqual(["EMA 5"]);
    fireEvent.click(pressed(5));
    expect(pressed(5)).toHaveAttribute("aria-pressed", "false");
    expect(emaSeries()).toHaveLength(0);
  });

  it("remembers the choice and restores it after mount", () => {
    const first = chart();
    fireEvent.click(pressed(5));
    fireEvent.click(pressed(20));
    expect(window.localStorage.getItem(EMA_STORAGE_KEY)).toBe("[5,20]");
    first.unmount();
    chart();
    expect(pressed(5)).toHaveAttribute("aria-pressed", "true");
    expect(pressed(20)).toHaveAttribute("aria-pressed", "true");
    expect(pressed(3)).toHaveAttribute("aria-pressed", "false");
    expect(emaSeries().map((s) => s.name)).toEqual(["EMA 5", "EMA 20"]);
  });

  it("ignores junk in the remembered slot", () => {
    window.localStorage.setItem(EMA_STORAGE_KEY, '{"x":1}');
    chart();
    expect(emaSeries()).toHaveLength(0);
    window.localStorage.setItem(EMA_STORAGE_KEY, "[7, 5, \"x\"]");
    chart();
    expect(screen.getAllByRole("button", { name: "EMA 5", pressed: true }).length).toBe(1);
  });

  it("does not break when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    chart();
    fireEvent.click(pressed(10));
    expect(pressed(10)).toHaveAttribute("aria-pressed", "true");
    expect(emaSeries().map((s) => s.name)).toEqual(["EMA 10"]);
  });

  it("renders all off on the first render, before the remembered choice is restored", () => {
    window.localStorage.setItem(EMA_STORAGE_KEY, "[5]");
    const seen: string[] = [];
    const real = setOption.getMockImplementation();
    setOption.mockImplementation((o: Opt) => {
      seen.push(o.series.filter((s) => s.name.startsWith("EMA ")).map((s) => s.name).join());
      return real?.(o);
    });
    chart();
    // The first chart is built without a line (as the server render would have it).
    expect(seen[0]).toBe("");
    expect(seen[seen.length - 1]).toBe("EMA 5");
  });
});

describe("EMA chart option", () => {
  it("draws exactly the enabled lines: thin, no symbols, not bridged, token colours, under the observed line", () => {
    chart();
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(5));
    fireEvent.click(pressed(10));
    fireEvent.click(pressed(20));
    const opt = lastOption();
    const lines = emaSeries(opt);
    expect(lines.map((s) => s.name)).toEqual(["EMA 3", "EMA 5", "EMA 10", "EMA 20"]);
    const observed = opt.series.find((s) => s.name === "Observed EPS")!;
    const colours = lines.map((s) => s.lineStyle?.color);
    expect(colours).toEqual([
      "tok(--chart-cat-2)",
      "tok(--chart-cat-3)",
      "tok(--chart-cat-4)",
      "tok(--chart-cat-5)",
    ]);
    expect(new Set(colours).size).toBe(4);
    expect(colours).not.toContain(observed.lineStyle?.color);
    for (const s of lines) {
      expect(s.connectNulls).toBe(false);
      expect(s.showSymbol).toBe(false);
      expect(s.lineStyle?.width).toBeLessThan(observed.lineStyle!.width!);
      expect(s.z).toBeLessThan(observed.z!);
    }
  });

  it("breaks every line at an unobserved bucket", () => {
    chart(WITH_GAP);
    fireEvent.click(pressed(3));
    const data = emaSeries()[0].data;
    expect(data[4][1]).toBeNull();
    // Warm-up restarts after the partial bucket: nothing before index 4 + 3.
    expect(data[5][1]).toBeNull();
    expect(data[6][1]).toBeNull();
    expect(data[7][1]).not.toBeNull();
    expect(data[3][1]).not.toBeNull();
  });

  it("does not move the y-axis when a line is switched on", () => {
    chart();
    const before = lastOption().yAxis;
    for (const p of [3, 5, 10, 20]) fireEvent.click(pressed(p));
    expect(lastOption().yAxis).toEqual(before);
    const observed = lastOption().series.find((s) => s.name === "Observed EPS")!.data;
    const max = Math.max(...observed.map((d) => d[1] ?? -Infinity));
    for (const s of emaSeries()) {
      for (const d of s.data) expect(d[1] ?? 0).toBeLessThanOrEqual(max);
    }
  });

  it("adds an EMA row to the tooltip only where the line has a value", () => {
    chart(WITH_GAP);
    fireEvent.click(pressed(3));
    const fmt = lastOption().tooltip.formatter;
    const at = (i: number) => fmt([{ dataIndex: i }]);
    expect(at(0)).not.toContain("EMA 3");
    expect(at(2)).toContain("EMA 3: 4.00 EPS");
    expect(at(4)).not.toContain("EMA 3");
    expect(at(7)).toContain("EMA 3:");
  });
});

describe("EMA legend, note and text", () => {
  it("shows no EMA legend entry and no note while all are off", () => {
    chart();
    expect(screen.queryByText(/EMA lines are drawn in your browser/)).toBeNull();
    const key = screen.getByRole("list", { name: "Chart key" });
    expect(within(key).queryByText(/EMA/)).toBeNull();
  });

  it("states the span of each average from 5-minute buckets", () => {
    chart();
    fireEvent.click(pressed(20));
    const key = screen.getByRole("list", { name: "Chart key" });
    expect(within(key).getByText("EMA 20 · 100 min")).toBeInTheDocument();
  });

  it("states the span from hourly buckets", () => {
    chart(bucketsOf([1, 2, 3, 4, 5], 3600));
    fireEvent.click(pressed(3));
    expect(screen.getByText("EMA 3 · 3 h")).toBeInTheDocument();
  });

  it("falls back to buckets when the bucket length is not uniform", () => {
    const mixed = bucketsOf([1, 2, 3, 4, 5]);
    mixed[2] = { ...mixed[2], bucket_seconds: 600 };
    chart(mixed);
    fireEvent.click(pressed(3));
    expect(screen.getByText("EMA 3 · 3 buckets")).toBeInTheDocument();
  });

  it("keeps a legend entry that says so when there are not enough consecutive buckets", () => {
    chart(bucketsOf([1, 2, "PARTIAL", 4, 5, "PARTIAL", 7, 8]));
    fireEvent.click(pressed(3));
    expect(screen.getByText(/not enough consecutive buckets/)).toBeInTheDocument();
    expect(screen.getByText(/EMA 3 · 15 min/)).toBeInTheDocument();
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(5));
    expect(screen.getByText(/not enough consecutive buckets/)).toBeInTheDocument();
  });

  it("shows the honest note only while an average is on, and names the averages in the summary", () => {
    chart();
    fireEvent.click(pressed(5));
    expect(screen.getByText(/EMA lines are drawn in your browser/)).toBeInTheDocument();
    expect(screen.getByText(/do not take part in detection/)).toBeInTheDocument();
    expect(screen.getByText(/a line stops at a bucket that was not fully observed/)).toBeInTheDocument();
    expect(screen.getByText(/Moving-average overlays on: 5 buckets/)).toBeInTheDocument();
    fireEvent.click(pressed(5));
    expect(screen.queryByText(/EMA lines are drawn in your browser/)).toBeNull();
  });
});
