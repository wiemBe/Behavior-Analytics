import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CustomRulesPage from "./page";
import { CUSTOM_RULE_LATENCY } from "@/components/customrules/authoring";
import { catalog, reader, rule, writer } from "@/components/customrules/fixtures";
import { resolveRuleSubjects } from "@/components/customrules/subjects";
import { ApiError, api } from "@/lib/api";

vi.mock("@/components/customrules/subjects", () => ({ resolveRuleSubjects: vi.fn() }));

beforeEach(() => {
  vi.mocked(resolveRuleSubjects).mockResolvedValue(new Map());
  vi.spyOn(api, "caller").mockResolvedValue(reader);
  vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog());
});

describe("custom-rule list states", () => {
  it("renders a read-permission forbidden state instead of an error", async () => {
    vi.spyOn(api, "customRules").mockRejectedValue(new ApiError(403, "Forbidden", "read:custom_rules"));
    render(await CustomRulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("read:custom_rules");
    expect(screen.getByText(/forbidden for this account/)).toBeInTheDocument();
  });

  it("says what failed and what the analyst can do", async () => {
    vi.spyOn(api, "customRules").mockRejectedValue(new ApiError(500, "secret"));
    render(await CustomRulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("backend is reachable");
    expect(screen.queryByText("secret")).toBeNull();
  });

  it("explains the empty state to a reader and names the granting roles", async () => {
    vi.spyOn(api, "customRules").mockResolvedValue([]);
    render(await CustomRulesPage());
    expect(screen.getByText("No custom rules exist yet.")).toBeInTheDocument();
    expect(screen.getByText("custom_rule:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
  });

  it("offers the empty-state creation path only to a writer", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    vi.spyOn(api, "customRules").mockResolvedValue([]);
    render(await CustomRulesPage());
    expect(screen.getByRole("link", { name: "New rule" })).toHaveAttribute("href", "/custom-rules/new");
    expect(screen.getByRole("link", { name: "Create the first rule" })).toHaveAttribute("href", "/custom-rules/new");
  });
});

describe("custom-rule list honesty", () => {
  it("names evaluation-off and shadow-mode settings and states latency once", async () => {
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog({ evaluation_enabled: false, alerting_mode: "shadow" }));
    vi.spyOn(api, "customRules").mockResolvedValue([rule()]);
    render(await CustomRulesPage());
    expect(screen.getByText("CUSTOM_RULES_ENABLED").tagName).toBe("CODE");
    expect(screen.getByText("CUSTOM_RULES_ALERTING_ENABLED").tagName).toBe("CODE");
    expect(screen.getAllByText(CUSTOM_RULE_LATENCY)).toHaveLength(1);
  });

  it("renders every evaluator status with its reason, including never evaluated", async () => {
    const statuses: Array<string | null> = [
      "OK",
      "OPEN",
      "RECOVERING",
      "NO_VERDICT",
      "STALE",
      "NOT_COLLECTED",
      "SUBJECT_RETIRED",
      "DISABLED",
      "NOT_ENABLED",
      null,
    ];
    const rows = statuses.map((status, index) =>
      rule({
        id: `rule-${index}`,
        name: `Rule ${index}`,
        last_status: status,
        last_status_reason: status === null ? null : `Reason for ${status}`,
        last_evaluated_at: status === null ? null : "2026-10-16T07:20:00Z",
      }),
    );
    vi.spyOn(api, "customRules").mockResolvedValue(rows);
    vi.mocked(resolveRuleSubjects).mockResolvedValue(
      new Map(rows.map((row) => [row.id, { label: "edge-web-01", href: "/behavior/sources/source-1", retiredAt: null }])),
    );
    render(await CustomRulesPage());
    const table = screen.getByRole("table", { name: "Custom rules" });
    for (const status of statuses.filter((value): value is string => value !== null)) {
      expect(within(table).getByText(status)).toBeInTheDocument();
      expect(within(table).getByText(`Reason for ${status}`)).toBeInTheDocument();
    }
    expect(within(table).getByText("NEVER_EVALUATED")).toBeInTheDocument();
    expect(within(table).getByText("No bucket has been judged yet.")).toBeInTheDocument();
    expect(within(table).getByText("Never evaluated")).toBeInTheDocument();
  });

  it("shows subject kind and label, condition unit, severity and UTC time", async () => {
    const row = rule();
    vi.spyOn(api, "customRules").mockResolvedValue([row]);
    vi.mocked(resolveRuleSubjects).mockResolvedValue(
      new Map([[row.id, { label: "edge-web-01", href: "/behavior/sources/source-1", retiredAt: null }]]),
    );
    render(await CustomRulesPage());
    const table = screen.getByRole("table", { name: "Custom rules" });
    expect(within(table).getByText("Log source")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "edge-web-01" })).toHaveAttribute("href", "/behavior/sources/source-1");
    expect(within(table).getByText("average EPS > 10 events/s for 2 buckets")).toBeInTheDocument();
    expect(within(table).getByText("2026-10-16 07:20:00 UTC")).toBeInTheDocument();
    expect(within(table).getByText("HIGH")).toHaveClass("crit");
  });
});
