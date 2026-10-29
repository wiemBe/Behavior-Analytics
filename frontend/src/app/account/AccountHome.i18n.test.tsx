import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountHome } from "./AccountHome";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, type CallerInfo } from "@/lib/api";
import { setTestLocale } from "../../../vitest.setup";

const nav = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: nav.refresh }),
  useSearchParams: () => new URLSearchParams(),
}));

const preference = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  updateUiLanguage: preference.update,
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
    ui_language: "en",
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

function view(value = session()) {
  return render(<StaticSession value={value}><AccountHome /></StaticSession>);
}

async function chooseTurkishAndSave() {
  await userEvent.click(screen.getByRole("radio", { name: "Türkçe" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
}

beforeEach(() => {
  preference.update.mockReset();
  nav.refresh.mockReset();
  document.cookie = "ba_locale=; Max-Age=0; Path=/";
});

afterEach(() => {
  document.cookie = "ba_locale=; Max-Age=0; Path=/";
});

describe("account language preference", () => {
  it("designs the loading state", () => {
    setTestLocale("tr");
    view(session({ status: "loading", me: null }));
    expect(screen.getByRole("status")).toHaveTextContent("hesap yükleniyor…");
  });

  it("disables the language control and says it is saving", async () => {
    preference.update.mockReturnValue(new Promise(() => {}));
    view();
    await chooseTurkishAndSave();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("group", { name: "Interface language" })).toBeDisabled();
  });

  it("stores the account preference, writes the cookie and refreshes", async () => {
    preference.update.mockResolvedValue(me({ ui_language: "tr" }));
    const rendered = view();
    await chooseTurkishAndSave();
    expect(await screen.findByRole("status")).toHaveTextContent("The language preference was saved.");
    expect(document.cookie).toContain("ba_locale=tr");
    expect(nav.refresh).toHaveBeenCalledTimes(1);

    setTestLocale("tr");
    rendered.rerender(<StaticSession value={session()}><AccountHome /></StaticSession>);
    expect(screen.getByRole("status")).toHaveTextContent("Dil tercihi kaydedildi.");
  });

  it("handles a development principal's 409 as browser-only", async () => {
    preference.update.mockRejectedValue(new ApiError(409, "no account"));
    const rendered = view();
    await chooseTurkishAndSave();
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("saved on this browser only");
    expect(status).toHaveClass("neutral");
    expect(status).not.toHaveClass("ok");
    expect(document.cookie).toContain("ba_locale=tr");
    expect(nav.refresh).toHaveBeenCalledTimes(1);

    setTestLocale("tr");
    rendered.rerender(<StaticSession value={session()}><AccountHome /></StaticSession>);
    expect(screen.getByRole("status")).toHaveTextContent("yalnızca bu tarayıcıda kaydedildi");
    expect(screen.getByRole("status")).toHaveClass("neutral");
  });

  it("leaves the cookie and selected language unchanged on failure", async () => {
    preference.update.mockRejectedValue(new ApiError(500, "down"));
    view();
    await chooseTurkishAndSave();
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be saved");
    expect(document.cookie).not.toContain("ba_locale=tr");
    await waitFor(() => expect(screen.getByRole("radio", { name: "English" })).toBeChecked());
  });

  it("shows the Turkish account settings and directory password state", () => {
    setTestLocale("tr");
    view(session({ me: me({ auth_source: "LDAP", ui_language: "tr" }) }));
    expect(screen.getByRole("heading", { level: 1, name: "Hesap ayarları" })).toBeInTheDocument();
    expect(screen.getByText("LDAP hesabı parolasını burada değil, dizinde değiştirir.")).toBeInTheDocument();
  });

  it("keeps a must-change account on the password form only", () => {
    view(session({ me: me({ must_change_password: true }) }));
    expect(screen.getByRole("form", { name: "Change password" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Interface language" })).not.toBeInTheDocument();
  });
});
