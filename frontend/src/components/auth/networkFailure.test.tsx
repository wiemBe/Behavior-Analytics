// What the sign-in form and the password form show when the request never reaches the server
// (a non-ApiError). `errorTexts` reads `auth.requestSendFailed`; both forms hand it a translator
// scoped to the `auth` namespace.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import { LoginForm } from "@/app/login/LoginForm";
import { PasswordChange } from "./PasswordChange";
import { StaticSession, type SessionValue } from "./session";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(""),
}));

const client = vi.hoisted(() => ({ login: vi.fn(), changePassword: vi.fn() }));
vi.mock("./client", () => client);
vi.mock("@/components/auth/client", () => client);

const base = {
  error: null,
  reload: vi.fn().mockResolvedValue(undefined),
  signOut: vi.fn().mockResolvedValue(null),
};
const anonymous: SessionValue = { ...base, status: "anonymous", me: null };
const signedIn: SessionValue = {
  ...base,
  status: "ready",
  me: {
    subject: "ada",
    email: null,
    permissions: ["read:*"],
    effective_permissions: ["read:*"],
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["viewer"],
  },
};

const SENT = {
  en: "The request could not be sent. Check your connection and try again.",
  tr: "İstek gönderilemedi. Bağlantınızı denetleyip tekrar deneyin.",
} as const;

beforeEach(() => {
  client.login.mockReset();
  client.changePassword.mockReset();
});

describe.each(["en", "tr"] as const)("a network failure in %s", (locale) => {
  const labels =
    locale === "en"
      ? { user: "Username", pass: "Password", signIn: "Sign in", cur: "Current password", next: "New password", conf: "Confirm new password", change: "Change password" }
      : { user: "Kullanıcı adı", pass: "Parola", signIn: "Oturum aç", cur: "Mevcut parola", next: "Yeni parola", conf: "Yeni parolayı doğrula", change: "Parolayı değiştir" };

  it("is worded by the sign-in form", async () => {
    setTestLocale(locale);
    client.login.mockRejectedValue(new Error("offline"));
    render(
      <StaticSession value={anonymous}>
        <LoginForm />
      </StaticSession>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(labels.user), "ada");
    await user.type(screen.getByLabelText(labels.pass), "secret-value");
    await user.click(screen.getByRole("button", { name: labels.signIn }));
    expect(await screen.findByText(SENT[locale])).toBeInTheDocument();
  });

  it("is worded by the password form", async () => {
    setTestLocale(locale);
    client.changePassword.mockRejectedValue(new Error("offline"));
    render(
      <StaticSession value={signedIn}>
        <PasswordChange />
      </StaticSession>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(labels.cur), "old-password-1");
    await user.type(screen.getByLabelText(labels.next), "new-password-12");
    await user.type(screen.getByLabelText(labels.conf), "new-password-12");
    await user.click(screen.getByRole("button", { name: labels.change }));
    expect(await screen.findByText(SENT[locale])).toBeInTheDocument();
  });
});
