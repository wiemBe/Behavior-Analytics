// The calls the route waves left in English on a Turkish page (I18N-12, section C), each pinned
// by a rendered Turkish check, with the English text unchanged next to it.
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import CoveragePage from "@/app/coverage/page";
import LogSourcesPage from "@/app/log-sources/page";
import { RecentIpsAnomaliesView } from "@/components/catalog/RecentIpsAnomalies";
import { MappingHistory } from "@/components/fieldmapping/MappingHistory";
import { SCHEMA } from "@/components/fieldmapping/fixtures";
import { ipsListItem } from "@/components/ips/fixtures";
import { ok } from "@/components/home/testkit";
import RulesPage from "@/app/rules/page";
import { page } from "@/lib/home/fixtures";
import { ApiError, api, type RuleSummary } from "@/lib/api";

const entry = (outcome: string | null) => ({
  at: "2026-10-13T09:00:00Z",
  actor: "system",
  action: "waf.profile.validated",
  profile_version: 2,
  mapping_before: null,
  mapping_after: null,
  outcome,
  reasons: null,
});

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
});

describe("the history outcome of a field mapping", () => {
  it("reads its label in Turkish and the lower-case code in English", () => {
    const { unmount } = render(<MappingHistory schema={SCHEMA} entries={[entry("INVALID")]} />);
    expect(screen.getByText("Sonuç: Geçersiz")).toBeInTheDocument();
    unmount();
    const busy = render(<MappingHistory schema={SCHEMA} entries={[entry("SOURCE_BUSY")]} />);
    expect(screen.getByText("Sonuç: Kaynak meşgul")).toBeInTheDocument();
    busy.unmount();
    const done = render(<MappingHistory schema={SCHEMA} entries={[entry("SUCCESS")]} />);
    expect(screen.getByText("Sonuç: Başarılı")).toBeInTheDocument();
    done.unmount();
    setTestLocale("en");
    render(<MappingHistory schema={SCHEMA} entries={[entry("INVALID")]} />);
    expect(screen.getByText("Outcome: invalid")).toBeInTheDocument();
  });

  it("shows an outcome the catalog lacks as recorded, and never looks up an inherited name", () => {
    const { unmount } = render(<MappingHistory schema={SCHEMA} entries={[entry("NEW_KIND")]} />);
    expect(screen.getByText("Sonuç: new_kind")).toBeInTheDocument();
    unmount();
    render(<MappingHistory schema={SCHEMA} entries={[entry("constructor")]} />);
    expect(screen.getByText("Sonuç: constructor")).toBeInTheDocument();
  });
});

describe("the IPS dashboard widget state pill", () => {
  it("reads the state in Turkish, and stays the English code in English", () => {
    const merged = [page([ipsListItem({ id: "e1", state: "OPEN" })], 1), page([], 0)] as never;
    const { container, unmount } = render(<RecentIpsAnomaliesView state={ok(merged)} />);
    expect(container.querySelectorAll(".tw-state-word")[1]).toHaveTextContent("AÇIK");
    expect(container.querySelector("td .pill")).toHaveTextContent("YÜKSEK");
    expect(container).not.toHaveTextContent(/\bOPEN\b/);
    unmount();
    setTestLocale("en");
    const english = render(<RecentIpsAnomaliesView state={ok(merged)} />);
    expect(english.container.querySelectorAll(".tw-state-word")[1]).toHaveTextContent("OPEN");
    expect(english.container.querySelector("td .pill")).toHaveTextContent("HIGH");
  });
});

describe("the list pages' read-failure sentence", () => {
  it("names the Coverage page's data and the permission in Turkish", async () => {
    vi.spyOn(api, "coverageSummary").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(api, "coverageTechniques").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    vi.spyOn(api, "coverageByRule").mockResolvedValue([]);
    vi.spyOn(api, "coverageByDataSource").mockResolvedValue([]);
    render(await CoveragePage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "İzin yok: algılama coverage'ı görüntülenemez. read:coverage izni için bir yöneticiye başvurun.",
    );
  });

  it("names the log sources in Turkish", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new Error("down"));
    render(await LogSourcesPage());
    expect(screen.getByText(/^Yüklenemedi: Log source'lar\./)).toBeInTheDocument();
  });
});

describe("the rules list health meaning", () => {
  it("reads the meaning of the verdict in Turkish, and in English", async () => {
    const rule = {
      id: "r-1", qradar_id: 77, name: "Multiple login failures", enabled: true,
      is_building_block: false, rule_type: "EVENT", origin: "USER", owner: "SOC",
      health_status: "HEALTHY", health_evaluated_at: "2026-10-15T09:00:00Z", health_reason: null,
      last_fired_at: "2026-10-14T22:10:00Z", offense_contribution_count: 12,
      event_contribution_count: 90, mitre_techniques: [],
    } as RuleSummary;
    vi.spyOn(api, "rules").mockResolvedValue({ items: [rule], total: 1, limit: 25, offset: 0 });
    vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([]);
    const { unmount } = render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("Beklenen sınırlar içinde tetikleniyor.")).toBeInTheDocument();
    unmount();
    setTestLocale("en");
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("Firing within expected bounds.")).toBeInTheDocument();
  });
});
