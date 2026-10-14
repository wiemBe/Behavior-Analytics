// The five range-declaring widgets read their effective window (UR.5.3), and the one range
// control in the frame sets the widget's override. The preset test is the one that lived in
// `EpsChart.test.tsx` ("offers 1 h, 24 h and 7 d, and a press re-reads for that window"),
// moved here with the same assertions because the control now belongs to `WidgetFrame`.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EpsByClass } from "@/components/home/EpsByClass";
import { EpsChart } from "@/components/home/EpsChart";
import { KpiTotalEps } from "@/components/home/KpiTotalEps";
import { TopSources } from "@/components/home/TopSources";
import { FleetClasses } from "@/components/catalog/FleetClasses";
import { failed, loading, ok } from "@/components/home/testkit";
import { homeReads } from "@/lib/api";
import { epsPoint, epsSeries } from "@/lib/home/fixtures";

import { WidgetRuntimeContext } from "./runtime";
import type { WidgetRange } from "./types";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on: vi.fn() })),
  };
});
vi.mock("echarts/core", () => ({ init, use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {}, PieChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const res = vi.hoisted(() => ({ state: undefined as unknown, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => {
    res.keys.push(key ?? "");
    return res.state ?? { status: "loading" };
  },
}));

beforeEach(() => {
  res.state = undefined;
  res.keys = [];
  init.mockClear();
});

/** What a dashboard does around a placed widget: holds the override, supplies the runtime. */
function Dashboard({
  dashboardRange = "24h",
  children,
}: {
  dashboardRange?: WidgetRange;
  children: (override: WidgetRange | null) => ReactNode;
}) {
  const [override, setOverride] = useState<WidgetRange | null>(null);
  return (
    <WidgetRuntimeContext.Provider
      value={{
        rangeOptions: ["1h", "24h", "7d"],
        override,
        dashboardRange,
        setRange: setOverride,
        onExpand: null,
        expanded: false,
      }}
    >
      {children(override)}
    </WidgetRuntimeContext.Provider>
  );
}

const pts = (...eps: number[]) => eps.map((e, i) => epsPoint(i, e, 4));

describe("each range-declaring widget reads its effective window and titles it", () => {
  const cases: Array<{
    name: string;
    render: (range?: WidgetRange) => ReactNode;
    keys: (w: WidgetRange) => string[];
    title: (w: string) => RegExp;
  }> = [
    {
      name: "kpi.total_eps",
      render: (r) => <KpiTotalEps range={r} />,
      keys: (w) => [homeReads.fleetSummary(w).key, homeReads.epsSeries(w, "none").key],
      title: (w) => new RegExp(`^Total EPS, ${w}$`),
    },
    {
      name: "chart.eps_fleet_total",
      render: (r) => <EpsChart range={r} />,
      keys: (w) => [homeReads.epsSeries(w, "none").key],
      title: (w) => new RegExp(`^Fleet EPS${w}$`),
    },
    {
      name: "chart.eps_by_class",
      render: (r) => <EpsByClass range={r} />,
      keys: (w) => [homeReads.fleetSummary(w).key],
      title: (w) => new RegExp(`^EPS by source class${w}$`),
    },
    {
      name: "table.top_sources",
      render: (r) => <TopSources range={r} />,
      keys: (w) => [homeReads.fleetSummary(w).key],
      title: (w) => new RegExp(`^Top log sources by EPS${w}$`),
    },
    {
      name: "table.fleet_classes",
      render: (r) => <FleetClasses range={r} />,
      keys: (w) => [homeReads.fleetSummary(w).key],
      title: (w) => new RegExp(`^Source classes${w}$`),
    },
  ];

  it.each(cases)("$name: the override, else the 24 h default", (c) => {
    res.state = loading;
    const { unmount } = render(<>{c.render("7d")}</>);
    for (const key of c.keys("7d")) expect(res.keys).toContain(key);
    expect(res.keys).not.toContain(homeReads.fleetSummary("24h").key);
    expect(res.keys).not.toContain(homeReads.epsSeries("24h", "none").key);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(c.title("7 d"));
    unmount();

    res.keys = [];
    render(<>{c.render()}</>);
    for (const key of c.keys("24h")) expect(res.keys).toContain(key);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(c.title("24 h"));
  });

  it.each(cases)("$name: follows the dashboard's range when it has no override", (c) => {
    res.state = loading;
    render(<Dashboard dashboardRange="1h">{() => c.render()}</Dashboard>);
    for (const key of c.keys("1h")) expect(res.keys).toContain(key);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(c.title("1 h"));
  });

  it("a state other than ok still titles its range and keeps the control", () => {
    res.state = failed(500);
    render(<Dashboard>{(o) => <EpsChart range={o} />}</Dashboard>);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("24 h");
    expect(screen.getByRole("group", { name: "Window" })).toBeInTheDocument();
  });
});

describe("the title-row range control", () => {
  it("offers 1 h, 24 h and 7 d, and a press re-reads for that window", () => {
    res.state = ok(epsSeries(pts(5, 6)));
    render(<Dashboard>{(o) => <EpsChart range={o} />}</Dashboard>);
    const group = screen.getByRole("group", { name: "Window" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["1 h", "24 h", "7 d"]);
    expect(within(group).getByRole("button", { name: "24 h" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(group).getByRole("button", { name: "7 d" }));
    expect(res.keys.at(-1)).toBe(homeReads.epsSeries("7d", "none").key);
    expect(within(group).getByRole("button", { name: "7 d" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "24 h" })).toHaveAttribute("aria-pressed", "false");
  });

  it("is a select at KPI density, with the same presets", () => {
    res.state = loading;
    render(<Dashboard>{(o) => <KpiTotalEps range={o} />}</Dashboard>);
    const select = screen.getByRole("combobox", { name: "Window for Total EPS, 24 h" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["1 h", "24 h", "7 d"]);
    fireEvent.change(select, { target: { value: "7d" } });
    expect(res.keys.at(-1)).toBe(homeReads.epsSeries("7d", "none").key);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Total EPS, 7 d");
  });

  it("is absent outside a dashboard, so a bare widget shows no control", () => {
    res.state = loading;
    render(<EpsChart />);
    expect(screen.queryByRole("group", { name: "Window" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Expand/ })).toBeNull();
  });
});
