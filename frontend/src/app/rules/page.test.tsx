import { readFileSync } from "node:fs";
import { join } from "node:path";

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
    health_reason: null,
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
    expect(notice).toHaveTextContent("the reason under each rule name says which cause applies to it");
    expect(notice).not.toHaveTextContent("does not say which cause applies to each");
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

  it("keeps summary labels on word boundaries and folds them at the 1280px breakpoint", () => {
    const css = readFileSync(join(__dirname, "rules.css"), "utf8");
    const labelRule = css.match(/\.cards\.rules-cards \.card \.k \{[^}]*\}/)?.[0] ?? "";
    expect(labelRule).toMatch(/overflow-wrap:\s*normal/);
    expect(labelRule).toMatch(/word-break:\s*normal/);
    expect(css).toMatch(/@media \(max-width:\s*1280px\)/);
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

describe("rules list health reason", () => {
  const LONG =
    "A dependency has not been observed, so the rule cannot be judged: building block " +
    "BB:CategoryDefinition: Authentication Failures has no collected observation in the window, " +
    "and the log source Firewall-DMZ-02 has produced no events since the last evaluation.";

  async function renderRows(items: RuleSummary[]) {
    vi.spyOn(api, "rules").mockResolvedValue({ items, total: items.length, limit: 25, offset: 0 });
    return render(await RulesPage({ searchParams: Promise.resolve({}) }));
  }

  function reasonOf(name: string): HTMLElement | null {
    const row = screen.getByRole("link", { name }).closest("tr")!;
    return row.querySelector<HTMLElement>(".rule-reason");
  }

  it.each(["INSUFFICIENT_DATA", "INACTIVE", "NOISY", "DISABLED", "DEPENDENCY_DEGRADED", "NEVER_OBSERVED"])(
    "shows the stored reason under the name of a %s rule",
    async (status) => {
      await renderRows([
        rule({
          name: "Rule A",
          health_status: status,
          health_reason: "The rule is 3.0 days old, inside the 7-day grace period.",
        }),
      ]);
      const reason = reasonOf("Rule A")!;
      expect(reason).toHaveTextContent("The rule is 3.0 days old, inside the 7-day grace period.");
      expect(reason.closest("td")).toHaveClass("cell-rule-name");
    },
  );

  it("shows nothing for a HEALTHY rule, even when a reason is stored", async () => {
    await renderRows([rule({ name: "Rule A", health_status: "HEALTHY", health_reason: "Firing." })]);
    expect(reasonOf("Rule A")).toBeNull();
  });

  it("shows nothing for an UNKNOWN rule that was never evaluated", async () => {
    await renderRows([
      rule({ name: "Rule A", health_status: "UNKNOWN", health_evaluated_at: null }),
    ]);
    expect(reasonOf("Rule A")).toBeNull();
    expect(screen.queryByText("No reason recorded")).toBeNull();
  });

  it("says no reason was recorded for a verdict that has none", async () => {
    await renderRows([
      rule({ name: "Rule A", health_status: "INACTIVE", health_reason: null }),
      rule({ id: "r-2", name: "Rule B", health_status: "NOISY", health_reason: "  " }),
    ]);
    expect(reasonOf("Rule A")).toHaveTextContent("No reason recorded");
    expect(reasonOf("Rule B")).toHaveTextContent("No reason recorded");
  });

  it("clamps a long reason and keeps the whole text in its title and accessible name", async () => {
    await renderRows([
      rule({ name: "Rule A", health_status: "INSUFFICIENT_DATA", health_reason: LONG }),
    ]);
    const reason = reasonOf("Rule A")!;
    expect(reason).toHaveAttribute("title", LONG);
    expect(reason).toHaveAccessibleName(`Health reason: ${LONG}`);
    expect(reason.textContent).toContain(LONG);
    const css = readFileSync(join(__dirname, "rules.css"), "utf8");
    const block = css.match(/\.rule-list-table \.rule-reason \{[^}]*\}/)?.[0] ?? "";
    expect(block).toMatch(/-webkit-line-clamp:\s*2/);
    expect(block).toMatch(/line-clamp:\s*2/);
    expect(block).not.toMatch(/nowrap/);
  });

  it("labels a reason for assistive technology without adding visible text", async () => {
    await renderRows([
      rule({ name: "Rule A", health_status: "INACTIVE", health_reason: "Quiet for 4 d." }),
    ]);
    const reason = reasonOf("Rule A")!;
    expect(reason).toHaveAccessibleName("Health reason: Quiet for 4 d.");
    expect(reason).toHaveTextContent("Quiet for 4 d.");
    expect(reason).not.toHaveTextContent("Health reason:");
  });
});
