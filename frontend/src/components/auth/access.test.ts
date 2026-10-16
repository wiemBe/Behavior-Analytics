import { describe, expect, it } from "vitest";

import { hasAnyAccess, holds, isDevPrincipal, signedInName, visibleNav } from "./access";
import type { NavGroup } from "@/lib/nav";

const groups: NavGroup[] = [
  { label: null, items: [{ href: "/", label: "Dashboard" }] },
  {
    label: "Administration",
    items: [
      { href: "/admin/search-cadence", label: "Search cadence" },
      { href: "/admin/users", label: "Users", permission: "admin:users" },
      { href: "/admin/directory", label: "Directory", permission: "admin:auth" },
    ],
  },
  {
    label: "Only gated",
    items: [{ href: "/secret", label: "Secret", permission: "admin:users" }],
  },
];

describe("holds", () => {
  it("grants an exact permission, its prefix wildcard, and admin:*", () => {
    expect(holds(["read:offenses"], "read:offenses")).toBe(true);
    expect(holds(["read:*"], "read:offenses")).toBe(true);
    expect(holds(["admin:*"], "admin:users")).toBe(true);
    expect(holds(["admin:*"], "admin:auth")).toBe(true);
  });

  it("does not let a read wildcard grant administration", () => {
    expect(holds(["read:*"], "admin:users")).toBe(false);
    expect(holds(["read:*"], "admin:auth")).toBe(false);
    expect(holds([], "admin:users")).toBe(false);
  });
});

describe("visibleNav", () => {
  it("hides administration entries the caller does not hold, and drops an empty group", () => {
    const visible = visibleNav(groups, ["read:*"]);
    const admin = visible.find((group) => group.label === "Administration");
    expect(admin?.items.map((item) => item.label)).toEqual(["Search cadence"]);
    expect(visible.some((group) => group.label === "Only gated")).toBe(false);
  });

  it("shows Users and Directory to admin:*", () => {
    const admin = visibleNav(groups, ["admin:*"]).find((group) => group.label === "Administration");
    expect(admin?.items.map((item) => item.label)).toEqual([
      "Search cadence",
      "Users",
      "Directory",
    ]);
  });
});

describe("session identity", () => {
  it("treats only a principal with no directory or local source as development", () => {
    expect(isDevPrincipal({ subject: "local-dev", username: "local-dev" })).toBe(true);
    expect(isDevPrincipal({ subject: "local-token" })).toBe(true);
    expect(
      isDevPrincipal({ subject: "local-dev", username: "local-dev", auth_source: "LOCAL" }),
    ).toBe(false);
    expect(isDevPrincipal({ subject: "ada", username: "ada", auth_source: "LDAP" })).toBe(false);
  });

  it("shows the username, then the subject", () => {
    expect(signedInName({ subject: "sub", username: " ada " })).toBe("ada");
    expect(signedInName({ subject: "local-dev", username: null })).toBe("local-dev");
  });

  it("treats an account with no grants as having no access", () => {
    expect(hasAnyAccess({ permissions: [], effective_permissions: [] })).toBe(false);
    expect(hasAnyAccess({ permissions: [], effective_permissions: ["read:offenses"] })).toBe(true);
  });
});
