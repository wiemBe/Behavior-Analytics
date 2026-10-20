import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppFrame } from "./AppFrame";
import { StaticSession, type SessionValue } from "./session";
import { api, type CallerInfo } from "@/lib/api";
import { consoleOf, estimateOf } from "@/components/history/fixtures";

const nav = vi.hoisted(() => ({ pathname: "/behavior" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    shellApi: {
      healthReady: vi.fn().mockResolvedValue({ status: "ready", database: "ok" }),
      overview: vi.fn().mockResolvedValue({
        instance_status: "HEALTHY",
        instance_version: "7.5",
        alerts: { open: 0, acknowledged: 0, by_severity: {} },
      }),
      searchCadence: vi.fn().mockResolvedValue([]),
      version: vi.fn().mockResolvedValue({ version: "0.1.0", git_sha: null, migration_head: null }),
    },
  };
});

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
    reload: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn().mockResolvedValue(null),
    ...over,
  };
}

function frame(value: SessionValue) {
  return render(
    <StaticSession value={value}>
      <AppFrame>
        <p>page body</p>
      </AppFrame>
    </StaticSession>,
  );
}

beforeEach(() => {
  nav.pathname = "/behavior";
  // The first-run history-backfill dialog asks the status of an administrator; nothing to ask here.
  vi.spyOn(api, "historyBackfill").mockResolvedValue([]);
});

describe("AppFrame", () => {
  it("shows the shell and the administration entries an admin holds", () => {
    frame(session());
    expect(screen.getByText("page body")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Directory" })).toBeInTheDocument();
    expect(screen.getByText("Signed in as ada")).toBeInTheDocument();
  });

  it("hides Users and Directory until the session is known, and while the caller lacks them", () => {
    frame(session({ status: "loading", me: null }));
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Directory" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();

    frame(session({ me: me({ permissions: ["read:offenses"], effective_permissions: ["read:offenses"] }) }));
    expect(screen.getAllByRole("link", { name: "Dashboard" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Directory" })).not.toBeInTheDocument();
  });

  it("replaces the page while a password change is required", () => {
    frame(session({ me: me({ must_change_password: true }) }));
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("shows only the no-role page when the account has no permission", () => {
    frame(
      session({
        me: me({ permissions: [], effective_permissions: [], roles: [] }),
      }),
    );
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "No role assigned" })).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("does not show the requested page when nobody is signed in", () => {
    frame(session({ status: "anonymous", me: null }));
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByText("Sign-in is required.")).toBeInTheDocument();
  });

  it("fails closed when the session cannot be read, and retries", async () => {
    const value = session({ status: "error", me: null, error: "boom" });
    frame(value);
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The session could not be read.");
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(value.reload).toHaveBeenCalledTimes(1);
  });

  it("renders sign-in without the shell", () => {
    nav.pathname = "/login";
    frame(session({ status: "anonymous", me: null }));
    expect(screen.getByText("page body")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  describe("the first-run history-backfill dialog", () => {
    beforeEach(() => {
      window.sessionStorage.clear();
      HTMLDialogElement.prototype.showModal = function showModal() {
        this.setAttribute("open", "");
      };
      HTMLDialogElement.prototype.close = function close() {
        this.removeAttribute("open");
      };
      vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
      vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    });

    it("opens over the first page for an administrator when a console is undecided", async () => {
      frame(session());
      expect(await screen.findByRole("dialog", { name: /Read the last 28 days/ })).toBeInTheDocument();
      expect(screen.getByText("page body")).toBeInTheDocument();
    });

    it("is not mounted for an account without admin:history_backfill", async () => {
      frame(session({ me: me({ permissions: ["read:*"], effective_permissions: ["read:*"] }) }));
      await act(async () => {});
      expect(api.historyBackfill).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("is not mounted on the sign-in page", async () => {
      nav.pathname = "/login";
      frame(session());
      await act(async () => {});
      expect(api.historyBackfill).not.toHaveBeenCalled();
    });

    it("is not mounted while a password change is required", async () => {
      frame(session({ me: me({ must_change_password: true }) }));
      await act(async () => {});
      expect(api.historyBackfill).not.toHaveBeenCalled();
    });
  });
});
