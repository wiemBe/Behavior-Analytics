import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import type { CallerInfo } from "@/lib/api";

import { consoleOf, legacySwitchedOffFamily } from "./fixtures";
import { HistoryTable } from "./HistoryTable";
import { rowsOf } from "./historyRows";

const NOW = new Date("2026-10-21T12:00:00Z");
const OFF = "IPS collection is off. An administrator can turn it on under Administration → Features.";

function session(permissions: string[]): SessionValue {
  const me = {
    subject: "ada",
    email: null,
    permissions,
    effective_permissions: permissions,
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: [],
  } as CallerInfo;
  return { status: "ready", me, error: null, reload: vi.fn(), signOut: vi.fn() };
}

function show(permissions: string[], console = consoleOf()) {
  return render(
    <StaticSession value={session(permissions)}>
      <HistoryTable console={console} now={NOW} />
    </StaticSession>,
  );
}

const reasonOf = () =>
  screen.getByText((_, el) => el?.tagName === "DD" && (el.textContent ?? "").startsWith("IPS collection is off."));

describe("a switched-off family row on the history page", () => {
  it("words the family from the new code, with no environment name", () => {
    const rows = rowsOf(consoleOf());
    expect(rows.find((r) => r.family === "IPS")?.reason).toBe(OFF);
    expect(rows.find((r) => r.family === "WAF")?.reason).not.toContain("_ENABLED");
  });

  it("links Administration → Features for an account holding read:features", () => {
    show(["read:history_backfill", "read:features"]);
    // IPS and Rule matches are both off in the fixture console: one link in each row.
    const links = screen.getAllByRole("link", { name: "Administration → Features" });
    expect(links).toHaveLength(2);
    for (const link of links) expect(link).toHaveAttribute("href", "/admin/features");
    expect(reasonOf().textContent).toBe(OFF);
  });

  it("shows the same sentence as text, with no link, without read:features", () => {
    show(["read:history_backfill"]);
    expect(screen.queryByRole("link", { name: "Administration → Features" })).toBeNull();
    expect(reasonOf().textContent).toBe(OFF);
  });

  it("still renders a row stored with the old code and an environment name", () => {
    show(["read:features"], consoleOf({ families: [legacySwitchedOffFamily()] }));
    expect(screen.getByText("RULE_MATCH_COLLECTION_ENABLED is off")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Administration → Features" })).toBeNull();
  });
});
