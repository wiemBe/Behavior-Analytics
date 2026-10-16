import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { SocOverview } from "@/lib/api";
import {
  analytics,
  anomaly,
  behavior,
  epsPoint,
  epsSeries,
  fleet,
  fleetClass,
  noData,
  offenseRow,
  page,
  summary,
} from "@/lib/home/fixtures";

import DashboardError from "./error";
import DashboardLoading from "./loading";
import HomePage from "./page";

const mocks = vi.hoisted(() => ({
  scenario: "ok" as "ok" | "empty" | "partial-permission" | "down" | "insufficient",
  reportAge: vi.fn(),
}));

const { setOption, init, use, on } = vi.hoisted(() => {
  const setOption = vi.fn();
  const on = vi.fn();
  return {
    setOption,
    on,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on })),
    use: vi.fn(),
  };
});

vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {}, PieChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/shell/AutoRefresh", () => ({
  formatClockUtc: () => "13:58:00 UTC",
  useRefreshTick: () => 0,
  useReportDataAge: mocks.reportAge,
}));

const receivedAt = "2026-10-14T13:58:00Z";
const okay = <T,>(data: T): ResourceState<T> => ({
  status: "ok",
  data,
  receivedAt,
  refreshing: false,
  refreshFailed: null,
});
const failed: ResourceState<never> = { status: "error", httpStatus: 503 };
const forbidden: ResourceState<never> = { status: "forbidden", httpStatus: 403 };

const sourceRows = [
  behavior({ log_source_id: "s1", name: "Core Firewall", state: "OPEN", deviation_ratio: 2 }),
  behavior({ log_source_id: "s2", name: "DNS", state: "NORMAL" }),
];
const healthySummary = summary({
  monitored_sources: 2,
  open_anomalies: 1,
  spikes: 1,
  insufficient_data_sources: 0,
});
const overview = {
  log_sources: { total_log_sources: 2, in_maintenance: 0 },
} as unknown as SocOverview;
const classSummary = fleet([
  fleetClass("FIREWALL", {
    mean_eps: 750,
    expected_eps: 700,
    baseline_sources: 1,
    monitored_sources: 1,
    top_sources: [{ id: "s1", name: "Core Firewall", events: 64_800_000 }],
  }),
  fleetClass("DNS", {
    mean_eps: 250,
    expected_eps: 240,
    baseline_sources: 1,
    monitored_sources: 1,
    top_sources: [{ id: "s2", name: "DNS", events: 21_600_000 }],
  }),
]);
const lineSeries = epsSeries([epsPoint(0, 980, 940), epsPoint(1, 1_000, 950)], {
  window: "24h",
  window_end: "2026-10-14T14:00:00Z",
});
const oneAnomaly = page([anomaly({ log_source_name: "Core Firewall" })]);
const oneOffense = page([
  offenseRow({ qradar_offense_id: 42, description: "Repeated authentication failures" }),
]);

function stateFor(key: string): ResourceState<unknown> {
  if (mocks.scenario === "down") return failed;
  if (mocks.scenario === "partial-permission" && key.startsWith("/offenses")) {
    return forbidden;
  }

  // UI-4.1: the page reads the saved dashboards first; none saved shows the template.
  if (key.startsWith("/dashboards")) return okay({ items: [], total: 0, limit: 200, offset: 0 });
  if (key === "/me") {
    return okay({ subject: "analyst", email: null, permissions: [], effective_permissions: [] });
  }

  const empty = mocks.scenario === "empty";
  const insufficient = mocks.scenario === "insufficient";

  if (key.startsWith("/fleet/eps-series")) {
    return okay(
      empty || insufficient
        ? epsSeries(insufficient ? [epsPoint(0, null), epsPoint(1, null)] : [], {
            window: "24h",
            window_end: "2026-10-14T14:00:00Z",
          })
        : lineSeries,
    );
  }
  if (key.startsWith("/fleet/summary")) {
    return okay(
      empty
        ? fleet([])
        : insufficient
          ? fleet([noData("FIREWALL"), noData("DNS")])
          : classSummary,
    );
  }
  if (key === "/anomalies/summary") {
    return okay(
      empty
        ? summary()
        : insufficient
          ? summary({ monitored_sources: 2, insufficient_data_sources: 2 })
          : healthySummary,
    );
  }
  if (key === "/behavior/sources") {
    return okay(
      empty
        ? []
        : insufficient
          ? sourceRows.map((row) => ({ ...row, state: "INSUFFICIENT_DATA" as const }))
          : sourceRows,
    );
  }
  if (key === "/overview") return okay(overview);
  if (key === "/offenses/analytics") {
    return okay(empty ? analytics() : analytics({ active: 1, critical: 1 }, [{ key: "8", count: 1 }]));
  }
  if (key === "/offenses?limit=1") return okay(empty ? page([]) : oneOffense);
  if (key.startsWith("/offenses?status=OPEN")) return okay(empty ? page([]) : oneOffense);
  if (key.startsWith("/anomalies?active_only=true")) return okay(empty ? page([]) : oneAnomaly);
  if (key.startsWith("/anomalies?since=")) return okay(empty || insufficient ? page([]) : oneAnomaly);
  throw new Error(`Unhandled Home read in test: ${key}`);
}

vi.mock("@/hooks/useApiResource", () => ({
  // A null key means "do not fetch yet" (UI-4.1: no saved dashboard is selected).
  useApiResource: (key: string | null) => (key === null ? { status: "idle" } : stateFor(key)),
}));

beforeEach(() => {
  mocks.scenario = "ok";
  mocks.reportAge.mockClear();
  setOption.mockClear();
  init.mockClear();
  on.mockClear();
});

// The widgets load with `next/dynamic` (UR.5.1), so the first assertions wait for every
// text-only placeholder to be replaced by its widget.
async function widgetsLoaded(container: HTMLElement) {
  await waitFor(() => expect(container.querySelectorAll("[aria-busy='true']")).toHaveLength(0), {
    timeout: 5000,
  });
}

describe("Dashboard page", () => {
  it("has one h1 and the fixed widget hierarchy in order", async () => {
    const { container } = render(<HomePage />);
    await widgetsLoaded(container);
    expect(screen.getByRole("heading", { level: 1, name: "SOC Overview" })).toBeInTheDocument();
    expect(screen.getByText(/24 h view of fleet volume/)).toBeInTheDocument();
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "SOC Overview",
      "Total EPS, 24 h",
      "Log sources, now",
      "Open offenses, latest snapshot",
      "Active anomalies, now",
      "Fleet EPS24 h",
      "EPS by source class24 h",
      "Top log sources by EPS24 h",
      "Anomalies by severity24 h",
      "Log source healthlatest state",
      "Recent anomaliesactive now",
      "Recent offensesopen, by last update",
    ]);
    expect(screen.getAllByRole("article")).toHaveLength(4);
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(11);
    expect(mocks.reportAge).toHaveBeenCalledWith("2026-10-14T14:00:00Z");
  });

  it("renders a fresh install as explained empty states, never zero for an absent measurement", async () => {
    mocks.scenario = "empty";
    const { container } = render(<HomePage />);
    await widgetsLoaded(container);
    expect(screen.getAllByText(/No class has a complete bucket in this window/)).toHaveLength(2);
    expect(screen.getByText(/No monitored log source; there is nothing to judge yet/)).toBeInTheDocument();
    expect(screen.getByText(/The window holds no step\. No line is drawn/)).toBeInTheDocument();
    expect(screen.getByText(/No log source has a complete bucket/)).toBeInTheDocument();
    expect(screen.getByText("No anomaly opened in the last 24 h.")).toBeInTheDocument();
    expect(screen.getAllByText(/No monitored log sources?\. Enable monitoring/)).toHaveLength(2);
    expect(screen.getByText(/No active anomalies\. Sources with no baseline/)).toBeInTheDocument();
    expect(screen.getAllByText(/No offense snapshot has been collected yet/)).toHaveLength(2);
    expect(container.querySelector(".kpi-value")?.textContent).toBe("—");
    expect(container.textContent).not.toContain("0.00 EPS");
  });

  it("contains an offenses permission gap while every non-offense widget still renders", async () => {
    mocks.scenario = "partial-permission";
    const { container } = render(<HomePage />);
    await widgetsLoaded(container);
    expect(screen.getAllByText(/permission read:offenses/)).toHaveLength(2);
    expect(screen.getByRole("table", { name: /EPS by source class/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Recent anomalies" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Fleet EPS/ })).toBeInTheDocument();
  });

  it("contains backend failure in every widget and gives a recovery action", async () => {
    mocks.scenario = "down";
    const { container } = render(<HomePage />);
    await widgetsLoaded(container);
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(11);
    for (const alert of alerts) {
      expect(alert).toHaveTextContent(/Refresh|next refresh/);
      expect(alert).toHaveTextContent(/backend is reachable/);
    }
    expect(screen.getByRole("heading", { level: 1, name: "SOC Overview" })).toBeInTheDocument();
  });

  it("keeps insufficient data neutral and does not draw an absent fleet measurement", async () => {
    mocks.scenario = "insufficient";
    const { container } = render(<HomePage />);
    await widgetsLoaded(container);
    expect(screen.getAllByText(/Insufficient data/).length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText(/No class has a complete bucket/)).toHaveLength(2);
    expect(screen.getAllByText(/No monitored source has a verdict yet/)).toHaveLength(2);
    expect(screen.getByText(/not zero traffic/)).toBeInTheDocument();
    expect(container.querySelector(".hc-chart[role='img']")).toBeNull();
    expect(container.textContent).not.toContain("0.00 EPS");
  });

  it("gives route-level loading and error fallbacks explicit, actionable copy", () => {
    const loading = render(<DashboardLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading dashboard…");
    loading.unmount();
    render(<DashboardError />);
    expect(screen.getByRole("alert")).toHaveTextContent("Dashboard could not be opened");
    expect(screen.getByRole("alert")).toHaveTextContent("check that the backend is reachable");
  });
});
