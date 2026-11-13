// SOC-13f: the exclusion list on a family's own page. Same states and Revoke as the source page,
// the words of that family's baseline, and a rule's effect read as "from the next evaluation".

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { trainingExclusionApi } from "@/lib/api";
import type { Variant } from "@/lib/trainingExclusion";
import type { ExclusionLoad, ExclusionSubject } from "@/lib/trainingExclusionLoad";

import { TrainingExclusions } from "./TrainingExclusions";
import { CAN, READ_ONLY, exclusion } from "./fixtures";

function mount(
  variant: Variant,
  subject: ExclusionSubject,
  initial: ExclusionLoad,
  value: SessionValue = CAN,
) {
  return render(
    <StaticSession value={value}>
      <TrainingExclusions subject={subject} variant={variant} initial={initial} />
    </StaticSession>,
  );
}

const WAF: ExclusionSubject = { kind: "endpoint", id: "ep-1", scope: "waf:endpoint" };
const RULE: ExclusionSubject = { kind: "rule", id: "rule-1" };

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the lead names the baseline", () => {
  it.each([
    ["waf", WAF, /this endpoint's request baseline \(request count and every ratio\)/],
    ["ipsEntity", { kind: "entity", id: "e", scope: "ips:entity" }, /this entity's IPS event baseline/],
    ["ipsSource", { kind: "source", id: "s", scope: "ips:source" }, /top-source share, block ratio and port fan-out/],
    ["pwValue", { kind: "value", id: "v", scope: "pw:value" }, /spike, drop and disappeared/],
    ["ruleStorm", RULE, /this rule's storm and silence baselines/],
  ] as const)("%s", (variant, subject, lead) => {
    mount(variant, subject, { kind: "ok", items: [] });
    expect(screen.getByText(lead)).toBeInTheDocument();
    expect(screen.queryByText(/source's volume baseline/)).toBeNull();
  });
});

describe("states", () => {
  it("empty: says what is empty", () => {
    mount("waf", WAF, { kind: "ok", items: [] });
    expect(screen.getByText("No analyst training exclusions affect this baseline.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("error: Retry reads this subject, not a log source", async () => {
    const forSubject = vi
      .spyOn(trainingExclusionApi, "forSubject")
      .mockResolvedValue({ items: [exclusion({ subject_kind: "endpoint", scope_key: "waf:endpoint" })], limit: 100 });
    const forSource = vi.spyOn(trainingExclusionApi, "forSource");
    mount("waf", WAF, { kind: "error" });
    expect(screen.getByText("Training exclusions could not be loaded.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(forSubject).toHaveBeenCalledWith("endpoint", "ep-1", "waf:endpoint");
    expect(forSource).not.toHaveBeenCalled();
    expect(await screen.findByRole("table")).toHaveTextContent("Endpoint requests and ratios");
  });

  it("a rule's Retry asks for both its scopes (no scope filter)", async () => {
    const forSubject = vi.spyOn(trainingExclusionApi, "forSubject").mockResolvedValue({ items: [], limit: 100 });
    mount("ruleStorm", RULE, { kind: "error" });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(forSubject).toHaveBeenCalledWith("rule", "rule-1", undefined);
  });

  it("forbidden: names read:anomalies", () => {
    mount("waf", WAF, { kind: "forbidden", permission: "read:anomalies" });
    expect(screen.getByText("Reading training exclusions needs the read:anomalies permission.")).toBeInTheDocument();
    expect(screen.queryByText(/No analyst training exclusions/)).toBeNull();
  });
});

describe("a rule's rows", () => {
  const rows: ExclusionLoad = {
    kind: "ok",
    items: [
      exclusion({ id: "a", subject_kind: "rule", scope_key: "rule:storm" }),
      exclusion({ id: "b", subject_kind: "rule", scope_key: "rule:silence", starts_at: "2026-11-03T08:00:00Z", ends_at: "2026-11-03T09:00:00Z" }),
    ],
  };

  it("shows both scopes by name and 'Used from the next evaluation', never the rebuild wording", () => {
    mount("ruleStorm", RULE, rows);
    const table = screen.getByRole("table");
    expect(table).toHaveTextContent("Rule storm");
    expect(table).toHaveTextContent("Rule silence");
    expect(table).toHaveTextContent("Used from the next evaluation");
    expect(table).not.toHaveTextContent("Pending next baseline rebuild");
  });

  it("revoking says the interval returns from the next evaluation", async () => {
    vi.spyOn(trainingExclusionApi, "revoke").mockResolvedValue(
      exclusion({ id: "a", subject_kind: "rule", state: "REVOKED", revoked_by: "k.osei", revoked_at: "2026-11-05T09:00:00Z", revocation_reason: "real" }),
    );
    mount("ruleStorm", RULE, rows);
    await userEvent.click(screen.getAllByRole("button", { name: "Revoke" })[0]);
    expect(screen.getByRole("form", { name: "Revoke training exclusion" })).toHaveTextContent(
      "returns to the rule's baseline from the next evaluation",
    );
    await userEvent.type(screen.getByLabelText("Reason for revoking"), "real");
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(
      await screen.findByText("Exclusion revoked. The interval returns to the rule's baseline from the next evaluation."),
    ).toBeInTheDocument();
  });

  it("another family's pending row keeps the rebuild wording and its revoke sentence", async () => {
    mount("waf", WAF, { kind: "ok", items: [exclusion({ subject_kind: "endpoint", scope_key: "waf:endpoint" })] });
    expect(screen.getByRole("table")).toHaveTextContent("Pending next baseline rebuild");
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(screen.getByRole("form")).toHaveTextContent("returns to training at the next baseline rebuild");
  });
});

describe("who may revoke", () => {
  it("a read-only caller sees the rows and no Revoke, with the permission named", () => {
    mount("waf", WAF, { kind: "ok", items: [exclusion({ subject_kind: "endpoint", scope_key: "waf:endpoint" })] }, READ_ONLY);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.getByText("Revoking an exclusion needs the anomaly:triage permission.")).toBeInTheDocument();
  });
});
