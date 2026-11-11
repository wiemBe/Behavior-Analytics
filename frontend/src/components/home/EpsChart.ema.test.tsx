import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMA_STORAGE_KEY } from "@/components/behavior/EmaToggles";
import { epsPoint, epsSeries } from "@/lib/home/fixtures";
import { WidgetRuntimeContext } from "@/widgets/runtime";

import { EpsChart } from "./EpsChart";
import { ok } from "./testkit";

const { setOption, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  const on = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize, dispose, on })), use: vi.fn() };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const res = vi.hoisted(() => ({ state: undefined as unknown }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => res.state ?? { status: "loading" },
}));

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
const last = () => setOption.mock.calls[setOption.mock.calls.length - 1][0] as Opt;
const emaLines = (o: Opt = last()) => o.series.filter((s) => s.name.startsWith("EMA "));
const plain = (o: Opt) => JSON.stringify(o);
const dataOf = (name: string) => last().series.find((s) => s.name === name)!.data.map((d) => d[1]);
const pressed = (period: number) => screen.getByRole("button", { name: `EMA ${period}` });

function runtime(expanded: boolean, children: ReactNode) {
  return (
    <WidgetRuntimeContext.Provider
      value={{
        rangeOptions: null,
        override: null,
        dashboardRange: "24h",
        setRange: () => undefined,
        onExpand: null,
        expanded,
      }}
    >
      {children}
    </WidgetRuntimeContext.Provider>
  );
}
const expandedChart = () => render(runtime(true, <EpsChart />));
const tile = () => render(runtime(false, <EpsChart />));

const pts = (...eps: Array<number | null>) => eps.map((e, i) => epsPoint(i, e, e === null ? null : 4));
const RUN = pts(2, 4, 6, 10, 0, 3, 5, 7, 9, 11, 13, 15);
const WITH_GAP = pts(2, 4, 6, 10, null, 3, 5, 7, 9, 11, 13);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  res.state = undefined;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => `tok(${name})`,
  } as unknown as CSSStyleDeclaration);
});
afterEach(() => vi.restoreAllMocks());

describe("EpsChart EMA overlay, expanded view", () => {
  it("offers the control, all off, and draws no EMA line by default", () => {
    res.state = ok(epsSeries(RUN));
    expandedChart();
    const group = screen.getByRole("group", { name: /moving averages/i });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["3", "5", "10", "20"]);
    for (const b of buttons) expect(b).toHaveAttribute("aria-pressed", "false");
    expect(emaLines()).toHaveLength(0);
    expect(screen.queryByText(/drawn in your browser/)).toBeNull();
  });

  it("draws exactly the enabled lines: thin, no symbols, not bridged, token colours, under the observed line", () => {
    res.state = ok(epsSeries(RUN));
    expandedChart();
    fireEvent.click(pressed(5));
    fireEvent.click(pressed(20));
    const lines = emaLines();
    expect(lines.map((s) => s.name)).toEqual(["EMA 5", "EMA 20"]);
    expect(lines.map((s) => s.lineStyle?.color)).toEqual(["tok(--chart-cat-3)", "tok(--chart-cat-5)"]);
    const observed = last().series.find((s) => s.name === "Observed EPS")!;
    for (const s of lines) {
      expect(s.connectNulls).toBe(false);
      expect(s.showSymbol).toBe(false);
      expect(s.lineStyle!.width).toBeLessThan(observed.lineStyle!.width!);
      expect(s.z).toBeLessThan(observed.z!);
    }
  });

  it("averages the observed EPS: the first value is the mean of the first run, a measured zero is averaged", () => {
    res.state = ok(epsSeries(RUN));
    expandedChart();
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v.slice(0, 2)).toEqual([null, null]);
    expect(v[2]).toBeCloseTo(4, 10);
    expect(v[4]).not.toBeNull();
  });

  it("ends at a step that was not collected and warms up again after it, never a zero", () => {
    res.state = ok(epsSeries(WITH_GAP));
    expandedChart();
    fireEvent.click(pressed(3));
    const v = dataOf("EMA 3");
    expect(v[3]).not.toBeNull();
    expect(v.slice(4, 7)).toEqual([null, null, null]);
    expect(v[7]).not.toBeNull();
    expect(v).not.toContain(0);
  });

  it("puts a tooltip row on a step only where the average has a value", () => {
    res.state = ok(epsSeries(WITH_GAP));
    expandedChart();
    fireEvent.click(pressed(3));
    const tip = (i: number) => last().tooltip.formatter({ dataIndex: i });
    expect(tip(0)).not.toContain("EMA 3");
    expect(tip(3)).toMatch(/EMA 3: [\d.,]+ EPS/);
    expect(tip(4)).not.toContain("EMA 3");
    expect(tip(8)).toMatch(/EMA 3: /);
  });

  it("names each enabled average in the key, and says so when a period never had a run", () => {
    res.state = ok(epsSeries(pts(2, 4, 6, null, 5, null, 7, 8)));
    expandedChart();
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(20));
    const key = screen.getByRole("list", { name: "Chart key" });
    expect(within(key).getByText(/EMA 3 · 15 min/)).toBeInTheDocument();
    const twenty = within(key).getByText(/EMA 20/).closest("li")!;
    expect(twenty).toHaveTextContent(/not enough consecutive buckets/i);
    expect(within(key).getByText(/EMA 3/).closest("li")).not.toHaveTextContent(/not enough/i);
  });

  it("shows the note and the screen-reader summary only while an average is on", () => {
    res.state = ok(epsSeries(RUN));
    expandedChart();
    expect(screen.queryByText(/drawn in your browser/)).toBeNull();
    fireEvent.click(pressed(5));
    expect(screen.getByText(/drawn in your browser/)).toBeInTheDocument();
    expect(screen.getByText(/Moving-average overlays on/, { selector: ".sr-only" })).toBeInTheDocument();
    fireEvent.click(pressed(5));
    expect(screen.queryByText(/drawn in your browser/)).toBeNull();
  });

  it("leaves the y-axis, the observed line and the expected line as they were", () => {
    res.state = ok(epsSeries(RUN));
    expandedChart();
    const off = last();
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(20));
    const on = last();
    expect(plain({ ...on, series: [] } as Opt)).toBe(plain({ ...off, series: [] } as Opt));
    expect(JSON.stringify(on.yAxis)).toBe(JSON.stringify(off.yAxis));
    expect(on.series.filter((s) => !s.name.startsWith("EMA "))).toEqual(off.series);
    fireEvent.click(pressed(3));
    fireEvent.click(pressed(20));
    expect(plain(last())).toBe(plain(off));
  });
});

describe("EpsChart EMA overlay, tile", () => {
  it("has no control, no line, no key entry and no note, whatever is stored, and the same option", () => {
    res.state = ok(epsSeries(RUN));
    const first = tile();
    const before = plain(last());
    first.unmount();
    setOption.mockClear();
    window.localStorage.setItem(EMA_STORAGE_KEY, JSON.stringify([3, 5, 10, 20]));
    tile();
    expect(screen.queryByRole("group", { name: /moving averages/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /EMA/ })).toBeNull();
    expect(emaLines()).toHaveLength(0);
    expect(plain(last())).toBe(before);
    expect(screen.queryByText(/EMA/)).toBeNull();
    expect(screen.queryByText(/drawn in your browser/)).toBeNull();
  });

  it("is not rebuilt when the stored choice is restored after mount", () => {
    res.state = ok(epsSeries(RUN));
    window.localStorage.setItem(EMA_STORAGE_KEY, JSON.stringify([5]));
    tile();
    expect(init).toHaveBeenCalledTimes(1);
  });

  it("an expanded view restores the stored choice", () => {
    res.state = ok(epsSeries(RUN));
    window.localStorage.setItem(EMA_STORAGE_KEY, JSON.stringify([5]));
    expandedChart();
    expect(pressed(5)).toHaveAttribute("aria-pressed", "true");
    expect(emaLines().map((s) => s.name)).toEqual(["EMA 5"]);
  });
});
