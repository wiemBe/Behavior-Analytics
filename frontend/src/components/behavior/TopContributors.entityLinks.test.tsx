import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TopContributors } from "./TopContributors";
import type { Contributor, ExplanationDimension, ExplanationPackage } from "@/lib/api";
import { entityHref } from "@/lib/entities";

// UIQ-2: a card value links to the entity timeline exactly when the contributor table would
// link it: decided by the dimension's name alone, through the same helper.

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

function packageOf(name: string, c: Contributor): ExplanationPackage {
  const dimension = {
    dimension: name,
    availability: "AVAILABLE",
    contributors: [c],
    baseline_distinct_count: 5,
    anomaly_distinct_count: 6,
    cardinality_ratio: 1.2,
    baseline_top_share: 0.19,
    anomaly_top_share: 0.73,
    new_value_count: 1,
    disappeared_value_count: 0,
    truncated: false,
    detail: null,
  } as ExplanationDimension;
  return { status: "COMPLETE", dimensions: [dimension] } as unknown as ExplanationPackage;
}

function renderCard(name: string, value: string) {
  render(
    <TopContributors
      packaged={packageOf(name, contributor(name, value))}
      anomalyType="VOLUME_SPIKE"
      evidenceStatus="COMPLETE"
    />,
  );
}

describe("top contributor card values", () => {
  it.each([
    ["source_ip", "10.0.0.5", "ip"],
    ["destination_ip", "198.51.100.9", "ip"],
    ["username", "alice", "user"],
  ] as const)("links %s to the stored timeline", (name, value, kind) => {
    renderCard(name, value);
    const link = screen.getByRole("link", { name: `Open the stored timeline for ${value}` });
    expect(link).toHaveAttribute("href", entityHref({ kind, value }));
    expect(link).toHaveTextContent(value);
  });

  it("does not link another dimension, even when the value looks like an address", () => {
    renderCard("destination_port", "10.0.0.5");
    expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /stored timeline/ })).toBeNull();
  });

  it("does not link an empty value of a linkable dimension", () => {
    renderCard("source_ip", "  ");
    expect(screen.queryByRole("link", { name: /stored timeline/ })).toBeNull();
  });

  it("does not link a value longer than the entity limit", () => {
    renderCard("username", "u".repeat(5000));
    expect(screen.queryByRole("link", { name: /stored timeline/ })).toBeNull();
  });
});
