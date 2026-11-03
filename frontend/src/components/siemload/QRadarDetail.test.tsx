import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { QRadarHealthLatest } from "@/lib/api";

import {
  consoleHistory,
  consoleLatest,
  health,
  historyOf,
  hostEntry,
  hostsOf,
  metricsHost,
  pairEntry,
} from "./fixtures";
import { QRadarDetail } from "./QRadarDetail";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown>, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => {
    mocks.keys.push(key);
    return mocks.states[key] ?? { status: "loading" };
  },
}));
vi.mock("@/components/home/useEChart", () => ({ useEChart: () => ({ current: null }) }));
vi.mock("echarts/core", () => ({ init: vi.fn(), use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {}, CustomChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const LATEST = "/admin/qradar-health/latest?notifications_hours=24";
const HISTORY_24 = "/admin/qradar-health/history?hours=24";
const HISTORY_168 = "/admin/qradar-health/history?hours=168";

const okState = <T,>(data: T): ResourceState<T> => ({
  status: "ok",
  data,
  receivedAt: "2026-11-02T10:17:00Z",
  refreshing: false,
  refreshFailed: null,
});
const latestOf = (...consoles: ReturnType<typeof consoleLatest>[]): QRadarHealthLatest => ({
  generated_at: "2026-11-02T10:16:30Z",
  consoles,
});

const withHosts = () =>
  health({
    hosts: hostsOf([hostEntry(), pairEntry("ha_healthy", ["ACTIVE", "STANDBY"])]),
  });

beforeEach(() => {
  mocks.states = {};
  mocks.keys = [];
});

describe("QRadarDetail: the reads", () => {
  it("reads the stored health with a 24 h notification roll-up and the trend of the chosen window", () => {
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    expect(mocks.keys).toContain(LATEST);
    expect(mocks.keys).toContain(HISTORY_24);
    mocks.keys = [];
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="7d" />);
    expect(mocks.keys).toContain(HISTORY_168);
  });

  it("is one read of each for any number of consoles", () => {
    render(
      <QRadarDetail
        consoles={[withHosts(), health({ instance_id: "b", instance_name: "qradar-dr", hosts: hostsOf([hostEntry()]) })]}
        range="24h"
        trend="24h"
      />,
    );
    expect(mocks.keys.filter((key) => key === LATEST).length).toBeGreaterThan(0);
    expect(new Set(mocks.keys)).toEqual(new Set([LATEST, HISTORY_24]));
  });
});

describe("QRadarDetail: a console that has everything", () => {
  beforeEach(() => {
    mocks.states[LATEST] = okState(
      latestOf(
        consoleLatest({
          hosts: [metricsHost("qr-console", { diskPercent: 88, level: "WARN", spill: 1024 ** 3 }), metricsHost("ep-ha-a"), metricsHost("ep-ha-b")],
        }),
      ),
    );
    mocks.states[HISTORY_24] = okState(historyOf([consoleHistory()]));
  });

  it("answers the section's question in order: hosts, health, notifications, trends", () => {
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    expect(screen.getAllByRole("heading").map((h) => h.textContent)).toEqual([
      "qradar-lab: hosts, health and trends",
      "Managed hosts",
      "Disk, system and pipeline",
      "System notifications, last 24 h",
      "Trends",
      "Reachability and API latency",
      "Fullest mount per host",
      "CRE queue per host",
      "Throttle spill per host",
    ]);
  });

  it("joins the host table to the figures: the pair's members and the flagged disk and spill", () => {
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    const health = screen.getByRole("table", { name: "Disk, system and pipeline per host of qradar-lab" });
    const row = within(health).getByText("qr-console").closest("tr") as HTMLElement;
    expect(row.querySelector(".pill.warn")).toHaveTextContent("Warning");
    expect(row).toHaveTextContent("Spilling");
    expect(within(health).getByText("ep-ha-a")).toBeInTheDocument();
    expect(within(health).getByText("ep-ha-b")).toBeInTheDocument();
    expect(screen.queryByText(/not in the managed host list/)).not.toBeInTheDocument();
  });

  it("keeps the trend window in the URL: both controls keep the other's choice", () => {
    render(<QRadarDetail consoles={[withHosts()]} range="7d" trend="24h" />);
    const control = screen.getByRole("navigation", { name: "Trend window" });
    expect(within(control).getByRole("link", { name: "24 h" })).toHaveAttribute("href", "/admin/siem-load?range=7d");
    expect(within(control).getByRole("link", { name: "24 h" })).toHaveAttribute("aria-current", "page");
    expect(within(control).getByRole("link", { name: "7 d" })).toHaveAttribute("href", "/admin/siem-load?range=7d&trend=7d");
    expect(within(control).getByRole("link", { name: "7 d" })).not.toHaveAttribute("aria-current");
  });

  it("shows no raw search-family key anywhere", () => {
    const { container } = render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    expect(container.textContent ?? "").not.toMatch(/QRADAR_HEALTH/);
  });

  it("uses no 'live' or 'real-time' wording", () => {
    const { container } = render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    expect(container.textContent ?? "").not.toMatch(/\blive\b|real[- ]?time/i);
  });
});

describe("QRadarDetail: states", () => {
  it("shows the host table from the page's own read while the stored reads are loading", () => {
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    expect(screen.getByRole("table", { name: "Managed hosts of qradar-lab" })).toBeInTheDocument();
    expect(screen.getAllByRole("status").map((el) => el.textContent)).toEqual([
      "Loading the disk, system and pipeline figures…",
      "Loading the system notifications…",
      "Loading the QRadar trends…",
    ]);
  });

  it("draws no heading and only says it is loading when there is no host table and nothing has answered", () => {
    render(<QRadarDetail consoles={[health()]} range="24h" trend="24h" />);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("QRadar's health in detail");
  });

  it("names admin:* when the stored health is forbidden, and keeps the host table", () => {
    mocks.states[LATEST] = { status: "forbidden", httpStatus: 403 };
    mocks.states[HISTORY_24] = { status: "forbidden", httpStatus: 403 };
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(3);
    expect(alerts[0]).toHaveTextContent("permission admin:*");
    expect(alerts[0]).toHaveTextContent("Nothing was changed");
    expect(alerts[2]).toHaveTextContent("The QRadar trends could not be read.");
    expect(screen.getByRole("table", { name: "Managed hosts of qradar-lab" })).toBeInTheDocument();
  });

  it("says what failed and what to do when the stored health cannot be read", () => {
    mocks.states[LATEST] = { status: "error", httpStatus: 500 };
    mocks.states[HISTORY_24] = { status: "error", httpStatus: null };
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    const alerts = screen.getAllByRole("alert");
    expect(alerts[0]).toHaveTextContent("QRadar's health in detail could not be read.");
    expect(alerts[0]).toHaveTextContent("The backend answered with HTTP 500.");
    expect(alerts[0]).toHaveTextContent("Nothing was changed. Check that the backend is reachable");
    expect(alerts[2]).toHaveTextContent("The backend did not answer.");
  });

  it("says a console missing from the stored health has no figures, and draws no zero for it", () => {
    mocks.states[LATEST] = okState(latestOf());
    mocks.states[HISTORY_24] = okState(historyOf([]));
    render(<QRadarDetail consoles={[withHosts()]} range="24h" trend="24h" />);
    expect(screen.getByText(/This console is not in the stored health/)).toBeInTheDocument();
    expect(screen.getByText("No trend was returned for this console.")).toBeInTheDocument();
  });

  it("shows the host table's own reason when the host list was not read, and still joins nothing", () => {
    mocks.states[LATEST] = okState(latestOf(consoleLatest()));
    mocks.states[HISTORY_24] = okState(historyOf());
    render(
      <QRadarDetail
        consoles={[
          health({
            hosts: { state: "FAILED", value: null, reason_code: "timeout", reason: "QRadar did not answer the host list." },
          }),
        ]}
        range="24h"
        trend="24h"
      />,
    );
    expect(screen.getByText("QRadar did not answer the host list.")).toBeInTheDocument();
    expect(screen.getByText(/not matched to it/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Disk, system and pipeline per host of qradar-lab" })).toBeInTheDocument();
  });
});
