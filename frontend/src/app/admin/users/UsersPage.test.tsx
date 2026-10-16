import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UsersPage, roleChoices } from "./UsersPage";
import Loading from "./loading";
import UsersError from "./error";
import type { Account, RoleInfo, UserDraft } from "@/components/auth/client";
import { ApiError } from "@/lib/api";

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  listRoles: vi.fn(),
  createUser: vi.fn(),
  setUserRoles: vi.fn(),
  disableUser: vi.fn(),
  enableUser: vi.fn(),
  unlockUser: vi.fn(),
  resetUserPassword: vi.fn(),
}));
vi.mock("@/components/auth/client", () => api);

const roles: RoleInfo[] = [
  { name: "admin", permissions: ["admin:*"] },
  { name: "analyst", permissions: ["read:offenses"] },
  { name: "viewer", permissions: ["read:*"] },
  { name: "detection_engineer", permissions: ["read:rules"] },
];

function account(over: Partial<Account> = {}): Account {
  return {
    id: "1",
    username: "ada",
    auth_source: "LOCAL",
    display_name: "Ada Lovelace",
    email: "ada@example.com",
    roles: ["admin"],
    disabled: false,
    disabled_at: null,
    last_login_at: "2026-10-15T12:00:00Z",
    locked_until: null,
    must_change_password: false,
    ...over,
  };
}

const directory: Account[] = [
  account(),
  account({
    id: "2",
    username: "bea",
    auth_source: "LDAP",
    display_name: null,
    email: null,
    roles: [],
    last_login_at: null,
  }),
  account({
    id: "3",
    username: "cy",
    roles: ["viewer"],
    disabled: true,
    disabled_at: "2026-10-01T00:00:00Z",
  }),
  account({
    id: "4",
    username: "dee",
    roles: ["analyst"],
    locked_until: "2999-01-01T00:00:00Z",
  }),
  account({
    id: "5",
    username: "eve",
    roles: ["retired"],
    disabled: true,
    locked_until: "2999-01-01T00:00:00Z",
  }),
];

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.listUsers.mockResolvedValue(directory);
  api.listRoles.mockResolvedValue(roles);
});

describe("roleChoices", () => {
  it("lists whatever the backend returns, and keeps a role the list no longer has", () => {
    expect(roleChoices(roles, ["retired", "admin"])).toEqual([
      "admin",
      "analyst",
      "viewer",
      "detection_engineer",
      "retired",
    ]);
  });
});

describe("UsersPage", () => {
  it("shows local and directory accounts, a locked one, and a disabled one", async () => {
    render(<UsersPage />);
    const table = await screen.findByRole("table");
    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    const ada = within(table).getByRole("row", { name: /ada/ });
    expect(ada).toHaveTextContent("LOCAL");
    expect(ada).toHaveTextContent("Ada Lovelace");
    expect(ada).toHaveTextContent("active");
    expect(ada).toHaveTextContent("2026-10-15 12:00:00 UTC");
    const bea = within(table).getByRole("row", { name: /bea/ });
    expect(bea).toHaveTextContent("LDAP");
    expect(bea).toHaveTextContent("—");
    expect(within(table).getByRole("row", { name: /cy/ })).toHaveTextContent("disabled");
    const dee = within(table).getByRole("row", { name: /dee/ });
    expect(dee).toHaveTextContent("locked");
    const eve = within(table).getByRole("row", { name: /eve/ });
    expect(eve).toHaveTextContent("disabled");
    expect(eve).toHaveTextContent("locked");
  });

  it("says when no accounts are stored", async () => {
    api.listUsers.mockResolvedValue([]);
    render(<UsersPage />);
    expect(await screen.findByText("No accounts are stored.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Create an account" })).toBeInTheDocument();
  });

  it("says when the list could not be loaded, and retries", async () => {
    api.listUsers.mockRejectedValueOnce(new ApiError(500, "database down", "database down"));
    render(<UsersPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Users could not be loaded.");
    expect(screen.getByRole("alert")).toHaveTextContent("database down");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    api.listUsers.mockResolvedValue([]);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No accounts are stored.")).toBeInTheDocument();
  });

  it("says when the caller cannot administer users", async () => {
    api.listUsers.mockRejectedValue(new ApiError(403, "Missing permission: admin:users", "Missing permission: admin:users"));
    render(<UsersPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role cannot administer users (permission admin:users).");
    expect(alert).toHaveTextContent("Missing permission: admin:users");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create an account" })).not.toBeInTheDocument();
  });

  it("creates a local user, shows the password once, and stores nothing", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    api.createUser.mockImplementation(async (body: UserDraft) =>
      account({
        id: "9",
        username: body.username,
        auth_source: body.auth_source,
        display_name: body.display_name,
        email: body.email,
        roles: body.roles,
        must_change_password: true,
        last_login_at: null,
      }),
    );
    const user = userEvent.setup();
    render(<UsersPage />);
    const form = await screen.findByRole("form", { name: "Create an account" });
    await user.type(within(form).getByLabelText("Username"), "faye");
    await user.type(within(form).getByLabelText("Display name"), "Faye");
    await user.type(within(form).getByLabelText("Initial password"), "Correct-Horse-1");
    await user.click(within(form).getByRole("checkbox", { name: "detection_engineer" }));
    await user.click(within(form).getByRole("button", { name: "Create account" }));
    expect(api.createUser).toHaveBeenCalledWith({
      username: "faye",
      auth_source: "LOCAL",
      email: null,
      display_name: "Faye",
      roles: ["detection_engineer"],
      password: "Correct-Horse-1",
    });
    expect(
      await screen.findByText(
        "Created local user faye. The account must change the password at the next sign-in.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Correct-Horse-1")).toBeInTheDocument();
    expect(within(form).getByLabelText("Initial password")).toHaveValue("");
    expect(setItem).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Dismiss the password" }));
    expect(screen.queryByText("Correct-Horse-1")).not.toBeInTheDocument();
  });

  it("pre-creates an LDAP user with no password", async () => {
    api.createUser.mockImplementation(async (body: UserDraft) =>
      account({ id: "8", username: body.username, auth_source: "LDAP", roles: body.roles, display_name: null, email: null, last_login_at: null }),
    );
    const user = userEvent.setup();
    render(<UsersPage />);
    const form = await screen.findByRole("form", { name: "Create an account" });
    await user.click(within(form).getByRole("radio", { name: "LDAP" }));
    expect(within(form).queryByLabelText("Initial password")).not.toBeInTheDocument();
    await user.type(within(form).getByLabelText("Username"), "gina");
    await user.click(within(form).getByRole("button", { name: "Create account" }));
    expect(api.createUser.mock.calls[0][0].password).toBeUndefined();
    expect(api.createUser.mock.calls[0][0].auth_source).toBe("LDAP");
    expect(
      await screen.findByText("Pre-created LDAP user gina. The account has no password here."),
    ).toBeInTheDocument();
  });

  it("lists every reason a create was refused", async () => {
    api.createUser.mockRejectedValue(
      new ApiError(422, "Unprocessable", ["A local user needs an initial password.", "too short"]),
    );
    const user = userEvent.setup();
    render(<UsersPage />);
    const form = await screen.findByRole("form", { name: "Create an account" });
    await user.type(within(form).getByLabelText("Username"), "faye");
    await user.type(within(form).getByLabelText("Initial password"), "short");
    await user.click(within(form).getByRole("button", { name: "Create account" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The account was not created.");
    expect(alert).toHaveTextContent("A local user needs an initial password.");
    expect(alert).toHaveTextContent("too short");
  });

  it("sets the roles the backend lists, including one that is no longer listed", async () => {
    api.setUserRoles.mockImplementation(async (_id: string, next: string[]) =>
      account({ id: "5", username: "eve", roles: next, disabled: true, locked_until: "2999-01-01T00:00:00Z" }),
    );
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Manage eve" }));
    const actions = screen.getByRole("region", { name: "Actions for eve" });
    expect(within(actions).getByRole("checkbox", { name: "retired" })).toBeChecked();
    expect(within(actions).getByRole("checkbox", { name: "detection_engineer" })).not.toBeChecked();
    await user.click(within(actions).getByRole("checkbox", { name: "detection_engineer" }));
    await user.click(within(actions).getByRole("button", { name: "Save roles for eve" }));
    expect(api.setUserRoles).toHaveBeenCalledWith("5", ["retired", "detection_engineer"]);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Roles for eve are now detection_engineer, retired.",
    );
  });

  it("shows the last-admin refusal as the backend says it", async () => {
    const text = "the last active user holding admin:* cannot be disabled or lose that role";
    api.disableUser.mockRejectedValue(new ApiError(409, text, text));
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Manage ada" }));
    await user.click(screen.getByRole("button", { name: "Disable ada" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The account was not changed.");
    expect(alert).toHaveTextContent(text);
  });

  it("enables, unlocks, and says so", async () => {
    api.enableUser.mockResolvedValue(account({ id: "3", username: "cy", roles: ["viewer"], disabled: false }));
    api.unlockUser.mockResolvedValue(account({ id: "4", username: "dee", roles: ["analyst"], locked_until: null }));
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Manage cy" }));
    await user.click(screen.getByRole("button", { name: "Enable cy" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Enabled cy.");
    await user.click(screen.getByRole("button", { name: "Manage dee" }));
    await user.click(screen.getByRole("button", { name: "Unlock dee" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Unlocked dee.");
  });

  it("resets a local password, shows it once, and refuses a mismatch or an LDAP account", async () => {
    api.resetUserPassword.mockResolvedValue(account({ must_change_password: true }));
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Manage bea" }));
    expect(screen.getByRole("region", { name: "Actions for bea" })).toHaveTextContent(
      "An LDAP account has no password here.",
    );
    expect(screen.queryByRole("form", { name: "Reset password" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Manage ada" }));
    const reset = screen.getByRole("form", { name: "Reset password" });
    await user.type(within(reset).getByLabelText("New password"), "one-password");
    await user.type(within(reset).getByLabelText("Confirm new password"), "other-password");
    await user.click(within(reset).getByRole("button", { name: "Reset password for ada" }));
    expect(api.resetUserPassword).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
    await user.clear(within(reset).getByLabelText("Confirm new password"));
    await user.type(within(reset).getByLabelText("Confirm new password"), "one-password");
    await user.click(within(reset).getByRole("button", { name: "Reset password for ada" }));
    expect(api.resetUserPassword).toHaveBeenCalledWith("1", "one-password");
    expect(
      await screen.findByText("Reset the password for ada. The account must change it at the next sign-in."),
    ).toBeInTheDocument();
    expect(screen.getByText("one-password")).toBeInTheDocument();
    expect(within(reset).getByLabelText("New password")).toHaveValue("");
  });

  it("shows a reset refusal as sent", async () => {
    api.resetUserPassword.mockRejectedValue(
      new ApiError(422, "Unprocessable", ["Only a local account can have its password reset here."]),
    );
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Manage ada" }));
    const reset = screen.getByRole("form", { name: "Reset password" });
    await user.type(within(reset).getByLabelText("New password"), "Correct-Horse-1");
    await user.type(within(reset).getByLabelText("Confirm new password"), "Correct-Horse-1");
    await user.click(within(reset).getByRole("button", { name: "Reset password for ada" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only a local account can have its password reset here.",
    );
  });

  it("says when the role list could not be loaded", async () => {
    api.listRoles.mockRejectedValue(new ApiError(500, "roles down", "roles down"));
    render(<UsersPage />);
    expect(await screen.findByRole("table")).toBeInTheDocument();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Roles could not be loaded. Creating now assigns no role.");
    expect(alert).toHaveTextContent("roles down");
  });
});

describe("users route states", () => {
  it("names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading users");
  });

  it("names the failure without the exception", async () => {
    const reset = vi.fn();
    render(<UsersError error={Object.assign(new Error("secret stack"), { digest: "ref-2" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Users could not be displayed.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret stack");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
