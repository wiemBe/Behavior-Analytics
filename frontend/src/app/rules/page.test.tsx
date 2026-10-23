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

  it("keeps the year and the zone in the last-fired cell", async () => {
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("columnheader", { name: "Last fired (UTC)" })).toBeInTheDocument();
    const cell = screen.getByRole("time");
    expect(cell).toHaveTextContent("2026-10-14 22:10:00 UTC");
    expect(cell.closest("td")).not.toHaveClass("nowrap");
  });

  it("does not attribute every unestablished rule to a missing last-fired time", async () => {
    vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([
      { status: "INSUFFICIENT_DATA", count: 4 },
      { status: "HEALTHY", count: 2 },
    ]);
    const { container } = render(await RulesPage({ searchParams: Promise.resolve({}) }));
    const notice = [...container.querySelectorAll(".notice")].find((n) =>
      n.textContent?.includes("dependency that has not been observed"),
    );
    expect(notice).toBeTruthy();
    expect(notice).toHaveTextContent("dependency that has not been observed");
    expect(notice).toHaveTextContent("no last-fired time");
    expect(notice).toHaveTextContent("does not say which cause applies to each");
    expect(notice).not.toHaveTextContent("silence cannot be distinguished");
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
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
  });

  it.each([
    [403, new ApiError(403, "secret"), "You do not have permission to view rules. Ask an administrator for the read:rules grant."],
    [401, new ApiError(401, "secret"), "Your session has expired. Sign in again to continue."],
    [500, new ApiError(500, "secret"), "Rules could not be loaded. The backend may be unreachable; reload the page to try again."],
    ["network", new Error("fetch failed"), "Rules could not be loaded. The backend may be unreachable; reload the page to try again."],
  ])("names what failed for a %s read failure, with no developer instruction", async (_case, error, sentence) => {
    vi.spyOn(api, "rules").mockRejectedValue(error);
    await RulesPage({ searchParams: Promise.resolve({}) }).then((p) => render(p));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    for (const gone of ["Backend unreachable", "Start the stack", "POST", "secret", "!"]) {
      expect(alert).not.toHaveTextContent(gone);
    }
    expect(screen.queryByRole("table")).toBeNull();
  });
});
