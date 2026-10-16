import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Account } from "./Account";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import type { CallerInfo } from "@/lib/api";

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
    signOut: vi.fn().mockResolvedValue(null),
    ...over,
  };
}

describe("Account", () => {
  it("shows the username and signs out", async () => {
    const value = session();
    render(
      <StaticSession value={value}>
        <Account />
      </StaticSession>,
    );
    expect(screen.getByText("Signed in as ada")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(value.signOut).toHaveBeenCalledTimes(1);
  });

  it("shows a development principal and no Sign out", () => {
    render(
      <StaticSession value={session({ me: me({ username: "local-dev", subject: "local-dev", auth_source: null }) })}>
        <Account />
      </StaticSession>,
    );
    expect(screen.getByText("Signed in as local-dev")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("still offers Sign out when a local account is named local-dev", () => {
    render(
      <StaticSession
        value={session({ me: me({ username: "local-dev", subject: "local-dev", auth_source: "LOCAL" }) })}
      >
        <Account />
      </StaticSession>,
    );
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("renders nothing while the session is loading", () => {
    const { container } = render(
      <StaticSession value={session({ status: "loading", me: null })}>
        <Account />
      </StaticSession>,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/sign out/i)).not.toBeInTheDocument();
  });

  it("shows the reason sign-out did not finish", async () => {
    const value = session({ signOut: vi.fn().mockResolvedValue("directory down") });
    render(
      <StaticSession value={value}>
        <Account />
      </StaticSession>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("directory down");
  });
});
