import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { setOption, resize, dispose, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return { setOption, resize, dispose, init: vi.fn(() => ({ setOption, resize, dispose })), use: vi.fn() };
});

// jsdom has no canvas, so ECharts is stubbed; the real `format` is kept because the tooltip
// encoding is part of what is under test.
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

import { bucket, HOSTILE, iso, point, series } from "./fixtures";
import { PropertyWatchSeriesChart } from "./PropertyWatchSeriesChart";

class TestResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

beforeEach(() => {
  setOption.mockClear();
  dispose.mockClear();
  init.mockClear();
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
});

const W = { windowStart: iso(0), windowEnd: iso(3), bucketSeconds: 300 };
const s = (n: number, key = `v${n}`) => series(`id-${n}`, key, [point(0), point(1), point(3)]);
const option = () => setOption.mock.calls[0][0] as { series: Array<{ name: string; connectNulls: boolean }> };

describe("PropertyWatchSeriesChart", () => {
  it("initialises once, draws the option and disposes on unmount", () => {
    const { unmount } = render(<PropertyWatchSeriesChart series={[s(1)]} {...W} />);
    expect(init).toHaveBeenCalledTimes(1);
    expect(setOption).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("img", { name: /Events per bucket/ })).toBeInTheDocument();
    unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("never lets a gap connect", () => {
    render(<PropertyWatchSeriesChart series={[s(1)]} {...W} />);
    for (const ser of option().series) expect(ser.connectNulls).toBe(false);
  });

  it("draws at most five values and says so", () => {
    render(<PropertyWatchSeriesChart series={[1, 2, 3, 4, 5, 6, 7].map((n) => s(n))} {...W} />);
    expect(option().series.filter((x) => x.name.startsWith("v"))).toHaveLength(5);
    expect(screen.getByText(/2 more selected values are not drawn/)).toBeInTheDocument();
  });

  it("keys each value with a categorical token and prints the value as text", () => {
    const { container } = render(<PropertyWatchSeriesChart series={[s(1, HOSTILE), s(2)]} {...W} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    const keys = container.querySelectorAll(".pw-key");
    expect(keys[0]).toHaveClass("pw-key-1");
    expect(keys[1]).toHaveClass("pw-key-2");
  });

  it("explains the band only when one value is plotted", () => {
    const { rerender } = render(<PropertyWatchSeriesChart series={[s(1)]} {...W} />);
    expect(screen.getByText("Expected range for the hour of the week")).toBeInTheDocument();
    expect(screen.queryByText(/select a single value/)).toBeNull();
    rerender(<PropertyWatchSeriesChart series={[s(1), s(2)]} {...W} />);
    expect(screen.queryByText("Expected range for the hour of the week")).toBeNull();
    expect(screen.getByText(/select a single value to see its band/)).toBeInTheDocument();
  });

  it("keys the marks it drew: not collected, a lower bound, an incident", () => {
    const lb = series("l", "lb", [point(0, { measurement: "LOWER_BOUND" }), point(1), point(3)]);
    render(
      <PropertyWatchSeriesChart
        series={[lb]}
        {...W}
        incidents={[{ start: iso(1), end: iso(2) }]}
      />,
    );
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("At least this many (lower bound)")).toBeInTheDocument();
    expect(screen.getByText("Incident")).toBeInTheDocument();
  });

  it("labels history points and provides their spans in a text table", () => {
    render(
      <PropertyWatchSeriesChart
        series={[series("h", "history", [point(0, { origin: "HISTORY" }), point(1)])]}
        {...W}
      />,
    );
    expect(screen.getAllByText("Read from QRadar history, not judged")).toHaveLength(2);
    const table = screen.getByRole("table", { name: "QRadar history shown in the chart" });
    expect(table).toHaveTextContent("Read from QRadar history, not judged");
    expect(table).toHaveTextContent(iso(0));
  });

  it("says a gap is not zero, as text", () => {
    render(<PropertyWatchSeriesChart series={[s(1)]} {...W} />);
    expect(screen.getByText(/not zero events; a line at 0 is a measured zero/)).toBeInTheDocument();
  });

  it("adds the parsed-share panel only when buckets are given, and not in compact form", () => {
    const buckets = [bucket(0), bucket(1)];
    const { rerender } = render(
      <PropertyWatchSeriesChart series={[s(1)]} {...W} coverage={{ buckets, floor: 0.9 }} />,
    );
    expect(option().series.some((x) => x.name === "Parsed share")).toBe(true);
    setOption.mockClear();
    rerender(<PropertyWatchSeriesChart series={[s(1)]} {...W} coverage={{ buckets, floor: 0.9 }} compact />);
    expect(option().series.some((x) => x.name === "Parsed share")).toBe(false);
  });

  it("keeps the parsed share on screen when no value is plotted", () => {
    render(
      <PropertyWatchSeriesChart
        series={[]}
        {...W}
        collected={[iso(0), iso(1)]}
        coverage={{ buckets: [bucket(0, { parsed_events: 0, unparsed_events: 1000 }), bucket(1)], floor: 0.9 }}
        emptyText="Select values."
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Select values.");
    const o = option();
    expect(o.series.map((x) => x.name)).toEqual(["Parsed share"]);
    expect((o.series[0] as unknown as { data: Array<[number, number | null]> }).data[0][1]).toBe(0);
  });

  it("draws no named image when nothing is plotted", () => {
    render(<PropertyWatchSeriesChart series={[]} {...W} emptyText="Select values." />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Select values.");
  });
});
