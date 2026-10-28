// The roll-up reason of each technique in the reader's language. A coded reason renders from the
// `backend` catalog in Turkish; an old row (no code) keeps the API's English; a missing reason
// keeps the page's own words for the status. English shows the API's text verbatim.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { api, type Page, type TechniqueCoverage } from "@/lib/api";

import CoveragePage from "./page";

function technique(over: Partial<TechniqueCoverage>): TechniqueCoverage {
  return {
    technique_id: "T1110",
    technique_name: "Brute Force",
    tactic: "Credential Access",
    status: "COVERED",
    coverage_score: 1,
    confidence: 1,
    reason: null,
    mapped_rule_count: 1,
    enabled_rule_count: 1,
    firing_rule_count: 1,
    inferred_rule_count: 0,
    degraded_rule_count: 0,
    last_evaluated_at: "2026-10-15T09:00:00Z",
    ...over,
  };
}

const ROWS: TechniqueCoverage[] = [
  technique({
    technique_id: "T1001",
    reason: "All 4 enabled rules mapped to this technique are detecting.",
    reason_code: "coverage.rollup.all_detecting",
    reason_params: { enabled: 4 },
  }),
  technique({
    technique_id: "T1002",
    status: "PARTIAL",
    reason: "2 of 3 enabled rules are detecting; the rest are not yet established.",
    reason_code: "coverage.rollup.some_unestablished",
    reason_params: { working: 2, enabled: 3 },
  }),
  technique({
    technique_id: "T1003",
    status: "DEGRADED",
    reason: "1 of 3 enabled rules are detecting; 2 are degraded.",
    reason_code: "coverage.rollup.some_degraded",
    reason_params: { working: 1, enabled: 3, degraded: 2 },
  }),
  technique({
    technique_id: "T1004",
    status: "DEGRADED",
    reason: "2 of 3 enabled rules are not currently detecting.",
    reason_code: "coverage.rollup.none_detecting",
    reason_params: { degraded: 2, enabled: 3 },
  }),
  technique({
    technique_id: "T1005",
    status: "MISSING",
    reason: "No rule is mapped to this technique.",
    reason_code: "coverage.rollup.unmapped",
    reason_params: {},
  }),
  // An old stored row: English text, no code.
  technique({ technique_id: "T1006", status: "MISSING", reason: "No rule is mapped to this technique." }),
  // No reason at all: the page's own words for the status.
  technique({ technique_id: "T1007", status: "NOT_EVALUATED", reason: null }),
];

const renderPage = async () => render(await CoveragePage({ searchParams: Promise.resolve({}) }));

function reasonOf(id: string): HTMLElement {
  const row = within(screen.getByRole("table", { name: /Teknikler|Techniques/ }))
    .getAllByRole("row")
    .find((r) => r.textContent?.startsWith(id));
  if (!row) throw new Error(`no row for ${id}`);
  return row.querySelector("td:last-child") as HTMLElement;
}

beforeEach(() => {
  vi.restoreAllMocks();
  const page: Page<TechniqueCoverage> = { items: ROWS, total: ROWS.length, limit: 50, offset: 0 };
  vi.spyOn(api, "coverageSummary").mockResolvedValue({
    generated_at: "2026-10-15T09:00:00Z",
    total_techniques: ROWS.length,
    by_status: { COVERED: 1, PARTIAL: 1, DEGRADED: 2, MISSING: 2, NOT_EVALUATED: 1 },
    covered_ratio: 0.14,
    average_coverage_score: 0.5,
    mapping_provenance: { explicit: 7, inferred: 0 },
  });
  vi.spyOn(api, "coverageTechniques").mockResolvedValue(page);
  vi.spyOn(api, "coverageByRule").mockResolvedValue([]);
  vi.spyOn(api, "coverageByDataSource").mockResolvedValue([]);
});

describe("/coverage roll-up reasons", () => {
  it("renders each roll-up shape in Turkish", async () => {
    setTestLocale("tr");
    await renderPage();
    expect(reasonOf("T1001")).toHaveTextContent("Bu tekniğe eşlenen 4 etkin kuralın tümü algılıyor.");
    expect(reasonOf("T1002")).toHaveTextContent(
      "2 / 3 etkin kural algılıyor; geri kalanların durumu henüz belirlenmedi.",
    );
    expect(reasonOf("T1003")).toHaveTextContent(
      "1 / 3 etkin kural algılıyor; 2 kural bozulmuş.",
    );
    expect(reasonOf("T1004")).toHaveTextContent("2 / 3 etkin kural şu anda algılama yapmıyor.");
    expect(reasonOf("T1005")).toHaveTextContent("Bu tekniğe eşlenmiş kural yok.");
  });

  it("keeps an old row's English and the page's own words for a missing reason", async () => {
    setTestLocale("tr");
    await renderPage();
    expect(reasonOf("T1006")).toHaveTextContent("No rule is mapped to this technique.");
    expect(reasonOf("T1007")).toHaveTextContent("Bu teknik için henüz bir değerlendirme çalışmadı.");
  });

  it("shows the API's text verbatim in English", async () => {
    setTestLocale("en");
    await renderPage();
    expect(reasonOf("T1003")).toHaveTextContent("1 of 3 enabled rules are detecting; 2 are degraded.");
    expect(reasonOf("T1005")).toHaveTextContent("No rule is mapped to this technique.");
  });
});
