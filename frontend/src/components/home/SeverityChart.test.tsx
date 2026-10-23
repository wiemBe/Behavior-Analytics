import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { anomaly, page } from "@/lib/home/fixtures";
import { lastHours } from "@/lib/home/severityPerHour";

import { SeverityChart } from "./SeverityChart";
import { failed, forbidden, loading, ok } from "./testkit";

const { setOption, init, use, on } = vi.hoisted(() => {
  const setOption = vi.fn();
  const dispose = vi.fn();
  const on = vi.fn();
  return { setOption, dispose, on, init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose, on })), use: vi.fn() };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ BarChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const res = vi.hoisted(() => ({ state: undefined as unknown }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => res.state ?? { status: "loading" },
}));

const NOW = Date.parse("2026-10-14T13:20:00Z");
const range = lastHours(NOW, 24);
const at = (severity: string, iso = "2026-10-14T09:10:00Z") => anomaly({ severity, detected_at: iso });
const names = () =>
  (setOption.mock.calls[0][0] as { series: Array<{ name: string }> }).series.map((s) => s.name);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  res.state = undefined;
  setOption.mockClear();
  init.mockClear();
  push.mockClear();
  on.mockClear();
});

describe("SeverityChart", () => {
  it("stacks severities in the fixed order and keys them with words", () => {
    res.state = ok(page([at("LOW"), at("CRITICAL"), at("HIGH")]));
    render(<SeverityChart />);
    expect(names()).toEqual(["CRITICAL", "HIGH", "LOW"]);
    const key = screen.getByRole("list", { name: "Severity key" });
    expect(key).toHaveTextContent(/Critical.*High.*Low/);
  });

  it("describes the chart in text, with the unit of its axis", () => {
    res.state = ok(page([at("HIGH"), at("HIGH")]));
    render(<SeverityChart />);
    expect(screen.getByRole("img", { name: /Anomalies per hour by severity/ })).toBeInTheDocument();
    expect(screen.getByText(/2 in 24 hours \(2 high\)/, { selector: ".sr-only" })).toBeInTheDocument();
    const axis = (setOption.mock.calls[0][0] as { yAxis: { name: string } }).yAxis.name;
    expect(axis).toBe("anomalies per hour");
    expect(screen.getByRole("img").closest(".tw-body-fill")).not.toBeNull();
  });

  it("shows TRUNCATED and does not present the capped stack as the whole", () => {
    res.state = ok({ ...page([at("HIGH")]), total: 345 });
    render(<SeverityChart />);
    expect(screen.getByText("Truncated: first 1 of 345")).toBeInTheDocument();
    expect((setOption.mock.calls[0][0] as { yAxis: { name: string } }).yAxis.name).toMatch(/at least/);
    expect(screen.getByText(/lower bound/, { selector: ".sr-only" })).toBeInTheDocument();
  });

  it("shows no truncation for a complete list", () => {
    res.state = ok(page([at("HIGH")]));
    render(<SeverityChart />);
    expect(screen.queryByText(/Truncated/)).not.toBeInTheDocument();
  });

  it("says a complete empty list is empty, and draws no chart", () => {
    res.state = ok(page([]));
    render(<SeverityChart />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: /Anomalies per hour/ })).not.toBeInTheDocument();
    // The sentence names the same window the header does.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("24 h");
    expect(screen.getByText("No anomaly opened in the last 24 h.")).toBeInTheDocument();
  });

  it("says the window is empty when every returned row falls outside it", () => {
    // The backend filters by since/until and returns an empty page. A fixture that does not
    // still returns rows; none of them are in the widget's range, and the list is complete.
    res.state = ok(page([at("HIGH", "2026-10-12T10:00:00Z"), at("LOW", "2026-10-12T11:00:00Z")]));
    render(<SeverityChart />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: /Anomalies per hour/ })).not.toBeInTheDocument();
    expect(screen.getByText("No anomaly opened in the last 24 h.")).toBeInTheDocument();
  });

  it("does not call a capped list with nothing in the window an empty window", () => {
    res.state = ok(page([at("HIGH", "2026-10-12T10:00:00Z")], 400));
    render(<SeverityChart />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByText(/No anomaly opened/)).not.toBeInTheDocument();
    expect(screen.getByText(/Truncated: first 1 of 400/)).toBeInTheDocument();
    expect(screen.getByText(/not a measured zero/)).toBeInTheDocument();
  });

  it("does not count a list whose total contradicts the rows", () => {
    res.state = ok({ ...page([at("HIGH", "2026-10-12T10:00:00Z")]), total: 0 });
    render(<SeverityChart />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByText(/No anomaly opened/)).not.toBeInTheDocument();
    expect(screen.getByText(/total does not match the rows returned/)).toBeInTheDocument();
  });

  it("links the title row to the anomalies of the range", () => {
    res.state = ok(page([at("HIGH")]));
    render(<SeverityChart />);
    const href = screen.getByRole("link", { name: "Anomalies" }).getAttribute("href")!;
    const q = new URL(href, "http://x");
    expect(q.pathname).toBe("/anomalies");
    expect(q.searchParams.get("since")).toBe(range.since);
    expect(q.searchParams.get("until")).toBe(range.until);
    expect(q.searchParams.get("severity")).toBeNull();
  });

  it("opens the anomalies of that hour and severity when a segment is clicked", () => {
    res.state = ok(page([at("HIGH")]));
    render(<SeverityChart />);
    const handler = on.mock.calls.find((c) => c[0] === "click")![1] as (e: unknown) => void;
    // Hour index 19 of the 24 columns: since is 14:00 on the 13th.
    handler({ seriesName: "HIGH", dataIndex: 19 });
    const url = new URL(push.mock.calls[0][0] as string, "http://x");
    expect(url.pathname).toBe("/anomalies");
    expect(url.searchParams.get("since")).toBe("2026-10-14T09:00:00.000Z");
    expect(url.searchParams.get("until")).toBe("2026-10-14T10:00:00.000Z");
    expect(url.searchParams.get("severity")).toBe("HIGH");
    handler({ seriesName: "HIGH", dataIndex: 99 });
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("renders loading, forbidden and error", () => {
    res.state = loading;
    const a = render(<SeverityChart />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading the anomaly list/);
    a.unmount();
    res.state = forbidden;
    const b = render(<SeverityChart />);
    expect(screen.getByText(/read:anomalies/)).toBeInTheDocument();
    expect(screen.queryByText(/No anomaly opened/)).not.toBeInTheDocument();
    b.unmount();
    res.state = failed(500);
    render(<SeverityChart />);
    expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
    expect(screen.queryByText(/No anomaly opened/)).not.toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("keeps the chart and says so when a refresh failed", () => {
    res.state = ok(page([at("HIGH")]), { refreshFailed: { httpStatus: 500 } });
    render(<SeverityChart />);
    expect(init).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/latest refresh failed/)).toBeInTheDocument();
  });
});
