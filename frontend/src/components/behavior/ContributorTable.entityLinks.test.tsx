import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ContributorTable } from "./ContributorTable";
import { DimensionExplorer } from "./DimensionExplorer";
import type { Contributor, ExplanationDimension } from "@/lib/api";

// SOC-16c: an address or a username in the contributor table opens its stored timeline. Which
// values link is decided by the dimension's name alone; a value that merely looks like an address
// in another dimension stays plain text.

function contributor(dimension: string, value: string, over: Partial<Contributor> = {}): Contributor {
  return {
    dimension,
    value,
    label: null,
    baseline_count: 47,
    anomaly_count: 528,
    absolute_delta: 481,
    percent_delta: 1023.4,
    anomaly_share: 0.73,
    baseline_share: 0.19,
    contribution_share: 0.959,
    baseline_rank: 3,
    anomaly_rank: 1,
    rank: 1,
    is_new: false,
    is_disappeared: false,
    ...over,
  };
}

function dimension(name: string, contributors: Contributor[]): ExplanationDimension {
  return {
    dimension: name,
    availability: "AVAILABLE",
    contributors,
    baseline_distinct_count: 5,
    anomaly_distinct_count: 6,
    cardinality_ratio: 1.2,
    baseline_top_share: 0.19,
    anomaly_top_share: 0.73,
    new_value_count: 1,
    disappeared_value_count: 0,
    truncated: false,
    detail: null,
  };
}

describe("evidence values that open the entity timeline", () => {
  it.each([
    ["source_ip", "10.0.0.5", "/entities?kind=ip&value=10.0.0.5"],
    ["destination_ip", "198.51.100.9", "/entities?kind=ip&value=198.51.100.9"],
    ["username", "alice", "/entities?kind=user&value=alice"],
  ])("links %s", (name, value, href) => {
    render(<ContributorTable dimension={dimension(name, [contributor(name, value)])} />);
    const link = screen.getByRole("link", { name: `Open the stored timeline for ${value}` });
    expect(link).toHaveAttribute("href", href);
    expect(link).toHaveTextContent(value);
  });

  it.each(["destination_port", "source_port", "event_name", "action", "category", "protocol", "qid"])(
    "does not link %s, even when the value looks like an address",
    (name) => {
      render(<ContributorTable dimension={dimension(name, [contributor(name, "10.0.0.5")])} />);
      expect(screen.queryByRole("link")).toBeNull();
      expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
    },
  );

  it("encodes the value and never interpolates it as typed", () => {
    const value = "DOM\\a/b&c#d eü";
    render(<ContributorTable dimension={dimension("username", [contributor("username", value)])} />);
    const href = screen.getByRole("link").getAttribute("href")!;
    expect(href).toBe("/entities?kind=user&value=DOM%5Ca%2Fb%26c%23d%20e%C3%BC");
    expect(new URL(href, "http://x").searchParams.get("value")).toBe(value);
  });

  it("links the value, not the label, and keeps the label and the new marker", () => {
    render(
      <ContributorTable
        dimension={dimension("source_ip", [contributor("source_ip", "10.0.0.5", { label: "gateway", is_new: true })])}
      />,
    );
    const cell = screen.getAllByRole("row")[1].querySelector("td")!;
    expect(within(cell).getByRole("link")).toHaveTextContent("10.0.0.5");
    expect(cell).toHaveTextContent("gateway");
    expect(cell).toHaveTextContent("new");
    expect(within(cell).queryByRole("link", { name: /gateway/ })).toBeNull();
  });

  it("does not link a blank value", () => {
    render(<ContributorTable dimension={dimension("username", [contributor("username", "  ")])} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("links through the dimension explorer's table too", () => {
    render(
      <DimensionExplorer
        dimensions={[dimension("source_ip", [contributor("source_ip", "10.0.0.5")])]}
        evidenceStatus="COMPLETE"
      />,
    );
    expect(screen.getByRole("link", { name: "Open the stored timeline for 10.0.0.5" })).toHaveAttribute(
      "href",
      "/entities?kind=ip&value=10.0.0.5",
    );
  });
});
