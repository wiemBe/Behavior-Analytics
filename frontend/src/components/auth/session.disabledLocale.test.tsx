import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "./session";
import type { CallerInfo } from "@/lib/api";
import { enableTurkish } from "../../../vitest.setup";

// D-41, with the real locale module (no mock): an account whose stored language is Turkish gets
// English, and the session sync does not write the cookie, refresh or loop.

const nav = vi.hoisted(() => ({ refresh: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: nav.refresh, replace: nav.replace, push: vi.fn() }),
}));

const client = vi.hoisted(() => ({ fetchSession: vi.fn(), logout: vi.fn() }));
vi.mock("./client", () => client);

function me(language: "en" | "tr"): CallerInfo {
  return {
    subject: "ada",
    email: "ada@example.com",
    permissions: ["read:*"],
    effective_permissions: ["read:overview"],
    username: "ada",
    auth_source: "LOCAL",
    roles: ["analyst"],
    ui_language: language,
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

const writes: string[] = [];
const storeDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, "cookie")!;

beforeEach(() => {
  writes.length = 0;
  nav.refresh.mockReset();
  client.fetchSession.mockReset();
  document.cookie = "ba_locale=; Path=/; Max-Age=0";
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => storeDescriptor.get!.call(document),
    set: (value: string) => {
      if (value.startsWith("ba_locale=") && !value.includes("Max-Age=0")) writes.push(value);
      storeDescriptor.set!.call(document, value);
    },
  });
});

afterEach(() => {
  Reflect.deleteProperty(document, "cookie");
  document.cookie = "ba_locale=; Path=/; Max-Age=0";
});

describe("session language sync while only English is enabled", () => {
  it("does not write the cookie or refresh for an account set to Turkish, across reloads", async () => {
    client.fetchSession.mockResolvedValue(me("tr"));
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(client.fetchSession).toHaveBeenCalledTimes(3);
    expect(writes).toEqual([]);
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("does not rewrite a tr cookie either", async () => {
    document.cookie = "ba_locale=tr; Path=/";
    writes.length = 0;
    client.fetchSession.mockResolvedValue(me("tr"));
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    expect(writes).toEqual([]);
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("writes the cookie once and refreshes once when Turkish is enabled", async () => {
    enableTurkish();
    client.fetchSession.mockResolvedValue(me("tr"));
    render(<SessionProvider><Probe /></SessionProvider>);
    await screen.findByText("ready");
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(writes).toHaveLength(1);
    expect(writes[0]).toContain("ba_locale=tr");
    expect(nav.refresh).toHaveBeenCalledTimes(1);
  });
});
