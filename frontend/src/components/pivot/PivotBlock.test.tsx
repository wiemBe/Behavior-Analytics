import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Pivot } from "@/lib/pivot";

import { PivotBlock } from "./PivotBlock";

const AQL =
  'SELECT QIDNAME(qid) AS "event_name", SUM(eventcount) AS "events" FROM events ' +
  "WHERE logsourceid = 77 AND starttime >= 1793872800000 AND starttime < 1793874600000 " +
  "GROUP BY qid ORDER BY SUM(eventcount) DESC LIMIT 50 START 1793872800000 STOP 1793874600000";
const SEARCH = "https://qr.lab.example/console/do/ariel/arielSearch?value(aql)=x";
const WINDOW_TEXT = "2026-11-05 10:00:00 UTC to 2026-11-05 10:30:00 UTC";

const BASE: Pivot = {
  kind: "anomaly",
  aql: AQL,
  console_url: SEARCH,
  aql_search_url: SEARCH,
  console_unavailable_reason: null,
  time_range: { start: "2026-11-05T10:00:00Z", stop: "2026-11-05T10:30:00Z" },
  notes: ["Entity filter omitted: the profile maps no host property."],
};

function mockClipboard(impl = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: impl },
    configurable: true,
    writable: true,
  });
  return impl;
}

describe("PivotBlock", () => {
  it("shows the AQL as text, the UTC window and the notes", () => {
    const { container } = render(<PivotBlock pivot={BASE} />);
    expect(screen.getByRole("heading", { name: "Investigate in QRadar" })).toBeInTheDocument();
    expect(screen.getByLabelText("AQL query")).toHaveTextContent(AQL);
    expect(container.querySelector("pre")).toHaveClass("pivot-aql");
    expect(screen.getByText("2026-11-05 10:00:00 UTC")).toHaveAttribute(
      "datetime",
      "2026-11-05T10:00:00Z",
    );
    expect(screen.getByText("2026-11-05 10:30:00 UTC")).toBeInTheDocument();
    expect(
      screen.getByText("Entity filter omitted: the profile maps no host property."),
    ).toBeInTheDocument();
    expect(screen.getByText(/The product does not run it/)).toBeInTheDocument();
  });

  it("renders a query containing markup as text", () => {
    const { container } = render(
      <PivotBlock pivot={{ ...BASE, aql: "<img src=x onerror=alert(1)>" }} />,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("copies the exact AQL and confirms it in a live region", async () => {
    const user = userEvent.setup();
    const writeText = mockClipboard();
    render(<PivotBlock pivot={BASE} />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status.textContent).toBe("");

    await user.click(screen.getByRole("button", { name: "Copy AQL" }));

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(AQL);
    expect(status).toHaveTextContent("AQL copied to clipboard");
  });

  it("copies the UTC window as text", async () => {
    const user = userEvent.setup();
    const writeText = mockClipboard();
    render(<PivotBlock pivot={BASE} />);

    await user.click(screen.getByRole("button", { name: "Copy window" }));

    expect(writeText).toHaveBeenCalledWith(WINDOW_TEXT);
    expect(screen.getByRole("status")).toHaveTextContent("Time range copied to clipboard");
  });

  it("says so when the browser refuses to copy", async () => {
    const user = userEvent.setup();
    mockClipboard(vi.fn().mockRejectedValue(new Error("denied")));
    render(<PivotBlock pivot={BASE} />);

    await user.click(screen.getByRole("button", { name: "Copy AQL" }));

    expect(screen.getByRole("status")).toHaveTextContent("did not allow copying");
  });

  it("opens the console in a new tab without opener or referrer", () => {
    render(<PivotBlock pivot={BASE} />);
    const link = screen.getByRole("link", { name: /Open in QRadar/ });
    expect(link).toHaveAttribute("href", SEARCH);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(link).getByText("(opens in a new tab)")).toHaveClass("sr-only");
    expect(screen.queryByRole("link", { name: /Log Activity/ })).toBeNull();
  });

  it("offers the events search beside the offense page link", () => {
    render(
      <PivotBlock
        pivot={{
          ...BASE,
          kind: "offense",
          console_url: "https://qr.lab.example/console/qradar/jsp/QRadar.jsp?summaryId=4242",
        }}
      />,
    );
    expect(screen.getByRole("link", { name: /Open in QRadar/ })).toHaveAttribute(
      "href",
      "https://qr.lab.example/console/qradar/jsp/QRadar.jsp?summaryId=4242",
    );
    expect(screen.getByRole("link", { name: /Open events in Log Activity/ })).toHaveAttribute(
      "href",
      SEARCH,
    );
  });

  it("has no Open button without a console address, and says why", () => {
    const reason = "The instance has no console host configured.";
    render(
      <PivotBlock
        pivot={{
          ...BASE,
          console_url: null,
          aql_search_url: null,
          console_unavailable_reason: reason,
          notes: [reason],
        }}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("button", { name: "Copy AQL" })).toBeInTheDocument();
    expect(screen.getByText(/Open in QRadar is not shown: The instance has no console host/)).toBeInTheDocument();
    // The reason is said once, not again in the notes.
    expect(screen.queryByRole("heading", { name: "Notes" })).toBeNull();
  });

  it("shows no notes heading when there are none", () => {
    render(<PivotBlock pivot={{ ...BASE, notes: [] }} />);
    expect(screen.queryByRole("heading", { name: "Notes" })).toBeNull();
  });

  it("says what failed and what to do when the pivot could not be read", () => {
    render(<PivotBlock pivot={null} failed />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be built");
    expect(screen.getByRole("alert")).toHaveTextContent("Reload the page");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
