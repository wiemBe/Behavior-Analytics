// The rule-health verdict reasons the backend stores (Family 8) in the reader's language: a
// coded reason renders from the `backend` catalog in Turkish — the dependency clauses with
// their labels, the rounded numbers, the grouped counts, the UTC timestamps — and stays the
// API's own text in English; an uncoded reason (a snapshot written before 0036, or a code the
// reader does not know) stays the API's English in both. The row and detail fallbacks when no
// reason was recorded are unchanged.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuleDetailPage from "./[id]/page";
import RulesPage from "./page";
import {
  api,
  ruleStatisticsApi,
  type RuleDetail,
  type RuleHealthSnapshot,
  type RuleSummary,
} from "@/lib/api";

import { setTestLocale } from "../../../vitest.setup";

const NB = "\u00a0";
/** `toHaveTextContent` normalises the element but not the needle, so NBSP becomes a space. */
const flat = (s: string) => s.replace(/\u00a0/g, " ");

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

const DEGRADED_EN =
  "The rule cannot fire reliably: BB: Admin Hosts is disabled; " +
  "building block 78 has not been observed. Its silence is not evidence about the rule itself.";
const DEGRADED_TR =
  "Kural güvenilir biçimde tetiklenemez: BB: Admin Hosts devre dışı ve yapı taşı 78 " +
  "gözlenmedi. Kuralın sessizliği, kuralın kendisi hakkında kanıt değildir.";

function degraded(over: Partial<RuleSummary> = {}): RuleSummary {
  return rule({
    id: "r-degraded",
    name: "Suspicious auth pattern",
    health_status: "DEPENDENCY_DEGRADED",
    health_reason: DEGRADED_EN,
    health_reason_code: "rule_health.dependency_degraded",
    health_reason_params: {
      dependencies: {
        kind: "list",
        items: [
          {
            code: "rule_health.dependency.disabled",
            params: {
              label: { code: "rule_health.label.named", params: { name: "BB: Admin Hosts" } },
            },
          },
          {
            code: "rule_health.dependency.not_observed",
            params: {
              label: { code: "rule_health.label.building_block", params: { ref: "78" } },
            },
          },
        ],
      },
    },
    ...over,
  });
}

const NOISY_EN =
  "The rule fired 812.4 times a day over the last 30 days, above the expected 500.0.";
const NOISY_TR = `Kural günde 812,4 kez tetiklendi (son 30${NB}gün); bu, beklenen 500,0 değerinin üzerinde.`;

function noisy(over: Partial<RuleSummary> = {}): RuleSummary {
  return rule({
    id: "r-noisy",
    name: "Excessive firewall denies",
    health_status: "NOISY",
    health_reason: NOISY_EN,
    health_reason_code: "rule_health.noisy",
    health_reason_params: { per_day: 812.4, window_days: 30.0, threshold: 500.0 },
    ...over,
  });
}

const GRACE_EN = "The rule is 2.5 days old, inside the 7-day grace period.";
const GRACE_TR = `Kural 2,5${NB}gün yaşında; 7${NB}günlük değerlendirme muafiyeti içinde.`;

const UNMEASURED_EN =
  "The rule is enabled and its dependencies are healthy, but it has never been " +
  "observed firing in 45 days.";
const UNMEASURED_TR = `Kural etkin ve bağımlılıkları sağlıklı; ancak 45${NB}gündür tetiklendiği hiç gözlenmedi.`;

const INACTIVE_EN =
  "The rule last fired 20 days ago, beyond the 14-day inactivity window.";
const INACTIVE_TR =
  `Kuralın son tetiklenmesi 20${NB}gün önceydi; ` +
  `14${NB}günlük hareketsizlik penceresinin dışında.`;

const DETAIL_MANY_EN =
  "Measured rule-match coverage of the evaluation window is incomplete " +
  "(719 of 8640 windows are COMPLETE): 3 windows failed to collect " +
  "(latest error class: TimeoutError); 1 window was skipped beyond the catch-up horizon " +
  "and never searched. Affected windows: 2026-10-25 10:00 UTC to 2026-10-25 12:00 UTC. " +
  "The absence of measured matches is not evidence that the rule has never fired. " +
  "The evaluation window stops holding the last affected window at 2026-11-24 12:00 UTC.";
const DETAIL_MANY_TR =
  "Değerlendirme penceresinin ölçülen kural eşleşmesi coverage'ı eksiksiz değil " +
  `(719${NB}/${NB}8.640 pencere eksiksiz): 3 pencere toplanamadı (son hata sınıfı: ` +
  "TimeoutError) ve 1 pencere, yakalama ufkunun ötesinde bırakıldığı için hiç aranmadı. " +
  "Etkilenen pencereler: 2026-10-25 10:00 UTC ile 2026-10-25 12:00 UTC arasında. " +
  "Ölçülen eşleşmelerin yokluğu, kuralın hiç tetiklenmediğinin kanıtı değildir. " +
  "Değerlendirme penceresi, etkilenen son pencereyi 2026-11-24 12:00 UTC tarihine kadar " +
  "tutuyor.";

const DETAIL_ONE_EN =
  "Measured rule-match coverage of the evaluation window is incomplete " +
  "(23 of 24 windows are COMPLETE): 1 window failed to collect " +
  "(latest error class: ProviderError). Affected window: 2026-10-25 10:00 UTC. " +
  "The absence of measured matches is not evidence that the rule has never fired. " +
  "The evaluation window stops holding the last affected window at 2026-11-24 11:00 UTC.";

function detailRule(over: Partial<RuleDetail> = {}): RuleDetail {
  return {
    ...rule(),
    id: "11111111-1111-1111-1111-111111111111",
    name: "Multiple login failures from a single source",
    description: null,
    categories: [],
    average_capacity: 0.75,
    generates_offense: true,
    response_actions: [],
    qradar_created_at: null,
    qradar_modified_at: null,
    first_seen_at: "2026-09-01T00:00:00Z",
    soc_notes: null,
    expected_daily_firings: null,
    health_monitoring_enabled: true,
    dependencies: [],
    ...over,
  } as RuleDetail;
}

function snapshot(over: Partial<RuleHealthSnapshot> = {}): RuleHealthSnapshot {
  return {
    evaluated_at: "2026-10-15T09:00:00Z",
    status: "INSUFFICIENT_DATA",
    logic_version: 1,
    confidence: 0.2,
    reason: null,
    window_start: null,
    window_end: null,
    last_triggered_at: null,
    trigger_count: 0,
    offense_contribution_count: 0,
    enabled: true,
    building_blocks_healthy: null,
    required_log_sources_healthy: null,
    missing_dependencies: [],
    evidence: {},
    ...over,
  };
}

describe("rules list reasons in the reader's language", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([]);
  });

  it("renders the coded dependency sentence with both label kinds in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rules").mockResolvedValue({
      items: [degraded()],
      total: 1,
      limit: 25,
      offset: 0,
    });
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    const cell = screen.getByRole("link", { name: "Suspicious auth pattern" }).closest("td");
    expect(cell).toHaveTextContent(DEGRADED_TR);
    expect(cell).not.toHaveTextContent("cannot fire reliably");
  });

  it("formats the noisy numbers in the Turkish form", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rules").mockResolvedValue({
      items: [
        noisy(),
        rule({
          id: "r-grace",
          name: "Brand new rule",
          health_status: "INSUFFICIENT_DATA",
          health_reason: GRACE_EN,
          health_reason_code: "rule_health.grace",
          health_reason_params: { age_days: 2.5, grace_days: 7 },
        }),
        rule({
          id: "r-unmeasured",
          name: "Quiet since deployment",
          health_status: "NEVER_OBSERVED",
          health_reason: UNMEASURED_EN,
          health_reason_code: "rule_health.never_observed.unmeasured",
          health_reason_params: { age_days: 45.0 },
        }),
        rule({
          id: "r-inactive",
          name: "Retired detection",
          health_status: "INACTIVE",
          health_reason: INACTIVE_EN,
          health_reason_code: "rule_health.inactive",
          health_reason_params: { quiet_days: 20.0, inactivity_days: 14 },
        }),
      ],
      total: 4,
      limit: 25,
      offset: 0,
    });
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText(flat(NOISY_TR))).toBeInTheDocument();
    expect(screen.getByText(flat(GRACE_TR))).toBeInTheDocument();
    expect(screen.getByText(flat(UNMEASURED_TR))).toBeInTheDocument();
    expect(screen.getByText(flat(INACTIVE_TR))).toBeInTheDocument();
  });

  it("keeps an uncoded reason as the API's English in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rules").mockResolvedValue({
      items: [degraded({ health_reason_code: null, health_reason_params: null })],
      total: 1,
      limit: 25,
      offset: 0,
    });
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    const cell = screen.getByRole("link", { name: "Suspicious auth pattern" }).closest("td");
    expect(cell).toHaveTextContent(DEGRADED_EN);
  });

  it("falls back to the API text for a code it does not know", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rules").mockResolvedValue({
      items: [noisy({ health_reason_code: "rule_health.noisy.v2" })],
      total: 1,
      limit: 25,
      offset: 0,
    });
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText(NOISY_EN)).toBeInTheDocument();
  });

  it("keeps the no-reason fallback", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rules").mockResolvedValue({
      items: [
        rule({
          id: "r-none",
          name: "Unevaluated rule",
          health_status: "INSUFFICIENT_DATA",
          health_reason: null,
        }),
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("Kaydedilmiş neden yok")).toBeInTheDocument();
  });

  it("renders the API text verbatim in English even when a code is present", async () => {
    setTestLocale("en");
    vi.spyOn(api, "rules").mockResolvedValue({
      items: [noisy()],
      total: 1,
      limit: 25,
      offset: 0,
    });
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText(NOISY_EN)).toBeInTheDocument();
  });
});

describe("rule detail reason in the reader's language", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([]);
    vi.spyOn(ruleStatisticsApi, "capacityHistory").mockResolvedValue([]);
  });

  it("renders the full incomplete-coverage sentence in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rule").mockResolvedValue(
      detailRule({
        health_status: "INSUFFICIENT_DATA",
        health_reason: DETAIL_MANY_EN,
        health_reason_code: "rule_health.incomplete.detail_many",
        health_reason_params: {
          complete: 719,
          expected: 8640,
          causes: {
            kind: "list",
            items: [
              {
                code: "rule_health.incomplete.cause.failed_many_class",
                params: { count: 3, error_class: "TimeoutError" },
              },
              { code: "rule_health.incomplete.cause.skipped_one", params: {} },
            ],
          },
          first_start: "2026-10-25T10:00:00+00:00",
          last_start: "2026-10-25T12:00:00+00:00",
          until: "2026-11-24T12:00:00+00:00",
        },
      }),
    );
    render(await RuleDetailPage({ params: Promise.resolve({ id: "r-1" }) }));
    expect(screen.getByText(flat(DETAIL_MANY_TR))).toBeInTheDocument();
  });

  it("renders the singular shape with one timestamp in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rule").mockResolvedValue(
      detailRule({
        health_status: "INSUFFICIENT_DATA",
        health_reason: DETAIL_ONE_EN,
        health_reason_code: "rule_health.incomplete.detail_one",
        health_reason_params: {
          complete: 23,
          expected: 24,
          causes: {
            kind: "list",
            items: [
              {
                code: "rule_health.incomplete.cause.failed_one_class",
                params: { error_class: "ProviderError" },
              },
            ],
          },
          first_start: "2026-10-25T10:00:00+00:00",
          until: "2026-11-24T11:00:00+00:00",
        },
      }),
    );
    const { container } = render(await RuleDetailPage({ params: Promise.resolve({ id: "r-1" }) }));
    const reason = container.querySelector(".rule-health-reason");
    expect(reason).toHaveTextContent(
      flat(`(23${NB}/${NB}24 pencere eksiksiz): 1 pencere toplanamadı`),
    );
    expect(reason).toHaveTextContent("Etkilenen pencere: 2026-10-25 10:00 UTC.");
  });

  it("renders the history rows' coded reasons and keeps old rows English", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rule").mockResolvedValue(detailRule({ ...degraded() } as Partial<RuleDetail>));
    vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([
      snapshot({
        evaluated_at: "2026-10-15T09:00:00Z",
        reason: NOISY_EN,
        reason_code: "rule_health.noisy",
        reason_params: { per_day: 812.4, window_days: 30.0, threshold: 500.0 },
      }),
      snapshot({
        evaluated_at: "2026-10-14T09:00:00Z",
        status: "NOISY",
        // A snapshot written before 0036: no code, the stored English stays.
        reason: NOISY_EN,
      }),
    ]);
    const { container } = render(await RuleDetailPage({ params: Promise.resolve({ id: "r-1" }) }));
    const cells = [...container.querySelectorAll("td.cell-reason")];
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveTextContent(flat(NOISY_TR));
    expect(cells[1]).toHaveTextContent(NOISY_EN);
  });

  it("keeps the verdict's own reason, not the newest history row's", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "rule").mockResolvedValue(detailRule({ ...degraded() } as Partial<RuleDetail>));
    vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([
      snapshot({
        evaluated_at: "2026-10-15T09:00:00Z",
        reason: NOISY_EN,
        reason_code: "rule_health.noisy",
        reason_params: { per_day: 812.4, window_days: 30.0, threshold: 500.0 },
      }),
    ]);
    const { container } = render(await RuleDetailPage({ params: Promise.resolve({ id: "r-1" }) }));
    const notice = container.querySelector(".rule-health-reason");
    expect(notice).toHaveTextContent(DEGRADED_TR);
  });
});
