import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuleDetailPage from "./page";
import Loading from "./loading";
import { incident, section } from "@/components/rules/ruleMatchFixtures";
import { ApiError, api, ruleMatchApi, ruleStatisticsApi, type RuleDetail } from "@/lib/api";

function rule(): RuleDetail {
  return {
    id: "11111111-1111-1111-1111-111111111111",
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

const ID = "11111111-1111-1111-1111-111111111111";

async function renderPage() {
  return render(await RuleDetailPage({ params: Promise.resolve({ id: ID }) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "rule").mockResolvedValue(rule());
  vi.spyOn(api, "ruleHealthHistory").mockResolvedValue([]);
  vi.spyOn(ruleStatisticsApi, "capacityHistory").mockResolvedValue([]);
});

describe("the match-anomaly section on /rules/[id]", () => {
  it("sits below the dependency lists and above Metadata, and reads the rule's own id", async () => {
    const read = vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section());
    await renderPage();
    expect(read).toHaveBeenCalledWith(ID);
    const heads = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(heads.indexOf("Match anomalies")).toBe(heads.indexOf("Metadata") - 1);
    // The dependency lists belong to the health card: the section must not split them off it.
    const deps = screen.getByRole("heading", { name: /^Telemetry requirements/ });
    const sectionHead = screen.getByRole("heading", { name: "Match anomalies" });
    expect(deps.compareDocumentPosition(sectionHead) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Health evidence" }).compareDocumentPosition(deps) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("its error says so in the section and leaves the health card and verdict as they are", async () => {
    vi.spyOn(ruleMatchApi, "section").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    const sectionEl = screen.getByRole("heading", { name: "Match anomalies" }).closest("section")!;
    expect(within(sectionEl).getByRole("alert")).toHaveTextContent("Match anomalies could not be loaded.");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: rule().name, level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getByText("HEALTHY")).toBeInTheDocument();
    expect(sectionEl).not.toHaveTextContent("secret");
  });

  it("does not relabel the health verdict when the match state is not judged", async () => {
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(
      section({ state: "MONITORING_OFF", reason: "Match anomalies are off for this rule (health_monitoring_enabled). The health status does not read that flag." }),
    );
    await renderPage();
    expect(screen.getByText("HEALTHY")).toBeInTheDocument();
    expect(screen.getByText(/health_monitoring_enabled/)).toBeInTheDocument();
  });

  it("shows an incident in the section with its link", async () => {
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section({ incidents: [incident()] }));
    await renderPage();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/99999999-0000-4000-8000-000000000001",
    );
  });

  it("does not ask for the section when the rule itself is missing", async () => {
    vi.spyOn(api, "rule").mockRejectedValue(new ApiError(404, "gone"));
    const read = vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section());
    await renderPage();
    expect(screen.queryByRole("heading", { name: "Match anomalies" })).toBeNull();
    expect(read.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("is loaded with the page, so the route's own loading boundary is its loading state", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });
});
