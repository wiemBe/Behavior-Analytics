import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RuleMatchBlock } from "./RuleMatchBlock";
import { block, row, span, SPAN_REASON } from "./ruleMatchFixtures";

function show(data: Parameters<typeof RuleMatchBlock>[0]["data"], failed = false) {
  return render(<RuleMatchBlock data={data} failed={failed} />);
}

describe("the rule-match block", () => {
  it("asks its question and lists a row with every column the design names", () => {
    show(block());
    expect(screen.getByRole("heading", { name: "Rule-match anomalies" })).toBeInTheDocument();
    expect(screen.getByText("Which rules changed match rate this span?")).toBeInTheDocument();
    const table = screen.getByRole("table");
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(heads).toEqual([
      "Rule",
      "Detector",
      "Observed (matches / window)",
      "Expected (matches / window)",
      "State",
      "Started (UTC)",
      "Incident",
    ]);
    const r = within(table).getAllByRole("row")[1];
    expect(within(r).getByRole("link", { name: "Multiple login failures from a single source" })).toHaveAttribute(
      "href",
      "/rules/22222222-2222-2222-2222-222222222222",
    );
    expect(r).toHaveTextContent("QRadar id 100123");
    expect(r).not.toHaveTextContent("Building block");
    expect(r).toHaveTextContent("Rule storm");
    expect(r).toHaveTextContent("4,800");
    expect(r).toHaveTextContent("310.5");
    expect(within(r).getByText("OPEN")).toBeInTheDocument();
    expect(r).toHaveTextContent("UTC");
    expect(within(r).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/99999999-0000-4000-8000-000000000001",
    );
  });

  it("states the window width and the span in the note", () => {
    show(block());
    expect(screen.getByText(/A window is 1 h long/)).toHaveTextContent("last 7 days");
  });

  it("marks a building block on the rule cell", () => {
    show(block({ items: [row({ is_building_block: true })] }));
    expect(screen.getByRole("table")).toHaveTextContent("QRadar id 100123 · Building block");
  });

  it("says an OK, complete span with no row is empty, and draws no table", () => {
    show(block({ items: [], total: 0 }));
    expect(screen.getByText("No rule-match anomalies in this span.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("NOT_COLLECTED says the collector has not run, with no table and no empty sentence", () => {
    show(
      block({
        state: "NOT_COLLECTED",
        span: null,
        items: null,
        total: null,
        reason: "Rule-match collection is off.",
        reason_code: "rule.match_anomaly.collection_off",
      }),
    );
    expect(screen.getByText("Rule-match collection has not run for this span.")).toBeInTheDocument();
    expect(screen.getByText("Rule-match collection is off.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("This is not a count of zero");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("No rule-match anomalies in this span.")).toBeNull();
  });

  it("INSUFFICIENT_DATA repeats the window reason and the counts, and still lists storm rows with the span status", () => {
    show(
      block({
        state: "INSUFFICIENT_DATA",
        span: span({
          complete: false,
          window_status: "incomplete",
          expected_windows: 168,
          complete_windows: 162,
          missing_windows: 3,
          truncated_windows: 2,
          failed_windows: 1,
          reason: SPAN_REASON,
        }),
        reason: SPAN_REASON,
        items: [row({ window_status: "incomplete" })],
      }),
    );
    expect(screen.getByText("Match rates were not fully measured over this span.")).toBeInTheDocument();
    expect(screen.getByText(SPAN_REASON)).toBeInTheDocument();
    expect(
      screen.getByText("Windows in the span: 168 expected, 162 complete, 3 missing, 2 truncated, 1 failed."),
    ).toBeInTheDocument();
    const r = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(r).toHaveTextContent("Rule storm");
    expect(r).toHaveTextContent("Span incomplete");
  });

  it("INSUFFICIENT_DATA with no storm row does not say the span was clean", () => {
    show(
      block({
        state: "INSUFFICIENT_DATA",
        span: span({ complete: false, window_status: "incomplete", reason: SPAN_REASON }),
        reason: SPAN_REASON,
        items: [],
        total: 0,
      }),
    );
    expect(screen.getByText("No storm was found in the windows that were measured. Silence was not judged.")).toBeInTheDocument();
    expect(screen.queryByText("No rule-match anomalies in this span.")).toBeNull();
  });

  it("names a TRUNCATED row, draws its count as a lower bound and does not call it exact", () => {
    show(block({ items: [row({ row_state: "TRUNCATED", observed: 10000 })] }));
    const r = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(r).toHaveTextContent("≥ 10,000");
    expect(r).toHaveTextContent("Truncated window: at least this many matches, not an exact count.");
  });

  it("does not mark a row OK as truncated", () => {
    show(block());
    expect(screen.queryByText(/Truncated window/)).toBeNull();
    expect(screen.queryByText(/≥/)).toBeNull();
  });

  it("says the list is cut and how many incidents there are in total", () => {
    show(block({ items_truncated: true, total: 83, items: [row(), row({ anomaly_id: "b", rule_id: "c" })] }));
    expect(screen.getByText(/Showing 2 of 83 incidents in this span/)).toBeInTheDocument();
  });

  it("counts the incidents when the list is whole", () => {
    show(block({ total: 1 }));
    expect(screen.getByText("1 rule-match incident in this span.")).toBeInTheDocument();
  });

  it("draws a missing observed or expected count as the dash, never 0", () => {
    show(block({ items: [row({ observed: null, expected: null })] }));
    const r = within(screen.getByRole("table")).getAllByRole("row")[1];
    const cells = within(r).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("—");
    expect(cells[2]).toHaveTextContent("—");
    expect(r).not.toHaveTextContent(/(^|\s)0(\s|$)/);
  });

  it("keeps a measured zero observed count (a silence) as 0", () => {
    show(block({ items: [row({ detector: "RULE_SILENCE", observed: 0, expected: 12 })] }));
    const r = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(r).toHaveTextContent("Rule silence");
    expect(within(r).getAllByRole("cell")[1]).toHaveTextContent(/^0/);
  });

  it("its own failure says the read failed and nothing else", () => {
    show(null, true);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Match anomalies could not be loaded.");
    expect(alert).toHaveTextContent("The other blocks on this page are not affected.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("treats a missing answer as a failure and not as an empty list", () => {
    show(null, false);
    expect(screen.getByRole("alert")).toHaveTextContent("Match anomalies could not be loaded.");
    expect(screen.queryByText("No rule-match anomalies in this span.")).toBeNull();
  });

  it("the failed flag wins over an answer that is present: the error, never the rows", () => {
    show(block({ items: [row()], total: 1 }), true);
    expect(screen.getByRole("alert")).toHaveTextContent("Match anomalies could not be loaded.");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("Multiple login failures")).toBeNull();
  });

  it("an answer whose state says NOT_COLLECTED is not collected even if it carries rows", () => {
    show(block({ state: "NOT_COLLECTED", span: null, items: [row()], total: 1 }));
    expect(screen.getByText("Rule-match collection has not run for this span.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("No rule-match anomalies in this span.")).toBeNull();
  });

  it("an answer with no item list is not collected whatever its state says, and is never an empty list", () => {
    for (const state of ["OK", "INSUFFICIENT_DATA"] as const) {
      const { unmount } = show(block({ state, items: null, total: null }));
      expect(screen.getByText("Rule-match collection has not run for this span.")).toBeInTheDocument();
      expect(screen.queryByRole("table")).toBeNull();
      expect(screen.queryByText("No rule-match anomalies in this span.")).toBeNull();
      unmount();
    }
  });
});
