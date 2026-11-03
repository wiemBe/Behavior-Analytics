import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QRadarHostEntry } from "@/lib/api";

import { hostEntry, hostsOf, member, notRead, ok, pairEntry, rate } from "./fixtures";
import { HostsTable } from "./HostsTable";
import { joinHealth } from "./qradarDetail";

function table(items: QRadarHostEntry[], over = {}) {
  const hosts = hostsOf(items, over);
  const rows = joinHealth(items, []).rows;
  return render(<HostsTable name="qradar-lab" rows={rows} hosts={hosts} />);
}

/** The row group (tbody) that holds a host. */
function group(host: string): HTMLElement {
  const body = screen
    .getAllByRole("rowgroup")
    .find((el) => el.tagName === "TBODY" && within(el).queryByText(host));
  if (!body) throw new Error(`no row group for ${host}`);
  return body;
}

const tones = (el: HTMLElement) => [...el.querySelectorAll(".pill.warn, .pill.crit")];

describe("HostsTable: single hosts", () => {
  it("shows hostname and address, role, appliance, version, status and the rates in their columns", () => {
    table([hostEntry()]);
    const row = within(group("qr-console")).getAllByRole("row")[0];
    expect(row).toHaveTextContent("qr-console");
    expect(row).toHaveTextContent("10.0.0.10");
    expect(row).toHaveTextContent("Console");
    expect(row).toHaveTextContent("Console 3199");
    expect(row).toHaveTextContent("7.5.0");
    expect(screen.getByRole("columnheader", { name: "Role, appliance and version" })).toBeInTheDocument();
    expect(row).toHaveTextContent("ACTIVE");
    const cells = within(row).getAllByRole("cell");
    // Seven columns: host (the row header), role and appliance, status, then per block the
    // average and peak (stacked) and the allocation of the hardware limit.
    expect(cells).toHaveLength(6);
    expect(cells.slice(2, 4).map((c) => c.textContent)).toEqual(["1,8003,900", "5,000 of 20,000"]);
    expect(cells.slice(4, 6).map((c) => c.textContent)).toEqual(["90,000150,000", "200,000 of 400,000"]);
    for (const cell of cells.slice(2)) expect(cell).toHaveClass("num");
  });

  it("names the units on the rate columns and says what the figures are", () => {
    table([hostEntry()]);
    expect(screen.getByRole("columnheader", { name: /EPS \(events\/s\), previous month/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /FPM \(flows\/min\), previous month/ })).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader", { name: "Average / peak" })).toHaveLength(2);
    expect(screen.getAllByRole("columnheader", { name: "Allocated of hardware limit" })).toHaveLength(2);
    expect(screen.getByText(/counted once for an HA pair/)).toBeInTheDocument();
  });

  it("warns about a single host that is not ACTIVE, with the status as QRadar wrote it", () => {
    table([hostEntry({ members: [member({ status: ok("DISCONNECTED") })] })]);
    const pill = group("qr-console").querySelector(".pill.warn");
    expect(pill).toHaveTextContent("DISCONNECTED");
  });

  it("does not warn about an ACTIVE host", () => {
    table([hostEntry()]);
    expect(tones(group("qr-console"))).toHaveLength(0);
  });

  it("shows a dash for a role QRadar gave no components for, with the reason as its title", () => {
    table([hostEntry({ role: null })]);
    const cell = within(group("qr-console")).getAllByRole("cell")[0];
    expect(cell).toHaveTextContent("—");
    expect(within(cell).getByTitle(/no component list/)).toBeInTheDocument();
  });

  it("says why a host's rates are missing instead of printing zeros", () => {
    table([
      hostEntry({
        eps: notRead("NOT_COLLECTED", "QRadar gave no EPS for this host.", "field_missing"),
        fpm: ok(rate({ average: null, peak: null })),
      }),
    ]);
    const row = within(group("qr-console")).getAllByRole("row")[0];
    expect(row).toHaveTextContent("Not collected");
    expect(row).toHaveTextContent("QRadar gave no EPS for this host.");
    // A figure QRadar left out is a dash, not a 0.
    const cells = within(row).getAllByRole("cell");
    expect(cells.slice(-2).map((c) => c.textContent)).toEqual(["——", "5,000 of 20,000"]);
    // No cell of the row prints a bare zero for a figure that was not given.
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
  });
});

describe("HostsTable: HA pairs", () => {
  it("shows a healthy pair as one row group with both members, and never warns about the standby", () => {
    table([pairEntry("ha_healthy", ["ACTIVE", "STANDBY"])]);
    const body = group("ep-ha");
    expect(within(body).getAllByRole("row")).toHaveLength(3);
    expect(body).toHaveTextContent("Healthy pair");
    expect(body).toHaveTextContent("The primary is active and the secondary is on standby, as expected.");
    const standby = within(body).getByText("ep-ha-b").closest("tr") as HTMLElement;
    expect(standby).toHaveTextContent("Secondary");
    expect(standby).toHaveTextContent("STANDBY");
    expect(standby).toHaveTextContent("Standby, as expected");
    const primary = within(body).getByText("ep-ha-a").closest("tr") as HTMLElement;
    expect(primary).toHaveTextContent("Primary");
    expect(primary).toHaveTextContent("ACTIVE");
    // No warning or error token anywhere in the group, and no "not ACTIVE" wording.
    expect(tones(body)).toHaveLength(0);
    expect(body.textContent).not.toMatch(/not ACTIVE/i);
  });

  it("counts the pair's EPS and FPM once, on the host row, never on a member", () => {
    table([pairEntry("ha_healthy", ["ACTIVE", "STANDBY"])]);
    const rows = within(group("ep-ha")).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("8,000");
    expect(rows[0]).toHaveTextContent("7,200");
    expect(rows[1]).not.toHaveTextContent(/8,000|7,200/);
    expect(rows[2]).not.toHaveTextContent(/8,000|7,200/);
    expect(screen.getAllByText(/^8,000 of /)).toHaveLength(1);
  });

  it("warns about a degraded pair with one sentence naming both statuses", () => {
    table([pairEntry("ha_degraded", ["ACTIVE", "OFFLINE"])]);
    const body = group("ep-ha");
    const pill = body.querySelector(".pill.warn");
    expect(pill).toHaveTextContent("Degraded pair");
    expect(body.querySelector(".pill.crit")).toBeNull();
    expect(body).toHaveTextContent("no failover is possible. Reported: ACTIVE / OFFLINE.");
    // The offline member is a fact, not an "expected standby".
    expect(body).not.toHaveTextContent("Standby, as expected");
  });

  it("warns about a pair that failed over, and says the secondary is running the host", () => {
    table([pairEntry("ha_failed_over", ["OFFLINE", "ACTIVE"])]);
    const body = group("ep-ha");
    expect(body.querySelector(".pill.warn")).toHaveTextContent("Failed over");
    expect(body).toHaveTextContent("The secondary is running this host and the primary is not. Reported: OFFLINE / ACTIVE.");
  });

  it("takes the error tone only for a pair with no active member", () => {
    table([pairEntry("ha_failed", ["FAILED", "OFFLINE"])]);
    const body = group("ep-ha");
    expect(body.querySelector(".pill.crit")).toHaveTextContent("Failed pair");
    expect(body.querySelector(".pill.warn")).toBeNull();
    expect(body).toHaveTextContent("No member is active. Reported: FAILED / OFFLINE.");
  });

  it("shows an unclassified pair with the statuses as QRadar wrote them, and warns", () => {
    table([pairEntry("unknown", ["ACTIVE", "Needs License"], { unclassified: ["ACTIVE", "Needs License"] })]);
    const body = group("ep-ha");
    expect(body.querySelector(".pill.warn")).toHaveTextContent("Unknown pair state");
    expect(body).toHaveTextContent("QRadar reported ACTIVE / Needs License, which this platform does not classify");
    expect(body).not.toHaveTextContent("Healthy pair");
  });

  it("says a member whose status was not read has none, rather than a healthy look", () => {
    table([pairEntry("unknown", ["ACTIVE", null])]);
    const standby = within(group("ep-ha")).getByText("ep-ha-b").closest("tr") as HTMLElement;
    expect(standby).toHaveTextContent("Not collected");
    expect(standby).toHaveTextContent("The server list was not read.");
    expect(standby).not.toHaveTextContent("Standby, as expected");
  });

  it("keeps every state's word distinct", () => {
    const words = (
      [
        ["ha_healthy", ["ACTIVE", "STANDBY"]],
        ["ha_degraded", ["ACTIVE", "OFFLINE"]],
        ["ha_failed_over", ["OFFLINE", "ACTIVE"]],
        ["ha_failed", ["FAILED", "FAILED"]],
        ["unknown", ["ACTIVE", "ACTIVE"]],
      ] as const
    ).map(([state, statuses]) => {
      const view = table([pairEntry(state, [statuses[0], statuses[1]])]);
      const text = group("ep-ha").querySelector(".sl-status .pill")?.textContent ?? "";
      view.unmount();
      return text;
    });
    expect(new Set(words).size).toBe(5);
  });
});

describe("HostsTable: the whole table", () => {
  it("lists a single host and a pair as separate row groups", () => {
    table([hostEntry(), pairEntry("ha_healthy", ["ACTIVE", "STANDBY"])]);
    expect(screen.getAllByRole("rowgroup").filter((el) => el.tagName === "TBODY")).toHaveLength(2);
  });

  it("says the host list was truncated, with the count read", () => {
    table([hostEntry()], { truncated: true, hosts: 100 });
    expect(screen.getByRole("status")).toHaveTextContent("The table shows the first 100.");
  });

  it("says an empty host list is empty and why", () => {
    table([]);
    expect(screen.getByText("QRadar lists no managed host.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says why the host table was not read, and draws no table", () => {
    render(
      <HostsTable
        name="qradar-lab"
        rows={[]}
        hosts={notRead("FAILED", "QRadar did not answer the host list.", "timeout")}
      />,
    );
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("QRadar did not answer the host list.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says an older backend sent no host table", () => {
    render(<HostsTable name="qradar-lab" rows={[]} hosts={undefined} />);
    expect(screen.getByText("The backend sent no host table.")).toBeInTheDocument();
  });

  it("names the table", () => {
    table([hostEntry()]);
    expect(screen.getByRole("table", { name: "Managed hosts of qradar-lab" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Managed hosts of qradar-lab" })).toBeInTheDocument();
  });
});
