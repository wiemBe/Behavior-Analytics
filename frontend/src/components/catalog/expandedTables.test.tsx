import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ok } from "@/components/home/testkit";
import { homeReads } from "@/lib/api";
import { fleet, fleetClass, page } from "@/lib/home/fixtures";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { CatchallSenders } from "./CatchallSenders";
import { sender, wafSource } from "./fixtures";
import { FleetClasses } from "./FleetClasses";
import { WafSources } from "./WafSources";

// UI-3.3b: inside the expand dialog a catalog table shows every row its read returned, from the
// same request the card makes.

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

describe("FleetClasses expanded", () => {
  const KEY = homeReads.fleetSummary("24h").key;
  const CLASSES = [
    "WAF", "IPS", "FIREWALL", "PROXY", "VPN", "LINUX", "WINDOWS", "NETWORK", "DNS", "DATABASE",
  ] as const;
  const setup = () => {
    res.byKey = { [KEY]: ok(fleet(CLASSES.map((c, i) => fleetClass(c, { mean_eps: 100 - i })))) };
  };

  it("shows the eight busiest on the card and all ten expanded, saying so", () => {
    setup();
    const card = render(inDialog(<FleetClasses />, false));
    expect(bodyRows()).toBe(8);
    expect(screen.getByText("Showing the 8 busiest of 10 classes.")).toBeInTheDocument();
    card.unmount();
    render(inDialog(<FleetClasses />));
    expect(bodyRows()).toBe(10);
    expect(screen.getByText("Showing 10 of 10 classes.")).toBeInTheDocument();
  });

  it("asks for the same read when expanded", () => {
    setup();
    render(inDialog(<FleetClasses />));
    expect([...new Set(res.asked)]).toEqual([KEY]);
  });
});

describe("CatchallSenders expanded", () => {
  const KEY = homeReads.newCatchallSenders(200).key;
  const setup = () => {
    const items = Array.from({ length: 9 }, (_, i) =>
      sender({ id: `c${i}`, identifier: `10.0.0.${i}`, first_seen_at: `2026-10-14T0${i}:00:00Z` }),
    );
    res.byKey = { [KEY]: ok(page(items, 9)) };
  };

  it("shows five on the card and every sender the read returned expanded", () => {
    setup();
    const card = render(inDialog(<CatchallSenders />, false));
    expect(bodyRows()).toBe(5);
    expect(screen.getByText("Showing the newest 5 of 9.")).toBeInTheDocument();
    card.unmount();
    render(inDialog(<CatchallSenders />));
    expect(bodyRows()).toBe(9);
    expect(screen.getByText("Showing the newest 9 of 9.")).toBeInTheDocument();
    expect([...new Set(res.asked)]).toEqual([KEY]);
  });
});

describe("WafSources expanded", () => {
  const KEY = homeReads.wafSources().key;
  const setup = () => {
    const items = Array.from({ length: 9 }, (_, i) =>
      wafSource({ log_source_id: `w${i}`, log_source_name: `waf-${i}` }),
    );
    res.byKey = {
      [KEY]: ok({ ...page(items, 9), limit: 200, offset: 0, range_start: "", range_end: "" }),
    };
  };

  it("shows six on the card and every source the read returned expanded", () => {
    setup();
    const card = render(inDialog(<WafSources />, false));
    expect(bodyRows()).toBe(6);
    card.unmount();
    render(inDialog(<WafSources />));
    expect(bodyRows()).toBe(9);
    expect(screen.getByText(/Showing the 9 that need attention first, of 9\./)).toBeInTheDocument();
    expect([...new Set(res.asked)]).toEqual([KEY]);
  });
});
