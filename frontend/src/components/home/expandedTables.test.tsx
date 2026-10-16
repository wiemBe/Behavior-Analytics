import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { anomaly, behavior, fleet, fleetClass, offenseRow, page, summary } from "@/lib/home/fixtures";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { ok } from "./testkit";
import { RecentAnomalies } from "./RecentAnomalies";
import { RecentOffenses } from "./RecentOffenses";
import { TopSources } from "./TopSources";

// UI-3.3b: inside the expand dialog a table shows every row its read returned, from the same
// request the card makes.

const res = vi.hoisted(() => ({
  byKey: {} as Record<string, unknown>,
  asked: [] as string[],
}));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => {
    res.asked.push(key ?? "");
    return res.byKey[key ?? ""] ?? { status: "loading" };
  },
}));

const runtime = (expanded: boolean): WidgetRuntime => ({
  rangeOptions: null,
  override: null,
  dashboardRange: "24h",
  setRange: vi.fn(),
  onExpand: null,
  expanded,
});
const inDialog = (node: ReactNode, expanded = true) => (
  <WidgetRuntimeContext.Provider value={runtime(expanded)}>{node}</WidgetRuntimeContext.Provider>
);
const bodyRows = () => document.querySelectorAll("tbody tr").length;

beforeEach(() => {
  res.byKey = {};
  res.asked = [];
});

describe("TopSources expanded", () => {
  const FLEET = homeReads.fleetSummary("24h").key;
  const BEH = homeReads.sourceBehaviors().key;
  const classes = ["FIREWALL", "PROXY", "VPN"] as const;
  function setup() {
    const top = (c: string) =>
      Array.from({ length: 5 }, (_, i) => ({ id: `${c}${i}`, name: `${c} ${i}`, events: 1000 - i }));
    res.byKey = {
      [FLEET]: ok(fleet(classes.map((c) => fleetClass(c, { top_sources: top(c) })))),
      [BEH]: ok([behavior({ log_source_id: "FIREWALL0", name: "FIREWALL 0" })]),
    };
  }

  it("shows five on the card and every source the read returned when expanded", () => {
    setup();
    const card = render(inDialog(<TopSources />, false));
    expect(bodyRows()).toBe(5);
    card.unmount();
    render(inDialog(<TopSources />));
    expect(bodyRows()).toBe(15);
    expect(screen.getByText(/Showing 15 of 15 sources/)).toBeInTheDocument();
  });

  it("asks for the same reads whether expanded or not", () => {
    setup();
    render(inDialog(<TopSources />, false));
    const card = [...new Set(res.asked)].sort();
    res.asked = [];
    render(inDialog(<TopSources />));
    expect([...new Set(res.asked)].sort()).toEqual(card);
  });

  it("takes expanded from its props too, as the dialog passes it", () => {
    setup();
    render(<TopSources expanded />);
    expect(bodyRows()).toBe(15);
  });
});

describe("RecentAnomalies expanded", () => {
  const LIST = homeReads.activeAnomalies(5).key;
  const SUM = homeReads.anomalySummary().key;
  const setup = () => {
    const items = Array.from({ length: 5 }, (_, i) => anomaly({ id: `a${i}` }));
    res.byKey = { [LIST]: ok(page(items, 18)), [SUM]: ok(summary()) };
  };

  it("says how many of how many it shows, on the card and expanded", () => {
    setup();
    const card = render(inDialog(<RecentAnomalies />, false));
    expect(screen.getByText("Showing the latest 5 of 18.")).toBeInTheDocument();
    card.unmount();
    render(inDialog(<RecentAnomalies />));
    expect(bodyRows()).toBe(5);
    expect(screen.getByText(/^Showing 5 of 18\./)).toBeInTheDocument();
    expect(screen.queryByText(/latest 5 of 18/)).toBeNull();
  });

  it("keeps the request's limit at five when expanded", () => {
    setup();
    render(inDialog(<RecentAnomalies />, false));
    const card = [...new Set(res.asked)].sort();
    res.asked = [];
    render(inDialog(<RecentAnomalies />));
    expect([...new Set(res.asked)].sort()).toEqual(card);
    expect(card).toContain(LIST);
  });
});

describe("RecentOffenses expanded", () => {
  const OPEN = homeReads.recentOffenses(5).key;
  const PROBE = homeReads.offenseProbe().key;
  const setup = () => {
    const items = Array.from({ length: 5 }, (_, i) => offenseRow({ qradar_offense_id: i + 1 }));
    res.byKey = { [OPEN]: ok(page(items, 12)), [PROBE]: ok(page([offenseRow()], 12)) };
  };

  it("says how many of how many it shows, on the card and expanded", () => {
    setup();
    const card = render(inDialog(<RecentOffenses />, false));
    expect(screen.getByText("Showing the latest 5 of 12.")).toBeInTheDocument();
    card.unmount();
    render(inDialog(<RecentOffenses />));
    expect(bodyRows()).toBe(5);
    expect(screen.getByText(/^Showing 5 of 12\./)).toBeInTheDocument();
  });

  it("keeps the request's limit at five when expanded", () => {
    setup();
    render(inDialog(<RecentOffenses />, false));
    const card = [...new Set(res.asked)].sort();
    res.asked = [];
    render(inDialog(<RecentOffenses />));
    expect([...new Set(res.asked)].sort()).toEqual(card);
    expect(card).toContain(OPEN);
  });
});
