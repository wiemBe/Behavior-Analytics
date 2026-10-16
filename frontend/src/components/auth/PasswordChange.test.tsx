import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PasswordChange } from "./PasswordChange";
import { StaticSession, type SessionValue } from "./session";
import { ApiError } from "@/lib/api";

const nav = vi.hoisted(() => ({ replace: vi.fn(), params: "next=/alerts" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/account/password",
  useSearchParams: () => new URLSearchParams(nav.params),
}));

const client = vi.hoisted(() => ({ changePassword: vi.fn() }));
vi.mock("./client", () => client);

function value(): SessionValue {
  return {
    status: "ready",
    me: {
      subject: "ada",
      email: "ada@example.com",
      permissions: ["admin:*"],
      effective_permissions: ["admin:*"],
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: true,
      roles: ["admin"],
    },
    error: null,
    reload: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn().mockResolvedValue(null),
  };
}

function renderForm(session = value(), required = false) {
  return render(
    <StaticSession value={session}>
      <PasswordChange required={required} />
    </StaticSession>,
  );
}

async function fill(current: string, next: string, confirm = next) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Current password"), current);
  await user.type(screen.getByLabelText("New password"), next);
  await user.type(screen.getByLabelText("Confirm new password"), confirm);
  await user.click(screen.getByRole("button", { name: "Change password" }));
}

beforeEach(() => {
  nav.replace.mockReset();
  nav.params = "next=/alerts";
  client.changePassword.mockReset();
});

describe("PasswordChange", () => {
  it("is the only page while a change is required, and can sign out", async () => {
    const session = value();
    renderForm(session, true);
    expect(screen.getByRole("heading", { level: 1, name: "Change password" })).toBeInTheDocument();
    expect(screen.getByText("This account cannot continue until the password is changed.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(session.signOut).toHaveBeenCalledTimes(1);
  });

  it("does not call the API when the confirmation differs", async () => {
    renderForm();
    await fill("old-password-1", "new-password-1", "new-password-2");
    expect(client.changePassword).not.toHaveBeenCalled();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveClass("banner", "warn");
    expect(alert).toHaveTextContent("The password was not changed.");
    expect(alert).toHaveTextContent("The new password and the confirmation do not match.");
  });

  it("lists every 422 reason as sent", async () => {
    client.changePassword.mockRejectedValue(
      new ApiError(422, "Unprocessable", ["at least 12 characters", "must not equal the username"]),
    );
    renderForm();
    await fill("old-password-1", "new-password-1");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The password was not changed.");
    expect(alert).toHaveTextContent("at least 12 characters");
    expect(alert).toHaveTextContent("must not equal the username");
  });

  it("shows a wrong current password as sent", async () => {
    client.changePassword.mockRejectedValue(new ApiError(401, "current password is wrong", "current password is wrong"));
    renderForm();
    await fill("old-password-1", "new-password-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("current password is wrong");
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("shows the directory refusal as sent", async () => {
    client.changePassword.mockRejectedValue(
      new ApiError(
        403,
        "LDAP accounts change their password in the directory, not here.",
        "LDAP accounts change their password in the directory, not here.",
      ),
    );
    renderForm();
    await fill("old-password-1", "new-password-1");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "LDAP accounts change their password in the directory, not here.",
    );
  });

  it("reloads the account and returns to the safe path", async () => {
    const session = value();
    client.changePassword.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: ["admin"],
    });
    renderForm(session);
    await fill("old-password-1", "new-password-1");
    expect(client.changePassword).toHaveBeenCalledWith("old-password-1", "new-password-1");
    expect(session.reload).toHaveBeenCalled();
    expect(nav.replace).toHaveBeenCalledWith("/alerts");
    expect(await screen.findByRole("status")).toHaveTextContent("The password is changed.");
  });

  it("stays when the account cannot be read again", async () => {
    const session = value();
    session.reload = vi.fn().mockRejectedValue(new Error("down"));
    client.changePassword.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: ["admin"],
    });
    renderForm(session);
    await fill("old-password-1", "new-password-1");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The password is changed, but the account could not be read again. Try again.",
    );
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
