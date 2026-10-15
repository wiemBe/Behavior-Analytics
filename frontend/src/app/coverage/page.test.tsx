import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CoveragePage from "./page";
import {
  ApiError,
  api,
  type CoverageSummary,
  type DataSourceCoverageView,
  type Page,
  type RuleCoverageView,
  type TechniqueCoverage,
} from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - any of the four reads fails: a "Backend unreachable" notice naming the coverage
//    sync command;
//  - no technique mappings at all (`total_techniques === 0`): the honest
//    "Coverage has not been evaluated" state, which must never read as 0% covered;
//  - the normal view: the five status counters, the summary line, the technique
//    table, the rule-centric view and the data-source-centric view;
//  - an empty technique page, an empty rule view and an empty data-source view, each
//    with its own notice;
//  - honesty: a missing technique name / tactic / score is an em dash, and a status
//    with no reason falls back to `healthMeaning` (an unevaluated technique is
//    neutral, never green or red).
// The status pills carry a tone class the page uses as meaning (healthTone:
// COVERED -> ok, MISSING -> crit, NOT_EVALUATED -> neutral), so those are asserted.

function summary(over: Partial<CoverageSummary> = {}): CoverageSummary {
  return {
    generated_at: "2026-10-15T09:00:00Z",
    total_techniques: 12,
    by_status: { COVERED: 6, PARTIAL: 2, DEGRADED: 1, MISSING: 2, NOT_EVALUATED: 1 },
    covered_ratio: 0.5,
    average_coverage_score: 0.62,
    mapping_provenance: { explicit: 9, inferred: 3 },
    ...over,
  };
}

function technique(over: Partial<TechniqueCoverage> = {}): TechniqueCoverage {
  return {
    technique_id: "T1110",
    technique_name: "Brute Force",
    tactic: "Credential Access",
    status: "COVERED",
    coverage_score: 0.9,
    confidence: 0.8,
    reason: null,
    mapped_rule_count: 3,
    enabled_rule_count: 2,
    firing_rule_count: 1,
    inferred_rule_count: 0,
    degraded_rule_count: 0,
    last_evaluated_at: "2026-10-15T09:00:00Z",
    ...over,
  };
}

function ruleView(over: Partial<RuleCoverageView> = {}): RuleCoverageView {
  return {
    rule_id: "r-1",
    qradar_id: 77,
    name: "Multiple login failures from a single source",
    enabled: true,
    health_status: "HEALTHY",
    techniques: [
      {
        technique_id: "T1110",
        technique_name: "Brute Force",
        tactic: "Credential Access",
        source: "EXPLICIT",
        confidence: 0.9,
        provenance: null,
      },
    ],
    ...over,
  };
}

function dataSource(over: Partial<DataSourceCoverageView> = {}): DataSourceCoverageView {
  return {
    kind: "LOG_SOURCE",
    target_ref: "227",
    target_name: "Core Firewall",
    rules: [
      {
        rule_id: "r-1",
        qradar_id: 77,
        name: "Multiple login failures from a single source",
        enabled: true,
        health_status: "HEALTHY",
        dependency_source: "EXPLICIT",
        dependency_confidence: 0.8,
      },
    ],
    techniques: ["T1110"],
    ...over,
  };
}

function page(
  items: TechniqueCoverage[],
  over: Partial<Page<TechniqueCoverage>> = {},
): Page<TechniqueCoverage> {
  return { items, total: items.length, limit: 50, offset: 0, ...over };
}

async function renderPage(params: Record<string, string> = {}) {
  return render(await CoveragePage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "coverageSummary").mockResolvedValue(summary());
  vi.spyOn(api, "coverageTechniques").mockResolvedValue(page([technique()]));
  vi.spyOn(api, "coverageByRule").mockResolvedValue([ruleView()]);
  vi.spyOn(api, "coverageByDataSource").mockResolvedValue([dataSource()]);
});

describe("coverage states", () => {
  it("explains a failed read and names the sync command", async () => {
    vi.spyOn(api, "coverageSummary").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Detection Coverage" })).toBeInTheDocument();
    const notice = screen.getByText(/Backend unreachable/);
    expect(notice).toHaveTextContent("python -m app.cli.sync coverage");
    expect(notice).not.toHaveTextContent("secret");
  });

  it("refuses to claim zero coverage when nothing is mapped", async () => {
    vi.spyOn(api, "coverageSummary").mockResolvedValue(
      summary({ total_techniques: 0, by_status: {}, covered_ratio: 0 }),
    );
    vi.spyOn(api, "coverageTechniques").mockResolvedValue(page([]));
    await renderPage();
    const notice = screen.getByText("Coverage has not been evaluated.").closest(".notice")!;
    expect(notice).toHaveTextContent(/not a finding of zero coverage/);
    expect(notice).toHaveTextContent("POST /api/v1/coverage/mappings");
    // No status counters and no technique table in this state.
    expect(screen.queryByText("COVERED")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Technique" })).toBeNull();
  });

  it("passes the offset to the API and writes it to the paging links", async () => {
    const read = vi.spyOn(api, "coverageTechniques").mockResolvedValue(
      page([technique()], { total: 120, limit: 50, offset: 50 }),
    );
    await renderPage({ offset: "50" });
    expect(read).toHaveBeenCalledWith({ limit: 50, offset: 50 });
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByText("51–100 of 120")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/coverage?offset=100",
    );
  });
});

describe("coverage contents", () => {
  it("counts every status, including the zeros", async () => {
    vi.spyOn(api, "coverageSummary").mockResolvedValue(
      summary({ by_status: { COVERED: 6, PARTIAL: 0, DEGRADED: 0, MISSING: 0, NOT_EVALUATED: 0 } }),
    );
    await renderPage();
    const card = (label: string) =>
      screen.getByText(label, { selector: ".k" }).closest(".card") as HTMLElement;
    expect(within(card("COVERED")).getByText("6")).toHaveClass("ok");
    expect(within(card("NOT EVALUATED")).getByText("0")).toBeInTheDocument();
  });

  it("states the provenance of the mappings and the generation time in UTC", async () => {
    await renderPage();
    expect(
      screen.getByText(
        /12 techniques evaluated · 50% covered · 9 explicit \/ 3 inferred mappings ·/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/generated 2026-10-15 09:00:00 UTC/)).toBeInTheDocument();
  });

  it("shows every technique column with score as a percentage", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[0];
    for (const header of [
      "Technique",
      "Name",
      "Tactic",
      "Status",
      "Rules",
      "Score",
      "Evidence",
    ]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("T1110");
    expect(row).toHaveTextContent("Brute Force");
    expect(row).toHaveTextContent("Credential Access");
    expect(row).toHaveTextContent("COVERED");
    expect(within(row).getByText("90%")).toBeInTheDocument();
    expect(row).toHaveTextContent("At least one healthy enabled rule maps to this technique.");
  });

  it("renders the rule-centric view with Yes/No and the mapping evidence", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[1];
    expect(within(table).getByRole("columnheader", { name: "Enabled" })).toBeInTheDocument();
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Multiple login failures from a single source");
    expect(row).toHaveTextContent("Yes");
    expect(row).toHaveTextContent("HEALTHY");
    expect(row).toHaveTextContent("T1110");
    expect(row).toHaveTextContent("explicit 90%");
  });

  it("renders the data-source-centric view, falling back to the ref and an em dash", async () => {
    vi.spyOn(api, "coverageByDataSource").mockResolvedValue([
      dataSource({ target_name: null, techniques: [] }),
    ]);
    await renderPage();
    const tables = screen.getAllByRole("table");
    const table = tables[tables.length - 1];
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("227");
    expect(row).toHaveTextContent("LOG SOURCE");
    expect(row).toHaveTextContent("explicit 80%");
    expect(within(row).getByText("—")).toBeInTheDocument();
  });
});

describe("coverage honesty", () => {
  it("keeps an unevaluated technique neutral and explains it", async () => {
    vi.spyOn(api, "coverageTechniques").mockResolvedValue(
      page([technique({ status: "NOT_EVALUATED", coverage_score: null })]),
    );
    await renderPage();
    const pill = screen.getByText("NOT EVALUATED", { selector: ".pill" });
    expect(pill.className).not.toContain("ok");
    expect(pill.className).not.toContain("crit");
    expect(screen.getByText("No evaluation has run for this technique yet.")).toBeInTheDocument();
    // A score that was never computed is an em dash, never 0%.
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("shows an em dash for a missing name, tactic and score", async () => {
    vi.spyOn(api, "coverageTechniques").mockResolvedValue(
      page([technique({ technique_name: null, tactic: null, coverage_score: null })]),
    );
    await renderPage();
    const row = within(screen.getAllByRole("table")[0]).getAllByRole("row")[1];
    expect(within(row).getAllByText("—")).toHaveLength(3);
  });

  it("explains a missing technique with its own meaning", async () => {
    vi.spyOn(api, "coverageTechniques").mockResolvedValue(
      page([technique({ status: "MISSING", coverage_score: 0, reason: "No rule maps to it." })]),
    );
    await renderPage();
    expect(screen.getByText("No rule maps to it.")).toBeInTheDocument();
    expect(screen.getByText("MISSING", { selector: ".pill" })).toHaveClass("crit");
  });

  it("says each list is empty with its own notice", async () => {
    vi.spyOn(api, "coverageTechniques").mockResolvedValue(page([]));
    vi.spyOn(api, "coverageByRule").mockResolvedValue([]);
    vi.spyOn(api, "coverageByDataSource").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No techniques on this page.")).toBeInTheDocument();
    expect(screen.getByText("No rules have ATT&CK technique mappings yet.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "No rule-to-log-source dependencies are available from this QRadar inventory.",
      ),
    ).toBeInTheDocument();
  });
});
