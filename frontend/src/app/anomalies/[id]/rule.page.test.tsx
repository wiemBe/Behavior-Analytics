import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { incident, ruleDetail, RULE_ID, section } from "@/components/rules/ruleMatchFixtures";
import { ApiError, api, ruleMatchApi, type Triage } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "anomalyPivot").mockRejectedValue(new ApiError(404, "no pivot"));
  vi.spyOn(api, "sourceMetrics");
});

async function renderPage() {
  return render(await AnomalyDetailPage({ params: Promise.resolve({ id: "x" }) }));
}

describe("a rule-match incident", () => {
  it("opens the rule investigation, not the Phase A one, and never asks for a source's metrics", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section({ incidents: [incident()] }));
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("Multiple login failures from a single source");
    expect(screen.getByText("QRadar rule 100123")).toBeInTheDocument();
    expect(screen.getByText("Rule storm")).toBeInTheDocument();
    expect(api.sourceMetrics).not.toHaveBeenCalled();
    expect(api.anomalyPivot).not.toHaveBeenCalled();
  });

  it("states observed, expected and the incident's own band with their unit, and the ratio with ×", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section({ incidents: [incident()] }));
    await renderPage();
    const measures = screen.getByRole("region", { name: "Evidence of this rule-match anomaly" });
    expect(measures).toHaveTextContent("Observed4,800matches / window");
    expect(measures).toHaveTextContent("Expected310.5matches / window");
    expect(measures).toHaveTextContent("Expected range120 to 640matches / window");
    expect(measures).toHaveTextContent("Ratio to expected15.46 ×");
    expect(screen.getByText(/4,800 against 310.5 expected per window/)).toBeInTheDocument();
  });

  it("reads a silence as a streak in windows", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      ruleDetail({ anomaly_type: "RULE_SILENCE", observed_value: 0, expected_value: 12 }),
    );
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(
      section({ incidents: [incident({ detector: "RULE_SILENCE", observed: 0, expected: 12, consecutive_buckets: 6, ratio: null })] }),
    );
    await renderPage();
    const measures = screen.getByRole("region", { name: "Evidence of this rule-match anomaly" });
    expect(measures).toHaveTextContent("Silence streak6windows");
    expect(measures).toHaveTextContent("Observed0matches / window");
  });

  it("says the band is not loaded when the rule's section failed, and never invents one", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(ruleMatchApi, "section").mockRejectedValue(new ApiError(500, "down"));
    await renderPage();
    const measures = screen.getByRole("region", { name: "Evidence of this rule-match anomaly" });
    expect(measures).toHaveTextContent("Expected range—");
    expect(measures).toHaveTextContent("not loaded");
    expect(measures).not.toHaveTextContent(/\d to \d/);
  });

  it("says the band is not stored when the incident is no longer listed in the section", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section({ incidents: [] }));
    await renderPage();
    expect(screen.getByText("not stored for this incident")).toBeInTheDocument();
  });

  it("draws a missing observed count as the dash, never 0, and a lower bound as at least", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail({ observed_value: null, expected_value: null }));
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section());
    await renderPage();
    const measures = screen.getByRole("region", { name: "Evidence of this rule-match anomaly" });
    expect(measures).toHaveTextContent("Observed—");
    expect(measures).toHaveTextContent("Expected—");
  });

  it("marks a truncated observed window as a lower bound", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail({ observed_measurement: "LOWER_BOUND" }));
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section());
    await renderPage();
    expect(screen.getByText(/lower bound/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Evidence of this rule-match anomaly" })).toHaveTextContent("≥ 4,800");
  });

  it("shows the lifecycle in UTC and links back to the rule", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section());
    await renderPage();
    expect(screen.getByRole("table", { name: "Evidence" })).toHaveTextContent("2026-11-09 08:00:00 UTC");
    expect(screen.getByRole("table", { name: "Evidence" })).toHaveTextContent("not ended");
    expect(screen.getByRole("link", { name: "Open the rule and its match-anomaly section" })).toHaveAttribute(
      "href",
      `/rules/${RULE_ID}`,
    );
  });
});

function ruleTriage(over: Partial<Triage> = {}): Triage {
  return {
    family: "rule",
    anomaly_id: "x",
    status: "NEW",
    owner: null,
    disposition: null,
    disposition_note: null,
    updated_by: null,
    updated_at: null,
    version: 0,
    events: { items: [], total: 0, limit: 50, offset: 0 },
    ...over,
  };
}

describe("triage of a rule-match incident", () => {
  beforeEach(() => {
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "a.rivera",
      permissions: ["admin:*"],
      effective_permissions: ["read:anomalies", "anomaly:triage"],
    } as unknown as Awaited<ReturnType<typeof api.caller>>);
    vi.spyOn(api, "alerts").mockResolvedValue({ items: [], total: 0, limit: 500, offset: 0 });
    vi.spyOn(ruleMatchApi, "section").mockResolvedValue(section({ incidents: [incident()] }));
  });

  it("renders the triage block, read for the rule family", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    const read = vi.spyOn(api, "triage").mockResolvedValue(ruleTriage());
    await renderPage();
    expect(read).toHaveBeenCalledWith("rule", ruleDetail().id, expect.anything());
    expect(screen.getByRole("button", { name: "Take it" })).toBeInTheDocument();
  });

  it("sends the change to /anomalies/triage/rule/{id}", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(api, "triage").mockResolvedValue(ruleTriage());
    const sent = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(ruleTriage({ status: "IN_PROGRESS", owner: "a.rivera", version: 1 })), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("button", { name: "Take it" }));
    await waitFor(() => expect(sent).toHaveBeenCalled());
    const [url, init] = sent.mock.calls[0];
    expect(String(url)).toContain(`/anomalies/triage/rule/${ruleDetail().id}`);
    expect((init as RequestInit).method).toBe("PATCH");
  });

  it("offers no quiet period: the alert policy has no rule family", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ruleDetail());
    vi.spyOn(api, "triage").mockResolvedValue(ruleTriage());
    await renderPage();
    expect(screen.queryByText(/quiet period/i)).not.toBeInTheDocument();
  });
});
