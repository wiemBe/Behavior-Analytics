import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RulesPage from "./page";
import { ApiError, api, type RuleSummary } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "rules").mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([]);
});

describe("rules page header", () => {
  it("links to rule statistics from the page header", async () => {
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Rule statistics" })).toHaveAttribute(
      "href",
      "/rules/statistics",
    );
  });
});

function rule(over: Partial<RuleSummary> = {}): RuleSummary {
  return {
    id: "r-1",
    qradar_id: 77,
    name: "Multiple login failures",
    enabled: true,
    is_building_block: false,
    rule_type: "EVENT",
    origin: "USER",
    owner: "SOC",
    health_status: "HEALTHY",
    health_evaluated_at: "2026-10-15T09:00:00Z",
    last_fired_at: "2026-10-14T22:10:00Z",
    offense_contribution_count: 12,
    event_contribution_count: 90,
    mitre_techniques: [],
    ...over,
  };
}

describe("rules list layout", () => {
  beforeEach(() => {
    vi.spyOn(api, "rules").mockResolvedValue({ items: [rule()], total: 1, limit: 25, offset: 0 });
  });

  it("puts the table in a scroll region and right-aligns the offense count", async () => {
    const { container } = render(await RulesPage({ searchParams: Promise.resolve({}) }));
    const region = screen.getByRole("region", { name: "Rules" });
    expect(within(region).getByRole("columnheader", { name: "Offenses" })).toHaveClass("num");
    expect(region.querySelector("td.num")).toHaveTextContent("12");
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("names the zone once in the header and keeps the time on one line", async () => {
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("columnheader", { name: "Last fired (UTC)" })).toBeInTheDocument();
    const cell = screen.getByText("10-14 22:10");
    expect(cell).toHaveClass("nowrap");
    expect(cell).not.toHaveTextContent("UTC");
  });

  it("sizes the health-count group from its count, so no card is left alone on a row", async () => {
    vi.spyOn(api, "ruleHealthSummary").mockResolvedValue(
      ["HEALTHY", "DEGRADED", "SILENT", "NOISY", "DISABLED", "NEVER_OBSERVED", "UNKNOWN"].map(
        (status) => ({ status, count: 1 }),
      ),
    );
    const { container } = render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(container.querySelectorAll(".cards .card")).toHaveLength(7);
    expect(container.querySelector(".cards")).toHaveClass("rules-cards-4");
  });

  it("says in an alert what failed and what to do", async () => {
    vi.spyOn(api, "rules").mockRejectedValue(new ApiError(500, "secret"));
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was changed");
  });
});
