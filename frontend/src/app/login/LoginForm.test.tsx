import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./LoginForm";
import Loading from "./loading";
import LoginError from "./error";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, sessionNavigation } from "@/lib/api";
import { enableTurkish } from "../../../vitest.setup";

const nav = vi.hoisted(() => ({ replace: vi.fn(), params: "" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(nav.params),
}));

const client = vi.hoisted(() => ({ login: vi.fn() }));
vi.mock("@/components/auth/client", () => client);

function session(): SessionValue {
  return {
    status: "anonymous",
    me: null,
    error: null,
    reload: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn(),
  };
}

function signedIn(over: Partial<SessionValue["me"] & object> = {}): SessionValue {
  return {
    ...session(),
    status: "ready",
    me: {
      subject: "ada",
      email: null,
      permissions: [],
      effective_permissions: [],
      username: "ada",
      ...over,
    },
  };
}

function renderForm(value = session()) {
  return render(
    <StaticSession value={value}>
      <LoginForm />
    </StaticSession>,
  );
}

async function submit(username = "ada", password = "secret-value") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Username"), username);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  nav.replace.mockReset();
  nav.params = "";
  client.login.mockReset();
  document.cookie = "ba_locale=en; Path=/";
});

afterEach(() => {
  document.cookie = "ba_locale=; Path=/; Max-Age=0";
});

afterEach(() => vi.unstubAllGlobals());

describe("LoginForm when the account is already signed in", () => {
  it("sends it to the page next names, without showing the form", () => {
    nav.params = "next=%2Falerts%3Fstate%3DOPEN";
    renderForm(signedIn());
    expect(nav.replace).toHaveBeenCalledWith("/alerts?state=OPEN");
    expect(screen.queryByLabelText("Username")).toBeNull();
  });

  it("sends it to / without a next", () => {
    renderForm(signedIn());
    expect(nav.replace).toHaveBeenCalledWith("/");
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("refuses an off-site next", () => {
    nav.params = "next=https%3A%2F%2Fevil.example%2Fx";
    renderForm(signedIn());
    expect(nav.replace).toHaveBeenCalledTimes(1);
    expect(nav.replace).toHaveBeenCalledWith("/");
  });

  it("sends an account that must change its password there first", () => {
    nav.params = "next=%2Falerts";
    renderForm(signedIn({ must_change_password: true }));
    expect(nav.replace).toHaveBeenCalledWith("/account/password?next=%2Falerts");
  });

  it("shows the form to a signed-out visitor and does not navigate", () => {
    renderForm();
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("shows the form when the account could not be read", () => {
    renderForm({ ...session(), status: "error", error: "x" });
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("shows a loading line, not the form, while the session is being read", () => {
    renderForm({ ...session(), status: "loading" });
    expect(screen.queryByLabelText("Username")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/sign-in form/);
    expect(nav.replace).not.toHaveBeenCalled();
  });
});

describe("LoginForm", () => {
  it("shows the API version, or says it could not be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ version: "3.2.1", git_sha: null, migration_head: null }),
      })),
    );
    const { unmount } = renderForm();
    expect(await screen.findByText("API version 3.2.1")).toBeInTheDocument();
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("down"))));
    renderForm();
    expect(await screen.findByText("API version could not be read")).toBeInTheDocument();
  });

  it("is idle with one heading and one submit", () => {
    renderForm();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("Behavior Analytics")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("disables the form while the request is in flight", async () => {
    client.login.mockReturnValue(new Promise(() => {}));
    renderForm();
    await submit();
    expect(await screen.findByRole("button", { name: "Signing in" })).toBeDisabled();
    expect(screen.getByRole("form", { name: "Sign in" })).toHaveAttribute("aria-busy", "true");
  });

  it("shows a 401 as sent and does not guess which field was wrong", async () => {
    client.login.mockRejectedValue(
      new ApiError(401, "Invalid username or password", "Invalid username or password"),
    );
    renderForm();
    await submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid username or password");
    expect(alert.tagName).toBe("P");
    expect(alert).toHaveClass("banner", "crit");
    expect(alert.querySelector("li")).toBeNull();
    expect(alert).not.toHaveTextContent(/unknown user|wrong password|which/i);
  });

  it("says the directory could not be reached and that local accounts still work", async () => {
    client.login.mockRejectedValue(
      new ApiError(
        503,
        "The directory server is unavailable. Sign-in was not completed.",
        "The directory server is unavailable. Sign-in was not completed.",
      ),
    );
    renderForm();
    await submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The directory could not be reached. Local accounts still work.");
    expect(alert).toHaveTextContent("The directory server is unavailable. Sign-in was not completed.");
  });

  it("says rate limited on 429, once, without the server detail", async () => {
    client.login.mockRejectedValue(new ApiError(429, "rate limit exceeded", "rate limit exceeded"));
    renderForm();
    await submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Too many requests came from this address within a minute.");
    expect(alert).toHaveTextContent("Try again in a minute.");
    expect(alert).not.toHaveTextContent("rate limit exceeded");
    expect(alert).not.toHaveTextContent("Wait, then try again.");
  });

  it("goes to a safe return path and stores no token", async () => {
    nav.params = "next=/alerts";
    const value = session();
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: ["analyst"],
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    renderForm(value);
    await submit();
    expect(value.reload).toHaveBeenCalled();
    expect(nav.replace).toHaveBeenCalledWith("/alerts");
    expect(setItem).not.toHaveBeenCalled();
  });

  it("refuses an unsafe return path", async () => {
    nav.params = "next=https://evil.example/phish";
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: [],
    });
    renderForm();
    await submit();
    expect(nav.replace).toHaveBeenCalledWith("/");
  });

  it("sends a password that must change to the password page and keeps the return path", async () => {
    nav.params = "next=/alerts";
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: true,
      roles: ["admin"],
    });
    renderForm();
    await submit();
    expect(nav.replace).toHaveBeenCalledWith("/account/password?next=%2Falerts");
  });

  it("loads the first page as a document when the account language differs", async () => {
    enableTurkish();
    document.cookie = "ba_locale=tr; Path=/";
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    nav.params = "next=/alerts";
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: ["analyst"],
    });
    renderForm();
    await submit();
    expect(assign).toHaveBeenCalledWith("/alerts");
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("keeps a client navigation when Turkish is not enabled (D-41)", async () => {
    document.cookie = "ba_locale=tr; Path=/";
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    nav.params = "next=/alerts";
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: ["analyst"],
    });
    renderForm();
    await submit();
    expect(assign).not.toHaveBeenCalled();
    expect(nav.replace).toHaveBeenCalledWith("/alerts");
  });

  it("stays when the account cannot be read after sign-in", async () => {
    const value = session();
    value.reload = vi.fn().mockRejectedValue(new Error("down"));
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: [],
    });
    renderForm(value);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Signed in, but the account could not be read. Try again.",
    );
    expect(nav.replace).not.toHaveBeenCalled();
  });
});

describe("sign-in route states", () => {
  it("names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the sign-in form");
  });

  it("names the failure without the exception", async () => {
    const reset = vi.fn();
    render(<LoginError error={Object.assign(new Error("secret stack"), { digest: "ref-1" })} reset={reset} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Sign in could not be displayed.");
    expect(alert).not.toHaveTextContent("secret stack");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
