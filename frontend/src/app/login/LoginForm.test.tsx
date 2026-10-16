import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./LoginForm";
import Loading from "./loading";
import LoginError from "./error";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError } from "@/lib/api";

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
});

describe("LoginForm", () => {
  it("is idle with one heading and one submit", () => {
    renderForm();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
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

  it("tells the caller to wait on 429", async () => {
    client.login.mockRejectedValue(new ApiError(429, "rate limit exceeded", "rate limit exceeded"));
    renderForm();
    await submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Wait, then try again.");
    expect(alert).toHaveTextContent("rate limit exceeded");
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
