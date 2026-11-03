import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";

import { current, health, hostEntry, hostsOf, summary } from "./fixtures";
import { SiemLoadPage } from "./SiemLoadPage";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown>, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => {
    mocks.keys.push(key);
    return mocks.states[key] ?? { status: "loading" };
  },
}));
vi.mock("@/components/shell/AutoRefresh", () => ({ useReportDataAge: () => undefined }));
vi.mock("@/components/home/useEChart", () => ({ useEChart: () => ({ current: null }) }));
vi.mock("echarts/core", () => ({ init: vi.fn(), use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {}, CustomChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const okState = <T,>(data: T): ResourceState<T> => ({
  status: "ok",
  data,
  receivedAt: "2026-11-02T10:15:04Z",
  refreshing: false,
  refreshFailed: null,
});

beforeEach(() => {
  mocks.states = { "/admin/ariel-load?hours=24": okState(summary()) };
  mocks.keys = [];
});

describe("SiemLoadPage: QRadar in detail", () => {
  it("puts the detail after the first screen and before our searches in flight", () => {
    mocks.states["/admin/ariel-load/current"] = okState(
      current({ qradar: { available: true, consoles: [health({ hosts: hostsOf([hostEntry()]) })] } }),
    );
    render(<SiemLoadPage range="24h" />);
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "Our load on QRadar",
      "QRadar",
      "qradar-lab: hosts, health and trends",
      "Searches in flight",
      "Load by caller",
    ]);
  });

  it("reads the stored health and the trend of the URL's window", () => {
    mocks.states["/admin/ariel-load/current"] = okState(
      current({ qradar: { available: true, consoles: [health({ hosts: hostsOf([hostEntry()]) })] } }),
    );
    render(<SiemLoadPage range="24h" trend="7d" />);
    expect(mocks.keys).toContain("/admin/qradar-health/latest?notifications_hours=24");
    expect(mocks.keys).toContain("/admin/qradar-health/history?hours=168");
  });

  it("keeps the trend window when the summary window changes, and the other way round", () => {
    mocks.states["/admin/ariel-load/current"] = okState(
      current({ qradar: { available: true, consoles: [health({ hosts: hostsOf([hostEntry()]) })] } }),
    );
    render(<SiemLoadPage range="24h" trend="7d" />);
    const summaryControl = screen.getByRole("navigation", { name: "Summary window" });
    expect(within(summaryControl).getByRole("link", { name: "7 d" })).toHaveAttribute(
      "href",
      "/admin/siem-load?range=7d&trend=7d",
    );
    expect(within(summaryControl).getByRole("link", { name: "24 h" })).toHaveAttribute(
      "href",
      "/admin/siem-load?trend=7d",
    );
    const trendControl = screen.getByRole("navigation", { name: "Trend window" });
    expect(within(trendControl).getByRole("link", { name: "24 h" })).toHaveAttribute("href", "/admin/siem-load");
  });

  it("draws no detail for a page with no console, or with its QRadar part unavailable", () => {
    mocks.states["/admin/ariel-load/current"] = okState(current({ qradar: { available: true, consoles: [] } }));
    const { unmount } = render(<SiemLoadPage range="24h" />);
    expect(screen.queryByText(/hosts, health and trends/)).not.toBeInTheDocument();
    expect(mocks.keys).not.toContain("/admin/qradar-health/latest?notifications_hours=24");
    unmount();
    mocks.states["/admin/ariel-load/current"] = okState(
      current({ qradar: { available: false, reason: "the registered consoles could not be read" } }),
    );
    render(<SiemLoadPage range="24h" />);
    expect(screen.queryByText(/hosts, health and trends/)).not.toBeInTheDocument();
  });
});
