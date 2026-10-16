import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CustomRuleDetailPage from "./page";
import { breach, catalog, preview, reader, rule, writer } from "@/components/customrules/fixtures";
import { resolveRuleSubjects } from "@/components/customrules/subjects";
import { ApiError, api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/components/customrules/subjects", () => ({ resolveRuleSubjects: vi.fn() }));
vi.mock("@/components/customrules/StoredRulePreview", () => ({
  StoredRulePreview: ({ subjectLabel }: { subjectLabel: string }) => <div aria-label="stored preview">Preview for {subjectLabel}</div>,
}));
vi.mock("@/components/customrules/CustomRuleEditor", () => ({
  CustomRuleEditor: () => <form aria-label="custom rule editor" />,
}));

async function renderPage(id = "rule-1") {
  return render(await CustomRuleDetailPage({ params: Promise.resolve({ id }) }));
}

beforeEach(() => {
  vi.spyOn(api, "customRule").mockResolvedValue(rule());
  vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog());
  vi.spyOn(api, "customRulePreview").mockResolvedValue(preview());
  vi.spyOn(api, "customRuleBreaches").mockResolvedValue([breach()]);
  vi.spyOn(api, "caller").mockResolvedValue(writer);
  vi.mocked(resolveRuleSubjects).mockResolvedValue(
    new Map([["rule-1", { label: "edge-web-01", href: "/behavior/sources/source-1", retiredAt: null }]]),
  );
});

describe("custom-rule detail states", () => {
  it("renders read forbidden as a named permission state", async () => {
    vi.spyOn(api, "customRule").mockRejectedValue(new ApiError(403, "Forbidden"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:custom_rules");
  });

  it("says what failed without exposing the backend response", async () => {
    vi.spyOn(api, "customRule").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Reload the page");
    expect(screen.queryByText("secret")).toBeNull();
  });

  it("hides write actions and editor from a reader and names granting roles", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    await renderPage();
    expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();
    expect(screen.queryByLabelText("custom rule editor")).toBeNull();
    expect(screen.getByText("custom_rule:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
  });

  it("states subject retirement and its UTC time", async () => {
    vi.spyOn(api, "customRule").mockResolvedValue(rule({ last_status: "SUBJECT_RETIRED", last_status_reason: "Endpoint retired." }));
    vi.mocked(resolveRuleSubjects).mockResolvedValue(
      new Map([["rule-1", { label: "GET /retired", href: "/behavior/waf/sources/source-1", retiredAt: "2026-10-15T06:00:00Z" }]]),
    );
    await renderPage();
    expect(screen.getByText("Subject retired")).toBeInTheDocument();
    expect(screen.queryByText("SUBJECT_RETIRED")).toBeNull();
    expect(screen.getByText(/stopped being tracked/)).toHaveTextContent(
      "This subject stopped being tracked at 2026-10-15 06:00:00 UTC. The rule draws no verdict from later buckets.",
    );
  });
});

describe("custom-rule detail evidence", () => {
  it("answers whether the rule is firing and what it read in shadow mode", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { name: "Sustained edge traffic", level: 1 })).toBeInTheDocument();
    const state = screen.getByRole("heading", { name: "Current state" }).parentElement!;
    expect(within(state).getByText("Open")).toHaveClass("crit");
    expect(within(state).queryByText("Two consecutive buckets exceeded the threshold.")).toBeNull();
    expect(state.querySelector(".cr-status")?.querySelectorAll(":scope > span")).toHaveLength(1);
    expect(screen.getByLabelText("stored preview")).toHaveTextContent("edge-web-01");
    const active = screen.getByRole("heading", { name: "Active breach" }).parentElement!;
    expect(within(active).getByText("Open")).toHaveClass("crit");
    expect(within(active).queryByText("OPEN")).toBeNull();
    expect(within(active).getByText("at least 19 events/s")).toBeInTheDocument();
    expect(within(active).getByText("14 events/s")).toBeInTheDocument();
    expect(within(active).getByText("2 buckets")).toBeInTheDocument();
    expect(within(active).getByText(/Nothing paged/)).toBeInTheDocument();
  });

  it("links an alerting-mode breach to Alerts", async () => {
    vi.spyOn(api, "customRule").mockResolvedValue(rule({ alerting_mode: "alerting" }));
    await renderPage();
    expect(screen.getByRole("link", { name: "Alerts" })).toHaveAttribute("href", "/alerts");
  });

  it("renders breach history with measurements, units and UTC times", async () => {
    await renderPage();
    const table = screen.getByRole("table", { name: "Breach history" });
    expect(within(table).getByText("Open")).toBeInTheDocument();
    expect(within(table).queryByText("OPEN")).toBeNull();
    expect(within(table).getByRole("columnheader", { name: "Worst (events/s)" })).toBeInTheDocument();
    expect(within(table).getByText("at least 19 events/s")).toBeInTheDocument();
    expect(within(table).getByText("2026-10-16 07:05:00 UTC")).toBeInTheDocument();
  });

  it("offers edit, enable or disable, and delete only to a writer", async () => {
    await renderPage();
    expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(screen.getByText("Edit rule")).toBeInTheDocument();
  });
});
