// The two subject-chart widgets (UI-3.5b): one WAF endpoint's requests and one log source's EPS.
// Each through its states: not configured, loading, failed, an empty window (absence of
// collection, never zero), INSUFFICIENT_DATA, PARTIAL, TRUNCATED, a configured id that is gone,
// and the effective range carried by the read.

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { failed, forbidden, loading, ok } from "@/components/home/testkit";
import { wafBaselineCell, wafEndpoint } from "@/components/waf/fixtures";
import { homeReads } from "@/lib/api";
import { behavior } from "@/lib/home/fixtures";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { SourceEps } from "./SourceEps";
import { EP, SOURCE, SRC, epsCell, eps, reqAt, series } from "./subjectFixtures";
import { WafEndpointTraffic } from "./WafEndpointTraffic";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on: vi.fn() })),
  };
});
vi.mock("echarts/core", () => ({ init, use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown>, asked: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => {
    if (key !== null) res.asked.push(key);
    return key === null ? { status: "idle" } : (res.byKey[key] ?? { status: "loading" });
  },
}));

beforeEach(() => {
  res.byKey = {};
  res.asked = [];
  setOption.mockClear();
  init.mockClear();
});

const runtime = (over: Partial<WidgetRuntime> = {}): WidgetRuntime => ({
  rangeOptions: ["1h", "24h", "7d"],
  override: null,
  dashboardRange: "24h",
  setRange: vi.fn(),
  onExpand: null,
  expanded: false,
  onConfigure: null,
  ...over,
});

interface Drawn {
  series: Array<{ name: string; data: Array<[number, number | null]> }>;
}
const drawn = () => setOption.mock.calls.at(-1)![0] as Drawn;
const line = (name: string) => drawn().series.find((s) => s.name === name)!.data;

// --- WAF endpoint traffic ------------------------------------------------------------------

describe("waf.endpoint_traffic", () => {
  const cfg = { waf_source: SRC, endpoint: EP };
  const EPS = homeReads.wafEndpointsOf(SRC).key;
  const SER = (w: "1h" | "24h" | "7d" = "24h") => homeReads.wafEndpointSeries(EP, w).key;
  const CELLS = homeReads.wafEndpointCells(EP).key;
  const endpoints = (id = EP) => ok({ items: [wafEndpoint({ id })], total: 1, limit: 200, offset: 0 });

  function set(over: Record<string, unknown>) {
    res.byKey = { [EPS]: endpoints(), [CELLS]: ok([wafBaselineCell()]), ...over };
  }

  it("not configured: says so, reads nothing and draws no chart", () => {
    render(<WafEndpointTraffic config={{ waf_source: null, endpoint: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Choose an endpoint.");
    expect(screen.getByRole("status")).toHaveTextContent("Open Customize, then Configure");
    expect(screen.queryByRole("button", { name: "Configure" })).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(init).not.toHaveBeenCalled();
    expect(res.asked).toEqual([]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("not configured while customizing offers Configure, from the trigger it was pressed on", () => {
    const onConfigure = vi.fn();
    render(
      <WidgetRuntimeContext.Provider value={runtime({ onConfigure })}>
        <WafEndpointTraffic config={{ waf_source: SRC, endpoint: null }} />
      </WidgetRuntimeContext.Provider>,
    );
    const button = screen.getByRole("button", { name: "Configure" });
    fireEvent.click(button);
    expect(onConfigure).toHaveBeenCalledWith(button);
    expect(screen.getByRole("status")).not.toHaveTextContent("Open Customize");
    expect(init).not.toHaveBeenCalled();
  });

  it("is polite loading text, with the drill-down already there", () => {
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading/);
    expect(screen.getByRole("link", { name: "Open this source's endpoint list. It cannot select this endpoint." })).toHaveAttribute(
      "href",
      `/behavior/waf/sources/${SRC}`,
    );
  });

  it("says what failed, with the HTTP status and no exception text", () => {
    set({ [SER()]: failed(500) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be read");
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500");
    expect(init).not.toHaveBeenCalled();
  });

  it("names the permission when the role cannot read it", () => {
    set({ [SER()]: forbidden });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent("permission read:anomalies");
  });

  it("titles the widget with the endpoint and the range, and leads to its source", () => {
    set({ [SER()]: ok(series([reqAt(0), reqAt(5)])) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "GET /portal/login · portal.lab.example24 h",
    );
    const sourceLink = screen.getByRole("link", {
      name: "Open this source's endpoint list. It cannot select this endpoint.",
    });
    expect(sourceLink).toHaveAttribute("href", `/behavior/waf/sources/${SRC}`);
    expect(sourceLink.getAttribute("href")).not.toMatch(/endpoint=/);
  });

  it("draws the requests against the band of the bucket's weekday and hour", () => {
    set({ [SER()]: ok(series([reqAt(0), reqAt(5)])) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/requests per 5 min against the expected range/);
    expect(line("Requests").map((p) => p[1])).toEqual([41, 41]);
    expect(line("Expected low").map((p) => p[1])).toEqual([22, 22]);
    expect(screen.queryByText(/Insufficient|Partial|Truncated/)).toBeNull();
  });

  it("an empty window is an absence of collection, never zero requests", () => {
    set({ [SER()]: ok(series([])) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText(/absence of collection, not zero requests/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(init).not.toHaveBeenCalled();
  });

  it("INSUFFICIENT_DATA: no reliable cell means no band, a pill and the reason", () => {
    set({
      [SER()]: ok(series([reqAt(0), reqAt(5)])),
      [CELLS]: ok([wafBaselineCell({ is_reliable: false })]),
    });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText(/No reliable expected range for this window yet/)).toBeInTheDocument();
    expect(line("Expected low")).toEqual([]);
    expect(line("Requests").map((p) => p[1])).toEqual([41, 41]);
  });

  it("an unreadable baseline says the range could not be read and draws none", () => {
    set({ [SER()]: ok(series([reqAt(0), reqAt(5)])), [CELLS]: failed(500) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText(/could not be read, so none is drawn/)).toBeInTheDocument();
    expect(line("Expected low")).toEqual([]);
  });

  it("PARTIAL: a bucket that was not fully collected is a null on the line and is counted", () => {
    set({
      [SER()]: ok(series([reqAt(0), reqAt(5, { completeness: "PARTIAL", request_count: 12 }), reqAt(10)])),
    });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText("Partial: 2 of 3 stored buckets collected")).toBeInTheDocument();
    expect(line("Requests").map((p) => p[1])).toEqual([41, null, 41]);
  });

  it("TRUNCATED: a read that stopped early says how many of how many", () => {
    set({ [SER()]: ok(series([reqAt(0), reqAt(5)], { total: 2016, truncated: true })) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText("Truncated: first 2 of 2,016")).toBeInTheDocument();
  });

  it("draws no line when no bucket was fully collected", () => {
    set({ [SER()]: ok(series([reqAt(0, { completeness: "MISSING", request_count: null })])) });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText(/No bucket was fully collected/)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("a configured endpoint that is gone says so and draws nothing, not another endpoint", () => {
    const other = "e9000000-0000-4000-8000-0000000000ff";
    set({
      [EPS]: endpoints(other),
      [SER()]: ok(series([reqAt(0), reqAt(5)])),
    });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent("This endpoint no longer exists.");
    expect(screen.queryByRole("img")).toBeNull();
    expect(init).not.toHaveBeenCalled();
    // The title is not the other endpoint's name.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("WAF endpoint traffic");
    expect(res.asked.some((k) => k.includes(other))).toBe(false);
  });

  it("a 404 from the series read is also gone, and while customizing offers Configure", () => {
    set({ [SER()]: failed(404) });
    const onConfigure = vi.fn();
    render(
      <WidgetRuntimeContext.Provider value={runtime({ onConfigure })}>
        <WafEndpointTraffic config={cfg} />
      </WidgetRuntimeContext.Provider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("This endpoint no longer exists.");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Configure" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "WAF" })).toHaveAttribute("href", "/behavior/waf");
  });

  it("an endpoint missing from a truncated list is not called gone", () => {
    set({
      [EPS]: ok({ items: [wafEndpoint({ id: "x" })], total: 400, limit: 200, offset: 0 }),
      [SER()]: ok(series([reqAt(0), reqAt(5)])),
    });
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.queryByText(/no longer exists/)).toBeNull();
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("WAF endpoint traffic");
  });

  it("reads the effective range: the dashboard's, the widget's own, and the prop", () => {
    set({});
    const { unmount } = render(
      <WidgetRuntimeContext.Provider value={runtime({ dashboardRange: "7d" })}>
        <WafEndpointTraffic config={cfg} />
      </WidgetRuntimeContext.Provider>,
    );
    expect(res.asked).toContain(SER("7d"));
    expect(res.asked).not.toContain(SER("24h"));
    unmount();
    res.asked = [];
    render(
      <WidgetRuntimeContext.Provider value={runtime({ dashboardRange: "7d" })}>
        <WafEndpointTraffic config={cfg} range="1h" />
      </WidgetRuntimeContext.Provider>,
    );
    expect(res.asked).toContain(SER("1h"));
    expect(res.asked).not.toContain(SER("7d"));
  });

  it("two placements have distinct section ids", () => {
    set({ [SER()]: ok(series([reqAt(0)])) });
    const { container } = render(
      <>
        <WafEndpointTraffic id="w_aaaa" config={cfg} />
        <WafEndpointTraffic id="w_bbbb" config={cfg} />
      </>,
    );
    const ids = [...container.querySelectorAll("[id]")].map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

// --- Log source EPS (source mode) ----------------------------------------------------------

describe("chart.eps_observed_expected", () => {
  const cfg = { source: SOURCE };
  const LIST = homeReads.sourceBehaviors().key;
  const SER = (w: "1h" | "24h" | "7d" = "24h") => homeReads.sourceSeries(SOURCE, w).key;
  const CELLS = homeReads.sourceCells(SOURCE).key;
  const sources = (id = SOURCE) => ok([behavior({ log_source_id: id, name: "fw-edge-01" })]);

  function set(over: Record<string, unknown>) {
    res.byKey = { [LIST]: sources(), [CELLS]: ok([epsCell()]), ...over };
  }

  it("not configured: says so, reads nothing and draws no chart", () => {
    render(<SourceEps config={{ source: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Choose a log source.");
    expect(screen.queryByRole("img")).toBeNull();
    expect(init).not.toHaveBeenCalled();
    expect(res.asked).toEqual([]);
  });

  it("not configured while customizing offers Configure", () => {
    const onConfigure = vi.fn();
    render(
      <WidgetRuntimeContext.Provider value={runtime({ onConfigure })}>
        <SourceEps config={{ source: null }} />
      </WidgetRuntimeContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(onConfigure).toHaveBeenCalledTimes(1);
  });

  it("is loading, failed and forbidden text", () => {
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading/);
    set({ [SER()]: failed(502) });
    const { unmount } = render(<SourceEps config={cfg} />);
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 502");
    unmount();
    set({ [SER()]: forbidden });
    render(<SourceEps config={cfg} />);
    expect(screen.getAllByRole("status").at(-1)).toHaveTextContent("permission read:anomalies");
  });

  it("titles the widget with the source and the range, and leads to the source page", () => {
    set({ [SER()]: ok(series([eps(0), eps(5)])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("fw-edge-0124 h");
    expect(screen.getByRole("link", { name: "Source" })).toHaveAttribute(
      "href",
      `/behavior/sources/${SOURCE}?range=24`,
    );
  });

  it.each([
    ["1h", `/behavior/sources/${SOURCE}?range=1`],
    ["7d", `/behavior/sources/${SOURCE}?range=24&from=7d`],
  ] as const)("a %s widget uses the nearest source-page range", (window, href) => {
    set({ [SER(window)]: ok(series([eps(0)])) });
    render(<SourceEps config={cfg} range={window} />);
    expect(screen.getByRole("link", { name: "Source" })).toHaveAttribute("href", href);
  });

  it("draws EPS in EPS against the band, and the median as a step", () => {
    set({ [SER()]: ok(series([eps(0), eps(5)])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/EPS against the expected range/);
    expect(line("Observed EPS").map((p) => p[1])).toEqual([100, 100]);
    expect(line("Expected EPS").map((p) => p[1])).toEqual([100, 100]);
    expect(line("Expected low").map((p) => p[1])).toEqual([70, 70]);
    expect(line("Expected range").map((p) => p[1])).toEqual([65, 65]);
  });

  it("an empty window is an absence of collection, never zero EPS", () => {
    set({ [SER()]: ok(series([])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByText(/absence of collection, not zero EPS/)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("INSUFFICIENT_DATA: an unreliable cell draws no band and no median", () => {
    set({
      [SER()]: ok(series([eps(0), eps(5)])),
      [CELLS]: ok([epsCell({ is_reliable: false })]),
    });
    render(<SourceEps config={cfg} />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(line("Expected EPS").map((p) => p[1])).toEqual([null, null]);
    expect(line("Expected low")).toEqual([]);
  });

  it("PARTIAL: a partial bucket is a null on the line, never its reported value", () => {
    set({
      [SER()]: ok(series([eps(0), eps(5, { completeness: "PARTIAL", average_eps: 12 }), eps(10)])),
    });
    render(<SourceEps config={cfg} />);
    expect(screen.getByText("Partial: 2 of 3 stored buckets collected")).toBeInTheDocument();
    expect(line("Observed EPS").map((p) => p[1])).toEqual([100, null, 100]);
  });

  it("TRUNCATED: a full page may have more behind it", () => {
    set({ [SER()]: ok(series([eps(0)], { total: 5000, truncated: true })) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByText("Truncated: first 1 of 5,000")).toBeInTheDocument();
  });

  it("a source that is no longer monitored says so and draws no other source", () => {
    set({ [LIST]: sources("someone-else"), [SER()]: ok(series([eps(0), eps(5)])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent("This log source is no longer monitored.");
    expect(screen.queryByRole("img")).toBeNull();
    expect(init).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Log source EPS");
    expect(screen.getByRole("link", { name: "Sources" })).toHaveAttribute("href", "/behavior/sources");
  });

  it("a 404 from the series read is gone, not an error", () => {
    set({ [SER()]: failed(404) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent("no longer monitored");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reads the effective range", () => {
    set({});
    render(
      <WidgetRuntimeContext.Provider value={runtime({ dashboardRange: "7d" })}>
        <SourceEps config={cfg} />
      </WidgetRuntimeContext.Provider>,
    );
    expect(res.asked).toContain(SER("7d"));
    expect(res.asked).not.toContain(SER("24h"));
  });
});
