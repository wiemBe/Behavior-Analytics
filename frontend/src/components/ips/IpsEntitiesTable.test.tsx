import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AnomalyState } from "@/lib/api";
import { ipsEntity } from "./behaviorFixtures";
import { IpsEntitiesTable } from "./IpsEntitiesTable";

function rowOf(text: string) {
  return within(screen.getByText(text).closest("tr")!);
}

describe("IPS entities table", () => {
  it("has column headers, a caption and a labelled scroll region", () => {
    render(<IpsEntitiesTable entities={[ipsEntity()]} />);
    expect(screen.getAllByRole("columnheader")).toHaveLength(5);
    expect(screen.getByRole("table")).toHaveAccessibleName(/Admitted entities of this IPS source/);
    expect(screen.getByRole("region", { name: "IPS entities" })).toBeInTheDocument();
  });

  it("names an entity by its dimension and key, and a signature by its name and QID", () => {
    render(
      <IpsEntitiesTable
        entities={[
          ipsEntity(),
          ipsEntity({
            id: "s",
            dimension: "SIGNATURE",
            value_key: "qid:5000123",
            display_name: "SMB brute force attempt",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Destination port 445/tcp")).toBeInTheDocument();
    expect(screen.getByText("SMB brute force attempt (QID 5000123)")).toBeInTheDocument();
  });

  it("reads a state of null as not evaluated, never as NORMAL", () => {
    render(<IpsEntitiesTable entities={[ipsEntity({ state: null })]} />);
    const r = rowOf("Destination port 445/tcp");
    expect(r.getByText("Not evaluated")).toBeInTheDocument();
    expect(r.queryByText(/normal/i)).toBeNull();
  });

  it("shows a state a detector reported", () => {
    render(<IpsEntitiesTable entities={[ipsEntity({ state: "OPEN", active_anomaly_count: 1 })]} />);
    expect(rowOf("Destination port 445/tcp").getByText("Open")).toBeInTheDocument();
  });

  it("shows baseline adequacy as reliable of total, and says when none can drive a verdict", () => {
    render(
      <IpsEntitiesTable
        entities={[
          ipsEntity({ id: "a", value_key: "445/tcp" }),
          ipsEntity({ id: "b", value_key: "22/tcp", baseline: { cells_total: 40, reliable_cells: 0 } }),
          ipsEntity({ id: "c", value_key: "80/tcp", baseline: { cells_total: 0, reliable_cells: 0 } }),
        ]}
      />,
    );
    expect(rowOf("Destination port 445/tcp").getByText("131 of 168")).toBeInTheDocument();
    const none = rowOf("Destination port 22/tcp");
    expect(none.getByText("0 of 40")).toBeInTheDocument();
    expect(none.getByText("None reliable yet")).toBeInTheDocument();
    const empty = rowOf("Destination port 80/tcp");
    expect(empty.getByText("None")).toBeInTheDocument();
    expect(empty.getByText("No baseline built")).toBeInTheDocument();
  });

  it("distinguishes active, retired (with its time) and inactive", () => {
    render(
      <IpsEntitiesTable
        entities={[
          ipsEntity({ id: "a", value_key: "1/tcp" }),
          ipsEntity({ id: "r", value_key: "2/tcp", active: false, retired_at: "2026-10-05T00:00:00Z" }),
          ipsEntity({ id: "i", value_key: "3/tcp", active: false }),
        ]}
      />,
    );
    expect(rowOf("Destination port 1/tcp").getByText("Active")).toBeInTheDocument();
    const retired = rowOf("Destination port 2/tcp");
    expect(retired.getByText("Retired")).toBeInTheDocument();
    expect(retired.getByText(/since/)).toBeInTheDocument();
    expect(rowOf("Destination port 3/tcp").getByText("Inactive")).toBeInTheDocument();
  });

  it("links to the anomalies of an entity only when it has active ones", () => {
    render(
      <IpsEntitiesTable
        entities={[
          ipsEntity({ id: "quiet", value_key: "1/tcp" }),
          ipsEntity({ id: "e1500000-0000-4000-8000-0000000000aa", value_key: "2/tcp", active_anomaly_count: 2 }),
        ]}
      />,
    );
    expect(rowOf("Destination port 1/tcp").queryByRole("link")).toBeNull();
    expect(rowOf("Destination port 2/tcp").getByRole("link", { name: "2 active anomalies" })).toHaveAttribute(
      "href",
      "/anomalies?ips_entity_id=e1500000-0000-4000-8000-0000000000aa",
    );
  });
});

describe("IPS entities table: state words", () => {
  const states: AnomalyState[] = [
    "INSUFFICIENT_DATA",
    "NORMAL",
    "CANDIDATE",
    "OPEN",
    "RECOVERING",
    "RESOLVED",
    "SUPPRESSED",
  ];

  it.each(states)("words %s and never prints a raw enum with an underscore", (state) => {
    render(<IpsEntitiesTable entities={[ipsEntity({ state })]} />);
    const cell = within(screen.getByText("Destination port 445/tcp").closest("tr")!).getAllByRole("cell")[4];
    expect(cell.textContent).not.toMatch(/[A-Z]+_[A-Z]+/);
  });

  it("words INSUFFICIENT_DATA as such, muted, not like NORMAL", () => {
    render(<IpsEntitiesTable entities={[ipsEntity({ state: "INSUFFICIENT_DATA" })]} />);
    const word = screen.getByText("Insufficient data");
    expect(word).toHaveClass("muted");
    expect(word.className).not.toContain("ok");
  });
});
