import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QRadarConsoleHealth, QRadarHealthPart } from "@/lib/api";

import { DR_ID, health, notRead, ok, unreachableHealth } from "./fixtures";
import { QRadarPanel } from "./QRadarPanel";

const panel = (consoles: QRadarConsoleHealth[]) =>
  render(<QRadarPanel part={{ available: true, consoles }} />);

/** The value cell of a row, by its label. */
function value(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: "dt" });
  return term.nextElementSibling as HTMLElement;
}

describe("QRadarPanel: a console that answered", () => {
  it("shows reachability and latency, version and the time the reads were taken, in UTC", () => {
    panel([health()]);
    expect(screen.getByRole("region", { name: "Console qradar-lab" })).toBeInTheDocument();
    expect(value("Reachable")).toHaveTextContent("Reachable");
    expect(value("API latency")).toHaveTextContent("182 ms");
    expect(value("Version")).toHaveTextContent("7.5.0 UP9");
    expect(screen.getByText(/Read from QRadar at/)).toHaveTextContent("2026-11-02 10:14:41 UTC");
  });

  it("says a cached answer is cached and how old it is, with when QRadar is asked again", () => {
    panel([health({ cached: true, age_seconds: 22, cache_ttl_seconds: 30 })]);
    const line = screen.getByText(/Cached, 22 s old/);
    expect(line).toHaveTextContent("2026-11-02 10:14:41 UTC");
    expect(line).toHaveTextContent("30 s");
    expect(screen.queryByText(/Read from QRadar at/)).not.toBeInTheDocument();
  });

  it("shows managed hosts as ACTIVE n of N", () => {
    panel([health()]);
    expect(value("Managed hosts")).toHaveTextContent("ACTIVE 3 of 3");
    expect(value("Managed hosts")).not.toHaveTextContent(/not ACTIVE/);
  });

  it("lists the hosts that are not ACTIVE, with their status, and the ones it could not list", () => {
    panel([
      health({
        servers: ok({
          total: 25,
          active: 3,
          unreported: 1,
          by_status: { ACTIVE: 3, DISCONNECTED: 1 },
          not_active: [
            { hostname: "ep-02", status: "DISCONNECTED" },
            { hostname: "dp-05", status: null },
          ],
          all_active: false,
        }),
      }),
    ]);
    const cell = value("Managed hosts");
    expect(cell).toHaveTextContent("ACTIVE 3 of 25");
    expect(within(cell).getByText("22 not ACTIVE")).toBeInTheDocument();
    const items = within(cell).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("ep-02 — DISCONNECTED");
    expect(items[1]).toHaveTextContent("dp-05 — no status reported");
    expect(cell).toHaveTextContent("20 more not listed");
  });

  it("shows EPS average and peak against the allocation, with the shares", () => {
    panel([health()]);
    const cell = value("EPS");
    expect(cell).toHaveTextContent("Average 4,120 EPS · peak 6,980 EPS");
    expect(cell).toHaveTextContent("of 8,000 EPS allocated over 3 hosts");
    expect(cell).toHaveTextContent("average 51.5 %");
    expect(cell).toHaveTextContent("peak 87.3 %");
  });

  it("names one host in the singular", () => {
    panel([
      health({
        eps: ok({
          hosts: 1,
          average_eps: 119,
          peak_eps: 1_916,
          allocated_eps: 5_000,
          average_share: 0.024,
        }),
      }),
    ]);
    expect(value("EPS")).toHaveTextContent("of 5,000 EPS allocated over 1 host:");
  });

  it("gives no share when the allocation is 0 instead of dividing by it", () => {
    panel([
      health({
        eps: ok({ hosts: 1, average_eps: 10, peak_eps: 20, allocated_eps: 0, average_share: null }),
      }),
    ]);
    const cell = value("EPS");
    expect(cell).toHaveTextContent("Allocation is 0 EPS, so there is no share.");
    expect(cell).not.toHaveTextContent("%");
    expect(cell).not.toHaveTextContent("Infinity");
  });

  it("shows the license pool EPS and FPM without a warning when nothing is over-allocated", () => {
    panel([health()]);
    const cell = value("License pool");
    expect(cell).toHaveTextContent("EPS 8,000 allocated of 10,000 licensed");
    expect(cell).toHaveTextContent("FPM 120,000 allocated of 200,000 licensed");
    expect(within(cell).queryByText("Over-allocated")).not.toBeInTheDocument();
  });

  it("flags an over-allocated pool with the warning pill, on the pool that is over", () => {
    panel([
      health({
        license: ok({
          eps: { total: 10_000, allocated: 12_500, overallocated: true },
          fpm: { total: 200_000, allocated: 120_000, overallocated: false },
        }),
      }),
    ]);
    const cell = value("License pool");
    const pill = within(cell).getByText("Over-allocated");
    expect(pill).toHaveClass("pill", "warn");
    expect(pill.parentElement).toHaveTextContent("EPS 12,500 allocated of 10,000 licensed");
    expect(within(cell).getAllByText("Over-allocated")).toHaveLength(1);
  });

  it("says FPM was not reported when the pool has none, and shows a missing figure as a dash", () => {
    panel([
      health({
        license: ok({ eps: { total: 10_000, allocated: null, overallocated: null }, fpm: null }),
      }),
    ]);
    const cell = value("License pool");
    expect(cell).toHaveTextContent("EPS — allocated of 10,000 licensed");
    expect(cell).toHaveTextContent("FPM not reported");
    expect(within(cell).queryByText("Over-allocated")).not.toBeInTheDocument();
  });

  it("shows Ariel as answering, our recent searches, our searches in flight and the ids QRadar holds", () => {
    panel([health()]);
    expect(value("Ariel")).toHaveTextContent("Answering");
    expect(value("Our searches")).toHaveTextContent(
      "12 searches in the last 15 min, 0 found Ariel unavailable (0.0 %)",
    );
    expect(value("Our searches")).toHaveTextContent("Last completed at");
    expect(value("Ours in flight")).toHaveTextContent("2");
    const held = value("Search ids held");
    expect(held).toHaveTextContent("214");
    expect(held).toHaveTextContent("What QRadar holds for every user, not what it is running.");
  });

  it("calls Ariel not answering when our latest search found it unavailable", () => {
    panel([
      health({
        ariel: {
          ...health().ariel,
          available: ok(false),
          recent: ok({
            window_minutes: 15,
            searches: 5,
            unavailable: 5,
            unavailable_share: 1,
            last_success_at: null,
            last_unavailable_at: "2026-11-02T10:14:20Z",
          }),
        },
      }),
    ]);
    expect(within(value("Ariel")).getByText("Not answering")).toHaveClass("crit");
    expect(value("Our searches")).toHaveTextContent("Last found unavailable at");
    expect(value("Our searches")).not.toHaveTextContent("Last completed");
  });

  it("marks a truncated id count as a lower bound", () => {
    panel([health({ ariel: { ...health().ariel, list_probe: ok({ held: 5000, truncated: true }) } })]);
    expect(value("Search ids held")).toHaveTextContent("≥ 5,000");
  });

  it("sets two consoles apart, each with its own reads", () => {
    panel([health(), health({ instance_id: DR_ID, instance_name: "qradar-dr", api_latency_ms: ok(940) })]);
    expect(screen.getByRole("region", { name: "Console qradar-lab" })).toBeInTheDocument();
    const dr = screen.getByRole("region", { name: "Console qradar-dr" });
    expect(within(dr).getByText("940 ms")).toBeInTheDocument();
  });
});

describe("QRadarPanel: reads that did not happen", () => {
  it("says the console is not reachable, with the reason, and never as healthy", () => {
    panel([unreachableHealth()]);
    const cell = value("Reachable");
    expect(within(cell).getByText("Not reachable")).toHaveClass("crit");
    expect(cell).toHaveTextContent("QRadar did not answer within the read timeout.");
    expect(cell).not.toHaveTextContent(/^Reachable/);
  });

  it("shows FAILED fields as their state and reason, with no number in their place", () => {
    panel([unreachableHealth()]);
    for (const label of ["API latency", "Version"]) {
      const cell = value(label);
      expect(within(cell).getByText("Failed"), label).toHaveClass("pill", "crit");
      expect(cell, label).toHaveTextContent("QRadar did not answer within the read timeout.");
      expect(cell, label).not.toHaveTextContent(/\d/);
    }
    for (const label of ["Managed hosts", "EPS", "License pool", "Search ids held"]) {
      const cell = value(label);
      expect(within(cell).getByText("Not collected"), label).toBeInTheDocument();
      expect(cell, label).not.toHaveTextContent(/\d/);
      expect(cell, label).not.toHaveTextContent("0");
    }
    expect(value("Ariel")).not.toHaveTextContent("Answering");
  });

  it("shows a FORBIDDEN field as forbidden with its sentence, not as empty", () => {
    panel([
      health({
        license: notRead("FORBIDDEN", "QRadar answered 403: the token lacks the capability for this read.", "forbidden"),
      }),
    ]);
    const cell = value("License pool");
    expect(within(cell).getByText("Forbidden")).toHaveClass("warn");
    expect(cell).toHaveTextContent("the token lacks the capability");
    expect(cell).not.toHaveTextContent(/EPS|FPM|allocated/);
    expect(value("Managed hosts")).toHaveTextContent("ACTIVE 3 of 3");
  });

  it("does not show an unread field's value even when the document carries one", () => {
    const hostile = health({
      api_latency_ms: { state: "FAILED", value: 1, reason_code: "timeout", reason: "It timed out." },
    });
    panel([hostile]);
    expect(value("API latency")).not.toHaveTextContent("1 ms");
    expect(value("API latency")).toHaveTextContent("Failed");
  });

  it("says there is no console when none is enabled", () => {
    render(<QRadarPanel part={{ available: true, consoles: [] }} />);
    expect(screen.getByText("No QRadar console is enabled.")).toBeInTheDocument();
    expect(screen.queryByRole("term")).not.toBeInTheDocument();
  });

  it.each<[string, QRadarHealthPart | undefined]>([
    ["with a reason", { available: false, reason: "the registered consoles could not be read" }],
    ["with no part at all", undefined],
  ])("says the health could not be read, %s, and shows no console", (_name, part) => {
    render(<QRadarPanel part={part} />);
    expect(screen.getByText("QRadar's health could not be read, so no figure is shown in its place.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /^Console/ })).not.toBeInTheDocument();
    if (part && !part.available) expect(screen.getByText(/the registered consoles could not be read/)).toBeInTheDocument();
  });
});
