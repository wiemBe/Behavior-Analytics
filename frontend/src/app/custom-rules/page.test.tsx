import { cleanup, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CustomRulesPage from "./page";
import { customRuleLatency } from "@/components/customrules/authoring";
import { catalog, reader, rule, writer } from "@/components/customrules/fixtures";
import { resolveRuleSubjects } from "@/components/customrules/subjects";
import { partsText } from "@/components/cadence/CadenceLatency";
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

describe("custom-rule list latency figure", () => {
  const kinds = [
    { subject_kind: "LOG_SOURCE" as const, column: "log_source_id", search_family: "PHASE_A_VOLUME" as const },
    { subject_kind: "PW_VALUE" as const, column: "pw_value_id", search_family: "PROPERTY_WATCH" as const },
  ];
  const timing = (family: "PHASE_A_VOLUME" | "PROPERTY_WATCH", cadence: number) => ({
    family, cadence_seconds: cadence, bucket_seconds: 300, settle_seconds: 120,
  });
  const sentence = () =>
    screen.getAllByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").startsWith("Rules are judged"));

  it("names each subject kind's figure from its family's cadence", async () => {
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(
      catalog({ subject_kinds: kinds, cadences: [timing("PHASE_A_VOLUME", 900), timing("PROPERTY_WATCH", 3600)] }),
    );
    vi.spyOn(api, "customRules").mockResolvedValue([rule()]);
    render(await CustomRulesPage());
    expect(sentence()).toHaveLength(1);
    expect(sentence()[0]).toHaveTextContent("Log source: up to 22 min; Property watch value: up to 1 h 7 min");
  });

  it("names no number when a kind's family is missing from the cadences", async () => {
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(
      catalog({ subject_kinds: kinds, cadences: [timing("PHASE_A_VOLUME", 900)] }),
    );
    vi.spyOn(api, "customRules").mockResolvedValue([rule()]);
    render(await CustomRulesPage());
    expect(sentence()[0]).toHaveTextContent("after each collection of their family");
    expect(sentence()[0].textContent).not.toMatch(/up to \d/);
  });

  it("links Search cadence only for read:search_cadence", async () => {
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(
      catalog({ subject_kinds: kinds, cadences: [timing("PHASE_A_VOLUME", 900), timing("PROPERTY_WATCH", 900)] }),
    );
    vi.spyOn(api, "customRules").mockResolvedValue([rule()]);
    vi.spyOn(api, "caller").mockResolvedValue({ ...reader, permissions: [] });
    render(await CustomRulesPage());
    expect(screen.queryByRole("link", { name: "Search cadence" })).toBeNull();
    cleanup();
    vi.spyOn(api, "caller").mockResolvedValue({ ...reader, permissions: [], effective_permissions: [...reader.effective_permissions, "read:search_cadence"] });
    render(await CustomRulesPage());
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute("href", "/admin/search-cadence");
    expect(sentence()[0]).toHaveTextContent("collected 5-minute buckets, up to 22 min after the events arrive");
  });
});

describe("custom-rule list honesty", () => {
  it("names evaluation-off and shadow-mode settings and states latency once", async () => {
    vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog({ evaluation_enabled: false, alerting_mode: "shadow" }));
    vi.spyOn(api, "customRules").mockResolvedValue([rule()]);
    render(await CustomRulesPage());
    expect(screen.getByText("CUSTOM_RULES_ENABLED").tagName).toBe("CODE");
    expect(screen.getByText("CUSTOM_RULES_ALERTING_ENABLED").tagName).toBe("CODE");
    expect(screen.getAllByText((_, el) => el?.tagName === "P" && el.textContent === partsText(customRuleLatency()))).toHaveLength(1);
  });

  it("renders every evaluator status in words, with its recorded reason", async () => {
    const cases: Array<{ status: string | null; label: string; tone: string }> = [
      { status: "OK", label: "OK", tone: "ok" },
      { status: "OPEN", label: "Open", tone: "crit" },
      { status: "RECOVERING", label: "Recovering", tone: "warn" },
      { status: "NO_VERDICT", label: "No verdict", tone: "" },
      { status: "STALE", label: "Stale", tone: "warn" },
      { status: "NOT_COLLECTED", label: "Not collected", tone: "" },
      { status: "SUBJECT_RETIRED", label: "Subject retired", tone: "" },
      { status: "DISABLED", label: "Disabled", tone: "" },
      { status: "NOT_ENABLED", label: "Not enabled", tone: "" },
      { status: null, label: "Never evaluated", tone: "" },
    ];
    const rows = cases.map((item, index) =>
      rule({
        id: `rule-${index}`,
        name: `Rule ${index}`,
        last_status: item.status,
        last_status_reason: item.status === null ? null : `Reason for ${item.status}`,
        last_evaluated_at: item.status === null ? null : "2026-10-16T07:20:00Z",
      }),
    );
    vi.spyOn(api, "customRules").mockResolvedValue(rows);
    vi.mocked(resolveRuleSubjects).mockResolvedValue(
      new Map(rows.map((row) => [row.id, { label: "edge-web-01", href: "/behavior/sources/source-1", retiredAt: null }])),
    );
    render(await CustomRulesPage());
    const table = screen.getByRole("table", { name: "Custom rules" });
    for (const item of cases) {
      const pill = within(table).getByText(item.label, { selector: ".cr-status .pill" });
      expect(pill.textContent).not.toMatch(/_/);
      if (item.tone === "ok") expect(pill).toHaveClass("ok");
      else expect(pill.className.split(/\s+/).filter(Boolean)).not.toContain("ok");
      if (item.tone && item.tone !== "ok") expect(pill).toHaveClass(item.tone);
    }
    for (const status of cases.map((item) => item.status).filter((value): value is string => value !== null)) {
      expect(within(table).getByText(`Reason for ${status}`)).toBeInTheDocument();
    }
    expect(within(table).queryByText("No bucket has been judged yet.")).toBeNull();
    expect(within(table).getAllByText("Never evaluated").length).toBeGreaterThanOrEqual(2);
  });

  it("omits the reason line when the engine recorded none", async () => {
    const rows = [
      rule({ id: "open", name: "Open rule", last_status: "OPEN", last_status_reason: null }),
      rule({ id: "ok", name: "Ok rule", last_status: "OK", last_status_reason: null }),
      rule({ id: "recovering", name: "Recovering rule", last_status: "RECOVERING", last_status_reason: null }),
      rule({ id: "fresh", name: "Fresh rule", last_status: null, last_status_reason: null, last_evaluated_at: null }),
    ];
    vi.spyOn(api, "customRules").mockResolvedValue(rows);
    vi.mocked(resolveRuleSubjects).mockResolvedValue(new Map());
    render(await CustomRulesPage());
    const table = screen.getByRole("table", { name: "Custom rules" });
    for (const name of ["Open rule", "Ok rule", "Recovering rule", "Fresh rule"]) {
      const row = within(table).getByRole("row", { name: new RegExp(name) });
      const status = row.querySelector(".cr-status");
      expect(status?.querySelectorAll(":scope > span")).toHaveLength(1);
    }
  });

  it("puts status immediately after the name", async () => {
    vi.spyOn(api, "customRules").mockResolvedValue([rule()]);
    render(await CustomRulesPage());
    const table = screen.getByRole("table", { name: "Custom rules" });
    expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Name",
      "Status and reason",
      "Subject",
      "Condition",
      "Severity",
      "Enabled",
      "Last evaluated (UTC)",
    ]);
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
