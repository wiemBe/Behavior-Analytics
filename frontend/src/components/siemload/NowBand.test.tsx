import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ArielLoadCurrent } from "@/lib/api";

import { current, window } from "./fixtures";
import { NowBand } from "./NowBand";

const band = (over: Partial<ArielLoadCurrent> = {}) => render(<NowBand current={current(over)} />);

function value(label: string): HTMLElement {
  return screen.getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;
}

function row(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(`^${name}`) });
}

describe("NowBand", () => {
  it("shows when the figures are as of, in UTC with the zone", () => {
    band();
    expect(screen.getByText(/^As of/)).toHaveTextContent("2026-11-02 10:15:03 UTC");
  });

  it("shows our searches in flight, active and stale, with the stale limit", () => {
    band();
    const cell = value("Searches in flight");
    expect(cell).toHaveTextContent("2 active · 1 stale");
    expect(cell).toHaveTextContent("Stale after 150 s without an end.");
  });

  it("shows the permit slots held against the cap, and what is kept back", () => {
    band();
    const cell = value("Permit slots held");
    expect(cell).toHaveTextContent("4 of 8 global");
    expect(cell).toHaveTextContent("3 console-pool slots held across all consoles, cap 4 each");
    expect(cell).toHaveTextContent("2 kept back for foreground work");
  });

  it("says the permit pool is not enforced instead of showing a cap", () => {
    band({ slots: { available: true, enforced: false } });
    const cell = value("Permit slots held");
    expect(cell).toHaveTextContent("Not enforced");
    expect(cell).not.toHaveTextContent(/\d/);
  });

  it("shows the last 5, 15 and 60 minutes: submitted, completed, failed, refused and wall time", () => {
    band();
    const table = screen.getByRole("table", { name: /last 5, 15 and 60 minutes/ });
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual([
      "Window",
      "Submitted",
      "Completed",
      "Failed",
      "Refused",
      "Wall p50",
      "Wall p95",
    ]);
    const cells = (name: string) =>
      within(row(name)).getAllByRole("cell").map((c) => c.textContent);
    expect(cells("Last 5 min")).toEqual(["5", "4", "0", "1", "640 ms", "1.9 s"]);
    expect(cells("Last 60 min")).toEqual(["47", "44", "2", "1", "910 ms", "12.4 s"]);
  });

  it("right-aligns the figures and says the unit with every wall time", () => {
    band();
    for (const cell of within(row("Last 15 min")).getAllByRole("cell")) {
      expect(cell).toHaveClass("num");
    }
    expect(within(row("Last 15 min")).getByText("820 ms")).toBeInTheDocument();
    expect(within(row("Last 15 min")).getByText("4.2 s")).toBeInTheDocument();
  });

  it("shows a dash, not a zero, for a wall time when no search completed", () => {
    band({
      windows: {
        available: true,
        minutes_5: window({ submitted: 1, completed: 0, failed: 1, refused: 0, wall_ms_p50: null, wall_ms_p95: null }),
        minutes_15: window(),
        minutes_60: window(),
      },
    });
    const cells = within(row("Last 5 min")).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["1", "0", "1", "0", "—", "—"]);
  });

  it("says no search was submitted in the hour, with measured zeros and no wall time", () => {
    const none = window({ submitted: 0, completed: 0, failed: 0, refused: 0, wall_ms_p50: null, wall_ms_p95: null });
    band({ windows: { available: true, minutes_5: none, minutes_15: none, minutes_60: none } });
    expect(screen.getByText("No search was submitted in the last 60 minutes.")).toBeInTheDocument();
    expect(within(row("Last 60 min")).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "0", "0", "0", "0", "—", "—",
    ]);
  });

  it("does not claim 'none sent' while searches were sent in the hour", () => {
    band();
    expect(screen.queryByText("No search was submitted in the last 60 minutes.")).not.toBeInTheDocument();
  });

  it("explains how wall time and failed are counted", () => {
    band();
    expect(screen.getByText(/covers completed searches only/)).toBeInTheDocument();
  });
});

describe("NowBand: a part that could not be read", () => {
  it("shows the in-flight part's reason and no count", () => {
    band({ in_flight: { available: false, reason: "in-flight searches could not be read" } });
    const cell = value("Searches in flight");
    expect(cell).toHaveTextContent("Not read");
    expect(cell).toHaveTextContent("in-flight searches could not be read");
    expect(cell).not.toHaveTextContent(/\d/);
    expect(value("Permit slots held")).toHaveTextContent("4 of 8 global");
  });

  it("shows the slots part's reason and no cap", () => {
    band({ slots: { available: false, reason: "permit slots could not be read" } });
    const cell = value("Permit slots held");
    expect(cell).toHaveTextContent("permit slots could not be read");
    expect(cell).not.toHaveTextContent(/\d/);
  });

  it("shows the windows part's reason and no table, not a table of zeros", () => {
    band({ windows: { available: false, reason: "recent searches could not be read" } });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(/recent searches could not be read/)).toBeInTheDocument();
    expect(screen.queryByText("No search was submitted in the last 60 minutes.")).not.toBeInTheDocument();
  });
});
