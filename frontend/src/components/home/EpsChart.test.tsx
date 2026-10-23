import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { epsPoint, epsSeries } from "@/lib/home/fixtures";

import { EpsChart } from "./EpsChart";
import { failed, forbidden, loading, ok } from "./testkit";

const { setOption, resize, dispose, init, use, on } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  const on = vi.fn();
  return { setOption, resize, dispose, on, init: vi.fn(() => ({ setOption, resize, dispose, on })), use: vi.fn() };
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

const res = vi.hoisted(() => ({ state: undefined as unknown, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => {
    res.keys.push(key ?? "");
    return res.state ?? { status: "loading" };
  },
}));

const pts = (...eps: Array<number | null>) => eps.map((e, i) => epsPoint(i, e, e === null ? null : 4));
const option = () =>
  setOption.mock.calls[0][0] as { series: Array<{ name: string; data: Array<[number, number | null]> }> };

beforeEach(() => {
  res.state = undefined;
  res.keys = [];
  setOption.mockClear();
  dispose.mockClear();
  init.mockClear();
});

describe("EpsChart", () => {
  it("draws the observed line with a null for the unmeasured step, and one chart instance", () => {
    res.state = ok(epsSeries(pts(5, null, 6, 7)));
    render(<EpsChart />);
    expect(init).toHaveBeenCalledTimes(1);
    const observed = option().series.find((s) => s.name === "Observed EPS")!;
    expect(observed.data.map((d) => d[1])).toEqual([5, null, 6, 7]);
    expect(screen.getByRole("img", { name: /Fleet EPS/ })).toBeInTheDocument();
    expect(screen.getByRole("img").closest(".tw-body-fill")).not.toBeNull();
  });

  it("says Partial with the count when some steps are null", () => {
    res.state = ok(epsSeries(pts(5, null, 6, 7)));
    render(<EpsChart />);
    expect(screen.getByText(/Partial: 3 of 4 steps collected/)).toBeInTheDocument();
  });

  it("labels the expected line and says it is not a range", () => {
    res.state = ok(epsSeries(pts(5, 6)));
    render(<EpsChart />);
    const key = screen.getByRole("list", { name: "Chart key" });
    const item = within(key).getByText("Sum of per-source expected").closest("li")!;
    expect(item).toHaveAttribute("title", expect.stringMatching(/not a range/));
    expect(option().series.map((s) => s.name)).toContain("Sum of per-source expected");
    expect(screen.getByText(/not a range/, { selector: ".sr-only" })).toBeInTheDocument();
  });

  it("has no expected line, no key entry and says so when no baseline is reliable", () => {
    res.state = ok(epsSeries([epsPoint(0, 5), epsPoint(1, 6)]));
    render(<EpsChart />);
    expect(option().series.map((s) => s.name)).toEqual(["Observed EPS"]);
    expect(screen.queryByText("Sum of per-source expected")).not.toBeInTheDocument();
    expect(screen.getByText(/No expected line/)).toBeInTheDocument();
  });

  it("keys the shaded gap only when there is one", () => {
    res.state = ok(epsSeries([epsPoint(0, 5), epsPoint(1, 6)]));
    const { unmount } = render(<EpsChart />);
    expect(screen.queryByText("Not measured")).not.toBeInTheDocument();
    unmount();
    res.state = ok(epsSeries(pts(5, null, 6)));
    render(<EpsChart />);
    expect(screen.getByText("Not measured")).toBeInTheDocument();
  });

  it("is NOT_COLLECTED, with no chart and no zero, when no step is measured", () => {
    res.state = ok(epsSeries(pts(null, null, null)));
    render(<EpsChart />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText(/No step of 3 has every in-scope source-interval complete/)).toBeInTheDocument();
    expect(screen.getByText(/not zero traffic/)).toBeInTheDocument();
  });

  it("links the title row to /fleet in every state", () => {
    for (const state of [loading, forbidden, failed(500), ok(epsSeries(pts(5, 6)))]) {
      res.state = state;
      const { unmount } = render(<EpsChart />);
      expect(screen.getByRole("link", { name: "Fleet" })).toHaveAttribute("href", "/fleet?window=24h");
      unmount();
    }
  });

  it.each(["1h", "7d"] as const)("keeps window %s on the fleet link", (window) => {
    res.state = loading;
    render(<EpsChart range={window} />);
    expect(screen.getByRole("link", { name: "Fleet" })).toHaveAttribute("href", `/fleet?window=${window}`);
  });

  it("renders loading, forbidden and error as words", () => {
    res.state = loading;
    const a = render(<EpsChart />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading the fleet EPS series/);
    a.unmount();
    res.state = forbidden;
    const b = render(<EpsChart />);
    expect(screen.getByText(/read:fleet/)).toBeInTheDocument();
    b.unmount();
    res.state = failed(502);
    render(<EpsChart />);
    expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 502/);
    expect(init).not.toHaveBeenCalled();
  });

  it("keeps the chart and says so when a refresh failed", () => {
    res.state = ok(epsSeries(pts(5, 6)), { refreshFailed: { httpStatus: 500 } });
    render(<EpsChart />);
    expect(init).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/latest refresh failed/)).toBeInTheDocument();
  });

  it("disposes the chart on unmount", () => {
    res.state = ok(epsSeries(pts(5, 6)));
    render(<EpsChart />).unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});
