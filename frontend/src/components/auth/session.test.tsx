import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "./session";
import { ApiError, type CallerInfo } from "@/lib/api";

const nav = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/behavior",
  useSearchParams: () => new URLSearchParams(),
}));

const client = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("./client", () => client);

function me(username = "ada"): CallerInfo {
  return {
    subject: username,
    email: `${username}@example.com`,
    permissions: ["admin:*"],
    effective_permissions: ["admin:*"],
    username,
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["admin"],
  };
}

function Probe() {
  const session = useSession();
  return (
    <div>
      <p data-testid="session">
        {session.status} {session.me?.username ?? ""} {session.error ?? ""}
      </p>
      <button type="button" onClick={() => void session.reload()}>
        reload
      </button>
      <button type="button" onClick={() => void session.signOut()}>
        sign out
      </button>
    </div>
  );
}

beforeEach(() => {
  nav.replace.mockReset();
  client.fetchSession.mockReset();
  client.logout.mockReset();
});

describe("session", () => {
  it("stays loading when there is no provider, so the bar renders nothing about the account", () => {
    render(<Probe />);
    expect(screen.getByTestId("session")).toHaveTextContent(/^loading/);
  });

  it("becomes ready from /me", async () => {
    client.fetchSession.mockResolvedValue(me());
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    expect(await screen.findByText(/ready ada/)).toBeInTheDocument();
  });

  it("treats 401 as signed out", async () => {
    client.fetchSession.mockRejectedValue(new ApiError(401, "Authentication required."));
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    expect(await screen.findByText(/anonymous/)).toBeInTheDocument();
  });

  it("records a failed read", async () => {
    client.fetchSession.mockRejectedValue(new ApiError(500, "boom"));
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    expect(await screen.findByText(/error/)).toBeInTheDocument();
    expect(screen.getByTestId("session")).toHaveTextContent("boom");
  });

  it("ignores a slow first read after a later one has won", async () => {
    let resolveFirst: (value: CallerInfo) => void = () => {};
    client.fetchSession.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    client.fetchSession.mockResolvedValueOnce(me("bea"));
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(await screen.findByText(/ready bea/)).toBeInTheDocument();
    resolveFirst(me("ada"));
    await screen.findByText(/ready bea/);
    expect(screen.getByTestId("session")).toHaveTextContent("ready bea");
    expect(screen.getByTestId("session")).not.toHaveTextContent("ada");
  });

  it("aborts the read on unmount", () => {
    let signal: AbortSignal | undefined;
    client.fetchSession.mockImplementation((next?: AbortSignal) => {
      signal = next;
      return new Promise(() => {});
    });
    const { unmount } = render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("signs out and leaves for the sign-in page", async () => {
    client.fetchSession.mockResolvedValue(me());
    client.logout.mockResolvedValue(undefined);
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    await screen.findByText(/ready ada/);
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(client.logout).toHaveBeenCalledTimes(1);
    expect(nav.replace).toHaveBeenCalledWith("/login");
    expect(screen.getByTestId("session")).toHaveTextContent(/anonymous/);
  });

  it("stays signed in when sign-out fails for a reason other than 401", async () => {
    client.fetchSession.mockResolvedValue(me());
    client.logout.mockRejectedValue(new ApiError(503, "directory down"));
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    await screen.findByText(/ready ada/);
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(nav.replace).not.toHaveBeenCalled();
    expect(screen.getByTestId("session")).toHaveTextContent("ready ada");
    expect(screen.getByTestId("session")).toHaveTextContent("directory down");
  });

  it("still leaves when the session is already gone", async () => {
    client.fetchSession.mockResolvedValue(me());
    client.logout.mockRejectedValue(new ApiError(401, "Authentication required."));
    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );
    await screen.findByText(/ready ada/);
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(nav.replace).toHaveBeenCalledWith("/login");
  });
});
