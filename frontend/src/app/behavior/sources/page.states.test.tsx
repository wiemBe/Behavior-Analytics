import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourcesIndexPage from "./page";
import { api, type SourceBehavior } from "@/lib/api";

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "11111111-1111-1111-1111-111111111111",
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 6,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 3,
    state: "OPEN",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 1,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the inventory table", () => {
  it("carries the narrow-width hooks: a named table and a droppable Detail column", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source()]);
    render(await SourcesIndexPage({ searchParams: Promise.resolve({}) }));
    expect(document.querySelector("table.source-inventory")).not.toBeNull();
    // The Detail link repeats the name's own link, so hiding the column loses nothing.
    const detail = screen.getByRole("link", { name: "Detail" });
    expect(detail.closest("td")).toHaveClass("col-detail");
    expect(document.querySelector("th.col-detail")).not.toBeNull();
    expect(screen.getByRole("link", { name: "LAB Firewall" })).toHaveAttribute(
      "href",
      "/behavior/sources/11111111-1111-1111-1111-111111111111",
    );
  });

  it("marks the filter row so its search field can yield width to the button", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source()]);
    render(await SourcesIndexPage({ searchParams: Promise.resolve({}) }));
    expect(document.querySelector("form.filters.source-filters")).not.toBeNull();
  });
});

describe("a failed read", () => {
  it("says what the analyst can do", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new Error("ECONNREFUSED"));
    render(await SourcesIndexPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/reload the page/i);
  });
});
