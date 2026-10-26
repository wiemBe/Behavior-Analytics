// The coverage route and the configuration-changes notice in Turkish: every state, the status
// words by value (an unknown value reads as the code with spaces, never empty), the honesty
// of "not evaluated", and the Turkish number form.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { createTextTranslator } from "@/i18n/textTranslator";
import {
  ApiError,
  api,
  type CoverageSummary,
  type DataSourceCoverageView,
  type Page,
  type RuleCoverageView,
  type TechniqueCoverage,
} from "@/lib/api";
import { kindLabel, mappingSourceLabel, statusLabel } from "@/lib/coverageText";

import tr from "../../../messages/tr";
import ConfigChangesPage from "../config-changes/page";
import CoverageError from "./error";
import CoverageLoading from "./loading";
import CoveragePage from "./page";

const turkish = createTextTranslator("tr", tr);

function summary(over: Partial<CoverageSummary> = {}): CoverageSummary {
  return {
    generated_at: "2026-10-15T09:00:00Z",
    total_techniques: 1204,
    by_status: { COVERED: 602, PARTIAL: 2, DEGRADED: 1, MISSING: 2, NOT_EVALUATED: 1, FUTURE_STATE: 4 },
    covered_ratio: 0.5,
    average_coverage_score: 0.62,
    mapping_provenance: { explicit: 1000, inferred: 204 },
    ...over,
  };
}

function technique(over: Partial<TechniqueCoverage> = {}): TechniqueCoverage {
  return {
    technique_id: "T1110",
    technique_name: "Brute Force",
    tactic: "Credential Access",
    status: "COVERED",
    coverage_score: 0.905,
    confidence: 0.8,
    reason: null,
    mapped_rule_count: 1204,
    enabled_rule_count: 2,
    firing_rule_count: 1,
    inferred_rule_count: 0,
    degraded_rule_count: 0,
    last_evaluated_at: "2026-10-15T09:00:00Z",
    ...over,
  };
}

const ruleView = (over: Partial<RuleCoverageView> = {}): RuleCoverageView => ({
  rule_id: "r-1",
  qradar_id: 77,
  name: "Multiple login failures",
  enabled: false,
  health_status: "NEVER_OBSERVED",
  techniques: [
    { technique_id: "T1110", technique_name: "Brute Force", tactic: null, source: "EXPLICIT", confidence: 0.9, provenance: null },
    { technique_id: "T1078", technique_name: null, tactic: null, source: "INFERRED", confidence: 0.25, provenance: null },
  ],
  ...over,
});

const dataSource = (over: Partial<DataSourceCoverageView> = {}): DataSourceCoverageView => ({
  kind: "LOG_SOURCE_TYPE",
  target_ref: "227",
  target_name: "Check Point",
  rules: [
    { rule_id: "r-1", qradar_id: 77, name: "Multiple login failures", enabled: true, health_status: "HEALTHY", dependency_source: "INFERRED", dependency_confidence: 0.8 },
  ],
  techniques: [],
  ...over,
});

const page = (items: TechniqueCoverage[]): Page<TechniqueCoverage> => ({ items, total: items.length, limit: 50, offset: 0 });
const renderPage = async (params: Record<string, string> = {}) =>
  render(await CoveragePage({ searchParams: Promise.resolve(params) }));

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
  vi.spyOn(api, "coverageSummary").mockResolvedValue(summary());
  vi.spyOn(api, "coverageTechniques").mockResolvedValue(page([technique(), technique({ technique_id: "T1000", status: "NOT_EVALUATED", technique_name: null, tactic: null, coverage_score: null, reason: "Backend reason, as sent." })]));
  vi.spyOn(api, "coverageByRule").mockResolvedValue([ruleView()]);
  vi.spyOn(api, "coverageByDataSource").mockResolvedValue([dataSource()]);
});

describe("/coverage in Turkish", () => {
  it("titles the page and counts every status in the Turkish number form", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Algılama kapsamı" })).toBeInTheDocument();
    expect(screen.getByText(/Eşlenen kurallardan ve gözlenen sağlıklarından türetilen MITRE ATT&CK teknik kapsamı\./)).toBeInTheDocument();
    for (const [label, value] of [
      ["KAPSANDI", "602"],
      ["KISMİ", "2"],
      ["BOZULMUŞ", "1"],
      ["EKSİK", "2"],
      ["DEĞERLENDİRİLMEDİ", "1"],
      ["FUTURE STATE", "4"],
    ]) {
      expect(screen.getByText(label, { selector: ".k" }).parentElement).toHaveTextContent(value);
    }
    expect(document.body).toHaveTextContent(
      "1.204 teknik değerlendirildi · %50,0 kapsandı · 1.000 açık / 204 çıkarımsal eşleme · oluşturulma: 2026-10-15 09:00:00 UTC",
    );
  });

  it("labels the technique table and words each status; a reason from the backend stays as sent", async () => {
    await renderPage();
    const table = screen.getByRole("table", { name: "Teknikler" });
    for (const name of ["Teknik", "Ad", "Taktik", "Durum", "Kurallar", "Puan", "Kanıt"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const [, covered, unevaluated] = within(table).getAllByRole("row");
    expect(covered).toHaveTextContent("KAPSANDI");
    expect(within(covered).getByText("KAPSANDI")).toHaveClass("ok");
    expect(covered).toHaveTextContent("1.204");
    expect(covered).toHaveTextContent("%90,5");
    expect(within(unevaluated).getByText("DEĞERLENDİRİLMEDİ")).not.toHaveClass("ok", "crit", "warn");
    expect(unevaluated).toHaveTextContent("Backend reason, as sent.");
    expect(unevaluated.textContent).toContain("—");
  });

  it("says coverage was not evaluated, never 0 %, when nothing is mapped", async () => {
    vi.mocked(api.coverageSummary).mockResolvedValue(summary({ total_techniques: 0, by_status: {}, covered_ratio: 0 }));
    await renderPage();
    expect(screen.getByText("Kapsam değerlendirilmedi.")).toBeInTheDocument();
    expect(screen.getByText(/Eşlemeleri bir yönetici ekler\. Bu, sıfır kapsam bulgusu değildir\./)).toBeInTheDocument();
    expect(screen.queryByText(/%0/)).toBeNull();
    expect(screen.queryByRole("table", { name: "Teknikler" })).toBeNull();
  });

  it("says what is empty in each of the three views", async () => {
    vi.mocked(api.coverageTechniques).mockResolvedValue(page([]));
    vi.mocked(api.coverageByRule).mockResolvedValue([]);
    vi.mocked(api.coverageByDataSource).mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("Bu sayfada teknik yok.")).toBeInTheDocument();
    expect(screen.getByText("Henüz hiçbir kuralın ATT&CK teknik eşlemesi yok.")).toBeInTheDocument();
    expect(screen.getByText("Bu QRadar envanterinde kuraldan log source'a bağımlılık bilgisi yok.")).toBeInTheDocument();
  });

  it("words the rule-centric and the data-source-centric views", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { level: 3, name: "Kural odaklı görünüm" })).toBeInTheDocument();
    const rules = screen.getByRole("table", { name: "Kurala göre kapsam" });
    for (const name of ["Kural", "Etkin", "Sağlık", "Teknikler", "Kanıt"]) {
      expect(within(rules).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const rule = within(rules).getAllByRole("row")[1];
    expect(rule).toHaveTextContent("Hayır");
    expect(rule).toHaveTextContent("HİÇ GÖZLENMEDİ");
    expect(rule).toHaveTextContent("açık %90,0; çıkarımsal %25,0");
    expect(screen.getByRole("heading", { level: 3, name: "Veri kaynağı odaklı görünüm" })).toBeInTheDocument();
    const sources = screen.getByRole("table", { name: "Veri kaynağına göre kapsam" });
    for (const name of ["Veri kaynağı", "Tür", "Kurallar", "Teknikler", "Kanıt"]) {
      expect(within(sources).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const source = within(sources).getAllByRole("row")[1];
    expect(source).toHaveTextContent("Log source türü");
    expect(source).toHaveTextContent("çıkarımsal %80,0");
    expect(source).toHaveTextContent("—");
  });

  it.each([403, 401, 500])("keeps the heading and shows the failure on a %s", async (status) => {
    vi.mocked(api.coverageSummary).mockRejectedValue(new ApiError(status, "x"));
    await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Algılama kapsamı" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("announces loading and the route error without the exception", () => {
    const a = render(<CoverageLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Algılama kapsamı yükleniyor…");
    a.unmount();
    render(<CoverageError error={Object.assign(new Error("secret"), { digest: "c1" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Algılama kapsamı gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: c1.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
  });
});

describe("the coverage words by value", () => {
  it("has a Turkish word for every status the backend defines", () => {
    const codes = ["COVERED", "PARTIAL", "DEGRADED", "MISSING", "NOT_COVERED", "NOT_EVALUATED", "HEALTHY", "NOISY", "INACTIVE", "NEVER_OBSERVED", "DEPENDENCY_DEGRADED", "DISABLED", "INSUFFICIENT_DATA", "UNKNOWN"];
    for (const code of codes) {
      expect(statusLabel(turkish, code), code).not.toBe(code.replace(/_/g, " "));
    }
  });

  it("shows a value this build does not know as the code with spaces, never empty, and never as a path", () => {
    expect(statusLabel(turkish, "FUTURE_STATE")).toBe("FUTURE STATE");
    expect(statusLabel(turkish, "a.b")).toBe("a.b");
    expect(statusLabel(turkish, "")).toBe("");
    expect(kindLabel(turkish, "NEW_KIND")).toBe("NEW KIND");
    expect(kindLabel(turkish, "BUILDING_BLOCK")).toBe("Yapı bloğu");
    expect(mappingSourceLabel(turkish, "EXPLICIT")).toBe("açık");
    expect(mappingSourceLabel(turkish, "MANUAL")).toBe("manual");
  });
});

describe("/config-changes in Turkish", () => {
  it("says the changes are not collected and why the page is empty", () => {
    render(<ConfigChangesPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Yapılandırma değişiklikleri" })).toBeInTheDocument();
    expect(screen.getByText("Yapılandırma değişiklikleri toplanmıyor.")).toBeInTheDocument();
    expect(screen.getByText(/onu dolduran bir toplayıcı ya da API yok/)).toBeInTheDocument();
  });

  it("keeps the English notice in an English session", () => {
    setTestLocale("en");
    render(<ConfigChangesPage />);
    expect(screen.getByText("Configuration changes are not collected.")).toBeInTheDocument();
  });
});
