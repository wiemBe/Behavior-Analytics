import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExecutionsTable } from "./ExecutionsTable";
import type { SearchExecution } from "@/lib/api";

function exec(id: string, over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id, search_id: "s-1", query_version: 1, status: "COMPLETED", trigger: "SCHEDULED",
    triggered_by: null, ariel_search_id: null, ariel_status: null,
    started_at: "2026-07-20T10:00:00Z", completed_at: "2026-07-20T10:00:30Z",
    duration_ms: 1200, result_count: 12, truncated: false, error_type: null,
    error_message: null, retry_count: 0, threshold_breached: false, ...over,
  };
}

function row(index: number): HTMLElement {
  return screen.getAllByRole("row")[index + 1];
}

describe("ExecutionsTable truncated runs", () => {
  const rows = [
    exec("a", { truncated: true, result_count: 500, threshold_breached: true }),
    exec("b", { truncated: true, result_count: 500 }),
    exec("c", { result_count: 12 }),
  ];

  it("reads a truncated count as a lower bound, announced as 'at least'", () => {
    render(<ExecutionsTable executions={rows} />);
    const cell = within(row(0)).getByText("500").closest("td")!;
    expect(cell).toHaveTextContent("at least");
    expect(within(cell).getByText("≥", { exact: false })).toHaveAttribute("aria-hidden", "true");
    expect(cell).not.toHaveTextContent(/trunc/i);
  });

  it("shows a truncated breach as breached", () => {
    render(<ExecutionsTable executions={rows} />);
    expect(within(row(0)).getByText("breached")).toBeInTheDocument();
    expect(within(row(0)).queryByText("no verdict")).not.toBeInTheDocument();
  });

  it("shows 'no verdict' for a truncated run that did not breach, with the reason", () => {
    render(<ExecutionsTable executions={rows} />);
    const verdict = within(row(1)).getByText("no verdict");
    expect(verdict).toHaveAttribute("title", expect.stringMatching(/lower bound/i));
  });

  it("keeps a measured run within its threshold as a dash, distinct from no verdict", () => {
    render(<ExecutionsTable executions={rows} />);
    const cells = within(row(2)).getAllByRole("cell");
    expect(cells[7]).toHaveTextContent("—");
    expect(within(row(2)).queryByText("no verdict")).not.toBeInTheDocument();
    expect(cells[5]).toHaveTextContent("12");
    expect(cells[5]).not.toHaveTextContent("at least");
  });

  it("explains the markers once, below the table, only when a run was truncated", () => {
    const { rerender } = render(<ExecutionsTable executions={rows} />);
    expect(screen.getByRole("list", { name: "Truncated runs" })).toBeInTheDocument();
    rerender(<ExecutionsTable executions={[exec("c")]} />);
    expect(screen.queryByRole("list", { name: "Truncated runs" })).not.toBeInTheDocument();
  });

  it("does not claim a number for a truncated run with no recorded count", () => {
    render(<ExecutionsTable executions={[exec("d", { truncated: true, result_count: null })]} />);
    expect(row(0)).toHaveTextContent(/truncated, count not recorded/i);
  });

  it("keeps a measured zero as 0 and a missing count as a dash", () => {
    render(
      <ExecutionsTable
        executions={[exec("e", { result_count: 0 }), exec("f", { result_count: null, status: "FAILED" })]}
      />,
    );
    expect(within(row(0)).getAllByRole("cell")[5]).toHaveTextContent("0");
    expect(within(row(1)).getAllByRole("cell")[5]).toHaveTextContent("—");
  });

  it("shows durations with a unit, right-aligned", () => {
    render(
      <ExecutionsTable
        executions={[
          exec("h", { duration_ms: 41200 }),
          exec("i", { duration_ms: 300000 }),
          exec("j", { duration_ms: null }),
        ]}
      />,
    );
    expect(within(row(0)).getAllByRole("cell")[4]).toHaveTextContent("41.2 s");
    expect(within(row(1)).getAllByRole("cell")[4]).toHaveTextContent("5 min 0 s");
    expect(within(row(2)).getAllByRole("cell")[4]).toHaveTextContent("—");
    expect(within(row(0)).getAllByRole("cell")[4]).toHaveClass("num");
  });

  it("shows start times in UTC with the zone", () => {
    render(<ExecutionsTable executions={[exec("g")]} />);
    expect(row(0)).toHaveTextContent("07-20 10:00 UTC");
  });
});
