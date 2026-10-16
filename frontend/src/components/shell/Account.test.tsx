import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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

function summary(container: HTMLElement): HTMLElement {
  const trigger = container.querySelector("summary");
  if (!trigger) throw new Error("missing account summary");
  return trigger;
}

afterEach(() => vi.unstubAllGlobals());

describe("Account", () => {
  it("shows the API version in the menu, or says it could not be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ version: "3.2.1", git_sha: null, migration_head: "0025" }),
      })),
    );
    const { unmount } = render(
      <StaticSession value={session()}>
        <Account />
      </StaticSession>,
    );
    expect(await screen.findByText("API version 3.2.1")).toBeInTheDocument();
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("down"))));
    render(
      <StaticSession value={session()}>
        <Account />
      </StaticSession>,
    );
    expect(await screen.findByText("API version could not be read")).toBeInTheDocument();
  });

  it("shows the username and signs out from the menu", async () => {
    const value = session();
    const { container } = render(
      <StaticSession value={value}>
        <Account />
      </StaticSession>,
    );
    const trigger = summary(container);
    expect(trigger).toHaveAttribute("aria-label", "Account: ada");
    expect(trigger).toHaveTextContent("ada");
    await userEvent.click(trigger);
    expect(screen.getByText("Signed in as ada")).toBeVisible();
    expect(screen.getByText("Source: LOCAL")).toBeVisible();
    expect(screen.getByText("Roles: admin")).toBeVisible();
    expect(screen.getByRole("link", { name: "Change password" })).toHaveAttribute("href", "/account");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(value.signOut).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape and returns focus to the account control", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <StaticSession value={session()}>
        <Account />
      </StaticSession>,
    );
    const trigger = summary(container);
    const details = container.querySelector("details");
    await user.click(trigger);
    expect(details?.open).toBe(true);
    expect(screen.getByText("Signed in as ada")).toBeVisible();
    trigger.focus();
    await user.keyboard("{Escape}");
    expect(details?.open).toBe(false);
    expect(trigger).toHaveFocus();
    expect(screen.getByText("Signed in as ada")).not.toBeVisible();
  });

  it("shows a development principal and no Sign out", async () => {
    const { container } = render(
      <StaticSession value={session({ me: me({ username: "local-dev", subject: "local-dev", auth_source: null, roles: [] }) })}>
        <Account />
      </StaticSession>,
    );
    await userEvent.click(summary(container));
    expect(screen.getByText("Signed in as local-dev")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Change password" })).not.toBeInTheDocument();
  });

  it("still offers Sign out when a local account is named local-dev", async () => {
    const { container } = render(
      <StaticSession
        value={session({ me: me({ username: "local-dev", subject: "local-dev", auth_source: "LOCAL" }) })}
      >
        <Account />
      </StaticSession>,
    );
    await userEvent.click(summary(container));
    expect(screen.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  it("offers no password change for a directory account", async () => {
    const { container } = render(
      <StaticSession value={session({ me: me({ auth_source: "LDAP", roles: ["analyst"] }) })}>
        <Account />
      </StaticSession>,
    );
    await userEvent.click(summary(container));
    expect(screen.getByText("Source: LDAP")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Change password" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeVisible();
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
    const { container } = render(
      <StaticSession value={value}>
        <Account />
      </StaticSession>,
    );
    await userEvent.click(summary(container));
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("directory down");
  });
});
