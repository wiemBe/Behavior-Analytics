import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NoRole } from "./NoRole";
import { StaticSession, type SessionValue } from "./session";
import type { CallerInfo } from "@/lib/api";

const me: CallerInfo = {
  subject: "ada",
  email: null,
  permissions: [],
  effective_permissions: [],
  username: "ada",
  auth_source: "LDAP",
  must_change_password: false,
  roles: [],
};

describe("NoRole", () => {
  it("says the account exists, names where a role is assigned, and offers sign-out", async () => {
    const signOut = vi.fn().mockResolvedValue(null);
    const value: SessionValue = {
      status: "ready",
      me,
      error: null,
      reload: vi.fn(),
      signOut,
    };
    render(
      <StaticSession value={value}>
        <NoRole me={me} />
      </StaticSession>,
    );
    expect(screen.getByRole("heading", { name: "No role assigned" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("The account ada exists and has no role.");
    expect(screen.getByRole("status")).toHaveTextContent(
      "An administrator assigns roles under Administration → Users.",
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalledTimes(1);
  });
});
