// The password policy as a coded 422 list on the account page (MSG-1): Turkish words each
// coded reason; English keeps the server's sentences; an uncoded list is shown as sent.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { setTestLocale } from "../../../vitest.setup";

import { PasswordChange } from "./PasswordChange";
import { StaticSession, type SessionValue } from "./session";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/account/password",
  useSearchParams: () => new URLSearchParams(""),
}));

const client = vi.hoisted(() => ({ changePassword: vi.fn() }));
vi.mock("./client", () => client);

const session: SessionValue = {
  status: "ready",
  me: {
    subject: "ada",
    email: "ada@example.com",
    permissions: ["read:*"],
    effective_permissions: ["read:*"],
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["viewer"],
  },
  error: null,
  reload: vi.fn().mockResolvedValue(undefined),
  signOut: vi.fn().mockResolvedValue(null),
};

const policy = new ApiError(
  422,
  "Unprocessable",
  ["password must be at least 12 characters", "password must not equal the username"],
  {
    detail: ["password must be at least 12 characters", "password must not equal the username"],
    detail_codes: [
      { code: "auth.password.too_short", params: {} },
      { code: "auth.password.equals_username", params: {} },
    ],
  },
);

async function submit(labels: { current: string; next: string; confirm: string; button: string }) {
  render(
    <StaticSession value={session}>
      <PasswordChange />
    </StaticSession>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(labels.current), "old-password-1");
  await user.type(screen.getByLabelText(labels.next), "short");
  await user.type(screen.getByLabelText(labels.confirm), "short");
  await user.click(screen.getByRole("button", { name: labels.button }));
  return screen.findByRole("alert");
}

beforeEach(() => {
  client.changePassword.mockReset();
});

describe("the password policy refusal", () => {
  it("lists the coded reasons in Turkish", async () => {
    setTestLocale("tr");
    client.changePassword.mockRejectedValue(policy);
    const alert = await submit({
      current: "Mevcut parola",
      next: "Yeni parola",
      confirm: "Yeni parolayı doğrula",
      button: "Parolayı değiştir",
    });
    expect(alert).toHaveTextContent("Parola en az 12 karakter olmalıdır");
    expect(alert).toHaveTextContent("Parola kullanıcı adıyla aynı olmamalıdır");
    expect(alert).not.toHaveTextContent("password must");
  });

  it("lists the server's sentences in English", async () => {
    setTestLocale("en");
    client.changePassword.mockRejectedValue(policy);
    const alert = await submit({
      current: "Current password",
      next: "New password",
      confirm: "Confirm new password",
      button: "Change password",
    });
    expect(alert).toHaveTextContent("password must be at least 12 characters");
    expect(alert).toHaveTextContent("password must not equal the username");
  });

  it("shows an uncoded list as sent, even in Turkish", async () => {
    setTestLocale("tr");
    client.changePassword.mockRejectedValue(
      new ApiError(422, "Unprocessable", ["password must be at least 12 characters"]),
    );
    const alert = await submit({
      current: "Mevcut parola",
      next: "Yeni parola",
      confirm: "Yeni parolayı doğrula",
      button: "Parolayı değiştir",
    });
    expect(alert).toHaveTextContent("password must be at least 12 characters");
  });
});
