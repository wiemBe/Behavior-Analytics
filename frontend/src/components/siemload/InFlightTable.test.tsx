import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ArielInFlight, QRadarHealthPart } from "@/lib/api";

import { DR_ID, LAB_ID, current, entry, health } from "./fixtures";
import { InFlightTable } from "./InFlightTable";

const consoles: QRadarHealthPart = {
  available: true,
  consoles: [health(), health({ instance_id: DR_ID, instance_name: "qradar-dr" })],
};
const flight = (part: ArielInFlight = current().in_flight, qradar: QRadarHealthPart | undefined = consoles) =>
  render(<InFlightTable part={part} qradar={qradar} />);

describe("InFlightTable", () => {
  it("lists caller, console, state, submitted time, age, polls and range", () => {
    flight();
    const table = screen.getByRole("table", { name: "Searches in flight" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Caller",
      "Console",
      "State",
      "Submitted (UTC)",
      "Age",
      "Polls",
      "Range searched",
    ]);
    const first = within(table).getByRole("row", { name: /Log source volume/ });
    const cells = within(first).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["Log source volume", "qradar-lab", "Active", "11-02 10:14", "13 s", "4", "5 min"]);
  });

  it("puts the submitted time in a time element with the full UTC instant", () => {
    flight();
    const time = screen.getAllByText("11-02 10:14")[0];
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-11-02T10:14:50Z");
    expect(time).toHaveAttribute("title", "2026-11-02 10:14:50 UTC");
  });

  it("sets stale searches apart under a label that says why, and marks each one Stale", () => {
    flight();
    const stale = screen.getByText(/Stale: older than 150 s/);
    expect(stale).toHaveTextContent("The worker probably stopped before it recorded an end.");
    const group = stale.closest("tbody") as HTMLElement;
    const rows = within(group).getAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText("Stale")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Rule matches")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1 d")).toBeInTheDocument();
    // The active rows are not in the stale group.
    expect(within(group).queryByText("Log source volume")).not.toBeInTheDocument();
    expect(screen.getAllByText("Active")).toHaveLength(2);
  });

  it("shows no stale group when nothing is stale", () => {
    flight({ ...(current().in_flight as Extract<ArielInFlight, { available: true }>), stale: [] });
    expect(screen.queryByText(/Stale: older than/)).not.toBeInTheDocument();
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
  });

  it("names the console by id when it is not one the health part knows, and a dash for none", () => {
    flight(
      {
        available: true,
        active: [entry({ instance_id: "99999999-9999-4999-8999-999999999999" }), entry({ instance_id: null })],
        stale: [],
        stale_after_seconds: 150,
      },
      { available: false, reason: "the registered consoles could not be read" },
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("99999999-9999-4999-8999-999999999999")).toBeInTheDocument();
    expect(within(rows[1]).getAllByRole("cell")[1]).toHaveTextContent("—");
  });

  it("shows a missing range as a dash and a caller it does not know as itself", () => {
    flight({
      available: true,
      active: [entry({ caller: "BRAND_NEW", range_seconds: null })],
      stale: [],
      stale_after_seconds: 150,
    });
    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("BRAND_NEW");
    expect(cells[6]).toHaveTextContent("—");
  });

  it("uses the second console's name for its searches", () => {
    flight({
      available: true,
      active: [entry({ instance_id: DR_ID })],
      stale: [],
      stale_after_seconds: 150,
    });
    expect(screen.getByText("qradar-dr")).toBeInTheDocument();
    expect(screen.queryByText(LAB_ID)).not.toBeInTheDocument();
  });

  it("says no search is in flight, and draws no table", () => {
    flight({ available: true, active: [], stale: [], stale_after_seconds: 150 });
    expect(screen.getByText("No search is in flight.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says the part could not be read, with its reason, and does not say none is in flight", () => {
    flight({ available: false, reason: "in-flight searches could not be read" });
    expect(screen.getByText(/in-flight searches could not be read/)).toBeInTheDocument();
    expect(screen.queryByText("No search is in flight.")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
