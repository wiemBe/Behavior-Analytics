// F5 (HOTFIX-1): after Sign out and Sign in without a reload, `/` showed only "Sign-in is required."
// while `/me` answered 200. Leaving the sign-in page while the session still reads as signed out
// reads `/me` once more.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppFrame } from "./AppFrame";
import { StaticSession, type SessionValue } from "./session";

const nav = vi.hoisted(() => ({ pathname: "/login" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function anonymous(reload: SessionValue["reload"]): SessionValue {
  return { status: "anonymous", me: null, error: null, reload, signOut: vi.fn().mockResolvedValue(null) };
}

function frame(value: SessionValue) {
  return (
    <StaticSession value={value}>
      <AppFrame>
        <p>page</p>
      </AppFrame>
    </StaticSession>
  );
}

beforeEach(() => {
  nav.pathname = "/login";
});

describe("AppFrame after a sign-in", () => {
  it("reads /me again when the sign-in page is left while the session reads as signed out", () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    const value = anonymous(reload);
    const view = render(frame(value));
    expect(reload).not.toHaveBeenCalled();
    nav.pathname = "/";
    view.rerender(frame(value));
    expect(reload).toHaveBeenCalledTimes(1);
    view.rerender(frame(value));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not read /me for a visitor who never was on the sign-in page, nor after a sign-out", () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    nav.pathname = "/alerts";
    render(frame(anonymous(reload)));
    expect(screen.getByText("Sign-in is required.")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });

  it("does not read /me when the sign-in page is left with a ready session", () => {
    const reload = vi.fn().mockResolvedValue(undefined);
    const ready: SessionValue = {
      status: "ready",
      me: {
        subject: "ada",
        email: null,
        permissions: ["admin:*"],
        effective_permissions: ["admin:*"],
        username: "ada",
        must_change_password: false,
        roles: ["admin"],
      },
      error: null,
      reload,
      signOut: vi.fn().mockResolvedValue(null),
    };
    const view = render(frame(ready));
    nav.pathname = "/";
    view.rerender(frame(ready));
    expect(reload).not.toHaveBeenCalled();
  });

  it("survives a failed re-read", async () => {
    const reload = vi.fn().mockRejectedValue(new Error("down"));
    const value = anonymous(reload);
    const view = render(frame(value));
    nav.pathname = "/";
    view.rerender(frame(value));
    await Promise.resolve();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Sign-in is required.")).toBeInTheDocument();
  });
});
