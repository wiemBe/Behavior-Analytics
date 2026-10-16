import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountHome } from "./AccountHome";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import type { CallerInfo } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function me(over: Partial<CallerInfo> = {}): CallerInfo {
  return {
    subject: "ada",
    email: "ada@example.com",
    permissions: ["admin:*"],
    effective_permissions: ["admin:*"],
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["admin"],
    ...over,
  };
}

function session(over: Partial<SessionValue> = {}): SessionValue {
  return {
    status: "ready",
    me: me(),
    error: null,
    reload: vi.fn(),
    signOut: vi.fn(),
    ...over,
  };
}

describe("AccountHome", () => {
  it("offers the password form to a local account", () => {
    render(
      <StaticSession value={session()}>
        <AccountHome />
      </StaticSession>,
    );
    expect(screen.getByRole("form", { name: "Change password" })).toBeInTheDocument();
  });

  it("tells a directory account to change its password in the directory", () => {
    render(
      <StaticSession value={session({ me: me({ auth_source: "LDAP" }) })}>
        <AccountHome />
      </StaticSession>,
    );
    expect(
      screen.getByText("An LDAP account changes its password in the directory, not here."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Change password" })).not.toBeInTheDocument();
  });

  it("waits while the session is loading", () => {
    render(
      <StaticSession value={session({ status: "loading", me: null })}>
        <AccountHome />
      </StaticSession>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading the account");
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });
});
