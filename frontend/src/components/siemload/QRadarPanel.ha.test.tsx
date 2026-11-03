import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QRadarServers } from "@/lib/api";

import { health, hostEntry, hostsOf, notRead, ok, pairEntry } from "./fixtures";
import { QRadarPanel } from "./QRadarPanel";

const servers = (over: Partial<QRadarServers> = {}) =>
  ok<QRadarServers>({
    total: 3,
    active: 3,
    unreported: 0,
    by_status: { ACTIVE: 3, STANDBY: 1 },
    not_active: [],
    all_active: true,
    ha_pairs: 1,
    ha_problem: 0,
    ha_issues: [],
    ...over,
  });

const value = (label: string) =>
  screen.getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;

const panel = (over: Parameters<typeof health>[0]) =>
  render(<QRadarPanel part={{ available: true, consoles: [health(over)] }} />);

describe("QRadarPanel: the managed hosts line with HA pairs", () => {
  it("counts managed hosts and says the pairs are healthy, with no warning", () => {
    panel({ servers: servers() });
    const cell = value("Managed hosts");
    expect(cell).toHaveTextContent("ACTIVE 3 of 3");
    expect(cell).toHaveTextContent("1 HA pair, all healthy");
    expect(cell.querySelector(".pill")).toBeNull();
    expect(cell).not.toHaveTextContent(/not ACTIVE/);
  });

  it("warns when a pair is not healthy and says how many", () => {
    panel({ servers: servers({ ha_pairs: 2, ha_problem: 1, ha_issues: [] }) });
    const cell = value("Managed hosts");
    expect(cell).toHaveTextContent("2 HA pairs, 1 not healthy");
    expect(cell.querySelector(".pill.warn")).toHaveTextContent("Needs attention");
  });

  it("says nothing about pairs on a site with none", () => {
    panel({ servers: servers({ ha_pairs: 0 }) });
    expect(value("Managed hosts")).not.toHaveTextContent("HA pair");
  });

  it("an older backend without the HA fields reads as before", () => {
    panel({
      servers: ok<QRadarServers>({ total: 3, active: 3, unreported: 0, by_status: { ACTIVE: 3 }, not_active: [], all_active: true }),
    });
    expect(value("Managed hosts")).toHaveTextContent("ACTIVE 3 of 3");
    expect(value("Managed hosts")).not.toHaveTextContent("HA pair");
  });

  it("leaves the host list to the table below once the table was read", () => {
    panel({
      servers: servers({
        active: 1,
        total: 2,
        not_active: [{ hostname: "ep-02", status: "DISCONNECTED" }],
        all_active: false,
      }),
      hosts: hostsOf([hostEntry(), pairEntry("ha_healthy", ["ACTIVE", "STANDBY"])]),
    });
    const cell = value("Managed hosts");
    expect(cell).toHaveTextContent("1 not ACTIVE");
    expect(cell).toHaveTextContent("Each host is listed in the table below");
    expect(cell).not.toHaveTextContent("ep-02");
  });

  it("keeps the short list when the host table could not be read", () => {
    panel({
      servers: servers({
        active: 1,
        total: 2,
        not_active: [{ hostname: "ep-02", status: "DISCONNECTED" }],
        all_active: false,
      }),
      hosts: notRead("FAILED", "QRadar did not answer the host list."),
    });
    const cell = value("Managed hosts");
    expect(cell).toHaveTextContent("ep-02 — DISCONNECTED");
    expect(cell).not.toHaveTextContent("table below");
  });
});
