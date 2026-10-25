import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "./session";
import type { CallerInfo } from "@/lib/api";

const nav = vi.hoisted(() => ({ refresh: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: nav.refresh, replace: nav.replace, push: vi.fn() }),
}));

const client = vi.hoisted(() => ({ fetchSession: vi.fn(), logout: vi.fn() }));
vi.mock("./client", () => client);

const locale = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn() }));
vi.mock("@/i18n/locale", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n/locale")>()),
  readLocaleCookie: locale.read,
  writeLocaleCookie: locale.write,
}));

function me(language?: "en" | "tr"): CallerInfo {
  return {
    subject: "ada",
    email: "ada@example.com",
    permissions: ["read:*"],
    effective_permissions: ["read:overview"],
    username: "ada",
    auth_source: "LOCAL",
    roles: ["analyst"],
    ...(language ? { ui_language: language } : {}),
  };
}

function Probe() {
  const session = useSession();
  return (
    <>
      <p>{session.status}</p>
      <button type="button" onClick={() => void session.reload()}>reload</button>
    </>
  );
}

beforeEach(() => {
  nav.refresh.mockReset();
  nav.replace.mockReset();
  client.fetchSession.mockReset();
  locale.read.mockReset();
  locale.write.mockReset();
});

describe("session language synchronization", () => {
  it("writes a differing account preference and refreshes once", async () => {
    client.fetchSession.mockResolvedValue(me("tr"));
    locale.read.mockReturnValue("en");
    locale.write.mockReturnValue(true);
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    expect(locale.write).toHaveBeenCalledWith("tr");
    expect(nav.refresh).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the cookie is equal or the field is missing", async () => {
    client.fetchSession.mockResolvedValueOnce(me("en"));
    locale.read.mockReturnValue("en");
    const { unmount } = render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    expect(locale.write).not.toHaveBeenCalled();
    unmount();

    client.fetchSession.mockResolvedValueOnce(me());
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    expect(locale.write).not.toHaveBeenCalled();
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("keeps a browser-only preference for a session without an account", async () => {
    client.fetchSession.mockResolvedValue({
      ...me("en"),
      username: null,
      auth_source: null,
    });
    locale.read.mockReturnValue("tr");
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    expect(locale.write).not.toHaveBeenCalled();
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("does not loop when the browser refuses the cookie", async () => {
    client.fetchSession.mockResolvedValue(me("tr"));
    locale.read.mockReturnValue("en");
    locale.write.mockReturnValue(false);
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(locale.write).toHaveBeenCalledTimes(1);
    expect(nav.refresh).not.toHaveBeenCalled();
  });
});
