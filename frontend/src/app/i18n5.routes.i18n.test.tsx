import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import { ApiError, api, ruleStatisticsApi, type Alert, type OffenseSummary, type RuleSummary } from "@/lib/api";
import { catalog, reader, rule as customRule } from "@/components/customrules/fixtures";
import { resolveRuleSubjects } from "@/components/customrules/subjects";

import AlertsPage from "./alerts/page";
import AlertDetailPage from "./alerts/[id]/page";
import CustomRulesPage from "./custom-rules/page";
import CustomRuleDetailPage from "./custom-rules/[id]/page";
import NewCustomRulePage from "./custom-rules/new/page";
import OffensesPage from "./offenses/page";
import OffenseDetailPage from "./offenses/[id]/page";
import RulesPage from "./rules/page";
import RuleDetailPage from "./rules/[id]/page";
import RuleStatisticsPage from "./rules/statistics/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock("@/components/customrules/subjects", () => ({ resolveRuleSubjects: vi.fn() }));

function offense(over: Partial<OffenseSummary> = {}): OffenseSummary {
  return {
    instance_id: "instance-1",
    qradar_offense_id: 42,
    captured_at: "2026-10-15T09:00:00Z",
    description: "QRadar supplied description",
    status: "OPEN",
    magnitude: 8,
    severity: 7,
    credibility: 6,
    relevance: 5,
    assigned_to: null,
    is_assigned: false,
    offense_type_name: "Source IP",
    offense_source: "10.0.0.5",
    source_network: "Internal",
    event_count: 1234,
    flow_count: 12,
    source_count: 3,
    destination_count: 9,
    start_time: "2026-10-14T22:10:00Z",
    last_updated_time: "2026-10-15T08:55:00Z",
    close_time: null,
    age_seconds: 39600,
    categories: ["Authentication"],
    ...over,
  };
}

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a-1",
    fingerprint: "vol:227:1789",
    title: "Backend alert title",
    description: null,
    severity: "HIGH",
    status: "OPEN",
    source_type: "ANOMALY",
    opened_at: "2026-10-15T07:00:00Z",
    first_seen_at: "2026-10-15T07:00:00Z",
    last_seen_at: "2026-10-15T08:30:00Z",
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 1234,
    evidence_snapshot: {},
    source_anomaly_ids: [],
    ...over,
  };
}

function rule(over: Partial<RuleSummary> = {}): RuleSummary {
  return {
    id: "r-1",
    qradar_id: 77,
    name: "Backend rule name",
    enabled: true,
    is_building_block: false,
    rule_type: "EVENT",
    origin: "USER",
    owner: "SOC",
    health_status: "INSUFFICIENT_DATA",
    health_evaluated_at: "2026-10-15T09:00:00Z",
    health_reason: "Backend health reason",
    last_fired_at: null,
    offense_contribution_count: 1234,
    event_contribution_count: 90,
    mitre_techniques: [],
    ...over,
  };
}

beforeEach(() => {
  setTestLocale("tr");
  vi.mocked(resolveRuleSubjects).mockResolvedValue(new Map());
});

describe("offense routes in Turkish", () => {
  it("renders the populated list, Turkish table number and backend fields", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue({ items: [offense()], total: 1, limit: 25, offset: 0 });
    vi.spyOn(api, "offenseAggregates").mockResolvedValue({
      active: 1234, critical: 2, unassigned: 1, exceeding_sla: 1, oldest_age_seconds: 7200,
      average_magnitude: 5, sla_hours: 24, critical_magnitude: 8,
    });
    render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("heading", { name: "Offense'lar" })).toBeInTheDocument();
    expect(screen.getAllByText("1.234").length).toBeGreaterThan(0);
    expect(screen.getByText("QRadar supplied description")).toBeInTheDocument();
    expect(screen.getByText("Açık", { selector: ".pill" })).toBeInTheDocument();
  });

  it("renders empty, filtered-empty and failed states", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    vi.spyOn(api, "offenseAggregates").mockResolvedValue({
      active: 0, critical: 0, unassigned: 0, exceeding_sla: 0, oldest_age_seconds: null,
      average_magnitude: null, sla_hours: 24, critical_magnitude: 8,
    });
    const empty = render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText(/henüz offense toplanmadığı için/)).toBeInTheDocument();
    empty.unmount();
    render(await OffensesPage({ searchParams: Promise.resolve({ search: "ssh" }) }));
    expect(screen.getByText("Bu filtrelerle eşleşen offense yok.")).toBeInTheDocument();
    vi.restoreAllMocks();
    vi.spyOn(api, "offenses").mockRejectedValue(new ApiError(403, "secret"));
    const failed = render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("read:offenses");
    failed.unmount();
  });

  it("translates the detail failure without issuing an invalid-id request", async () => {
    const read = vi.spyOn(api, "offense");
    render(await OffenseDetailPage({ params: Promise.resolve({ id: "bad" }) }));
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByText("Geçerli bir offense kimliği değil.")).toBeInTheDocument();
  });
});

describe("alert routes in Turkish", () => {
  it("renders every lifecycle label, a Turkish count and an empty state", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue({
      items: [alert(), alert({ id: "a-2", status: "ACKNOWLEDGED" }), alert({ id: "a-3", status: "RESOLVED" })],
      total: 3, limit: 100, offset: 0,
    });
    const populated = render(await AlertsPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("AÇIK")).toBeInTheDocument();
    expect(screen.getByText("ALINDI")).toBeInTheDocument();
    expect(screen.getByText("ÇÖZÜLDÜ")).toBeInTheDocument();
    expect(screen.getAllByText("1.234")).toHaveLength(3);
    populated.unmount();
    vi.spyOn(api, "alerts").mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    render(await AlertsPage({ searchParams: Promise.resolve({ status: "OPEN" }) }));
    expect(screen.getByText("Açık uyarı yok.")).toBeInTheDocument();
  });

  it("renders the detail failure and list permission refusal in Turkish", async () => {
    vi.spyOn(api, "alert").mockRejectedValue(new ApiError(500, "secret"));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    const detail = render(await AlertDetailPage({ params: Promise.resolve({ id: "a-9" }) }));
    expect(screen.getByText("a-9 kimlikli uyarı yüklenemedi.")).toBeInTheDocument();
    detail.unmount();
    vi.spyOn(api, "alerts").mockRejectedValue(new ApiError(403, "secret"));
    render(await AlertsPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("read:alerts");
  });
});

describe("rule routes in Turkish", () => {
  it("renders INSUFFICIENT_DATA, the backend reason and grouped table counts", async () => {
    vi.spyOn(api, "rules").mockResolvedValue({ items: [rule()], total: 1, limit: 25, offset: 0 });
    vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([{ status: "INSUFFICIENT_DATA", count: 1234 }]);
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getAllByText("Yetersiz veri").length).toBeGreaterThan(0);
    expect(screen.getByText("Backend health reason")).toBeInTheDocument();
    expect(screen.getAllByText("1.234").length).toBeGreaterThan(0);
  });

  it("renders list, detail and statistics failures as designed", async () => {
    vi.spyOn(api, "rules").mockRejectedValue(new ApiError(403, "secret"));
    vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([]);
    const list = render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("read:rules");
    list.unmount();
    vi.spyOn(api, "rule").mockRejectedValue(new ApiError(500, "secret"));
    vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([]);
    vi.spyOn(ruleStatisticsApi, "capacityHistory").mockResolvedValue([]);
    const detail = render(await RuleDetailPage({ params: Promise.resolve({ id: "r-9" }) }));
    expect(screen.getByText("Bu kural yüklenemedi. Kısa süre sonra yeniden deneyin.")).toBeInTheDocument();
    detail.unmount();
    vi.spyOn(ruleStatisticsApi, "statistics").mockRejectedValue(new ApiError(404, "none"));
    render(await RuleStatisticsPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("status")).toHaveTextContent("Kayıtlı QRadar sistemi yok");
  });
});

describe("custom-rule routes in Turkish", () => {
  it("renders empty reader and populated honesty states with code tokens", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog());
    vi.spyOn(api, "customRules").mockResolvedValue([]);
    const empty = render(await CustomRulesPage());
    expect(screen.getByText("Henüz özel kural yok.")).toBeInTheDocument();
    expect(screen.getByText("custom_rule:write").tagName).toBe("CODE");
    empty.unmount();
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog({ evaluation_enabled: false, alerting_mode: "shadow" }));
    vi.spyOn(api, "customRules").mockResolvedValue([customRule()]);
    render(await CustomRulesPage());
    expect(screen.getByText("CUSTOM_RULES_ENABLED").tagName).toBe("CODE");
    expect(screen.getByText("CUSTOM_RULES_ALERTING_ENABLED").tagName).toBe("CODE");
  });

  it("renders forbidden detail and a reader-only new-rule state", async () => {
    vi.spyOn(api, "customRule").mockRejectedValue(new ApiError(403, "secret"));
    vi.spyOn(api, "customRuleCatalog").mockRejectedValue(new ApiError(403, "secret"));
    const detail = render(await CustomRuleDetailPage({ params: Promise.resolve({ id: "r-9" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent("read:custom_rules");
    detail.unmount();
    vi.restoreAllMocks();
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog());
    render(await NewCustomRulePage());
    expect(screen.getByText("Kural düzenleyici gizlendi.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Özel kurallara dön" })).toBeInTheDocument();
  });
});
