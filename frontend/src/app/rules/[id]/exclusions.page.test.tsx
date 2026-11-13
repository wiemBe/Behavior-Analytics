// SOC-13f: the rule page lists the rule's training exclusions (storm and silence together), with
// the same states as the source page, and a rule's rows read "Used from the next evaluation".

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuleDetailPage from "./page";
import { StaticSession } from "@/components/auth/session";
import { CAN, READ_ONLY, exclusion } from "@/components/baseline/fixtures";
import { section } from "@/components/rules/ruleMatchFixtures";
import { ApiError, api, ruleMatchApi, ruleStatisticsApi, trainingExclusionApi, type RuleDetail } from "@/lib/api";

const ID = "11111111-1111-1111-1111-111111111111";

function rule(): RuleDetail {
  return {
    id: ID,
    qradar_id: 77,
    name: "Multiple login failures from a single source",
    enabled: true,
    is_building_block: false,
    rule_type: "EVENT",
    origin: "USER",
    owner: "SOC",
    health_status: "HEALTHY",
    health_evaluated_at: "2026-10-13T09:00:00Z",
    health_reason: null,
    last_fired_at: "2026-10-13T08:45:00Z",
    offense_contribution_count: 4,
    event_contribution_count: 120,
    mitre_techniques: ["T1110"],
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
  };
}

let forSubject: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "rule").mockResolvedValue(rule());
  vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([]);
  vi.spyOn(ruleStatisticsApi, "capacityHistory").mockResolvedValue([]);
  vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section());
  forSubject = vi.spyOn(trainingExclusionApi, "forSubject").mockResolvedValue({ items: [], limit: 100 });
});

async function renderPage(value = CAN) {
  return render(
    <StaticSession value={value}>{await RuleDetailPage({ params: Promise.resolve({ id: ID }) })}</StaticSession>,
  );
}

describe("training exclusions on /rules/[id]", () => {
  it("reads this rule's exclusions in both scopes (no scope filter)", async () => {
    await renderPage();
    expect(forSubject).toHaveBeenCalledWith("rule", ID, undefined);
    expect(screen.getByRole("heading", { name: "Baseline training exclusions" })).toBeInTheDocument();
    expect(screen.getByText("No analyst training exclusions affect this baseline.")).toBeInTheDocument();
  });

  it("lists storm and silence rows by name, 'Used from the next evaluation', with Revoke", async () => {
    forSubject.mockResolvedValue({
      items: [
        exclusion({ id: "a", subject_kind: "rule", scope_key: "rule:storm" }),
        exclusion({ id: "b", subject_kind: "rule", scope_key: "rule:silence" }),
      ],
      limit: 100,
    });
    await renderPage();
    const table = screen.getByRole("table", { name: "Analyst training exclusions" });
    expect(table).toHaveTextContent("Rule storm");
    expect(table).toHaveTextContent("Rule silence");
    expect(table).toHaveTextContent("Used from the next evaluation");
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(2);
  });

  it("a read-only caller sees the rows without Revoke", async () => {
    forSubject.mockResolvedValue({
      items: [exclusion({ subject_kind: "rule", scope_key: "rule:storm" })],
      limit: 100,
    });
    await renderPage(READ_ONLY);
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
  });

  it("error is its own state with Retry, and the rest of the page stays", async () => {
    forSubject.mockRejectedValue(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.getByText("Training exclusions could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: rule().name, level: 1 })).toBeInTheDocument();
  });

  it("forbidden names read:anomalies", async () => {
    forSubject.mockRejectedValue(new ApiError(403, "forbidden"));
    await renderPage();
    expect(screen.getByText("Reading training exclusions needs the read:anomalies permission.")).toBeInTheDocument();
  });
});
