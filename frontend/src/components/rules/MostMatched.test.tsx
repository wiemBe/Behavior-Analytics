import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MatchRankRow } from "@/lib/api";

import { MostMatched } from "./MostMatched";

const QUERY = { rank: "events" } as unknown as Parameters<typeof MostMatched>[0]["query"];

function row(over: Partial<MatchRankRow>): MatchRankRow {
  return {
    rule_id: "r1",
    qradar_id: 1,
    name: "Rule one",
    enabled: true,
    rule_type: "EVENT",
    is_building_block: false,
    match_records: 12,
    matched_events: 12,
    match_rate_per_second: 0.003,
    lower_bound: false,
    ...over,
  };
}

function rateCell(): HTMLElement {
  const cells = screen.getAllByRole("cell");
  return cells[cells.length - 1];
}

describe("MostMatched rate under the display floor", () => {
  it("never reads '≥ < 0.01' for a lower bound, and never claims '≥ 0.01'", () => {
    render(<MostMatched rows={[row({ lower_bound: true })]} reason={null} query={QUERY} />);
    const text = rateCell().textContent!.replace(/ /g, " ");
    expect(text).toBe("< 0.01 (lower bound)");
    expect(text).not.toMatch(/≥/);
  });

  it("keeps '≥' for a lower bound at or above the floor", () => {
    render(
      <MostMatched
        rows={[row({ lower_bound: true, match_rate_per_second: 1.5 })]}
        reason={null}
        query={QUERY}
      />,
    );
    expect(rateCell().textContent!.replace(/ /g, " ")).toBe("≥ 1.50");
  });

  it("shows an exact sub-floor rate as '< 0.01' only", () => {
    render(<MostMatched rows={[row({})]} reason={null} query={QUERY} />);
    expect(rateCell().textContent).toBe("< 0.01");
  });
});
