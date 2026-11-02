import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DirectoryPage } from "@/app/admin/directory/DirectoryPage";
import { UsersPage } from "@/app/admin/users/UsersPage";
import { LoginForm } from "@/app/login/LoginForm";
import { PasswordChange } from "@/components/auth/PasswordChange";
import { SignOutButton } from "@/components/auth/SignOutButton";
import { errorTexts } from "@/components/auth/reasons";
import { SessionProvider, StaticSession, type SessionValue } from "@/components/auth/session";
import { createTextTranslator } from "@/i18n/textTranslator";
import { ApiError } from "@/lib/api";
import errorsEn from "../../messages/en/errors.json";
import errorsTr from "../../messages/tr/errors.json";
import { setTestLocale } from "../../vitest.setup";

// POL-7: the auth and admin paths that went through errorTexts, the sign-in /me read after a
// successful login, and sign-out. A 429 is the rate-limit sentence (with Retry-After or "in a
// minute"), in en and tr, never the server's detail or an outage sentence. Other failures keep
// today's text.

const client = vi.hoisted(() => ({
  listUsers: vi.fn(),
  listRoles: vi.fn(),
  createUser: vi.fn(),
  setUserRoles: vi.fn(),
  disableUser: vi.fn(),
  enableUser: vi.fn(),
  unlockUser: vi.fn(),
  resetUserPassword: vi.fn(),
  getDirectory: vi.fn(),
  saveDirectory: vi.fn(),
  testDirectory: vi.fn(),
  login: vi.fn(),
  changePassword: vi.fn(),
  fetchSession: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("@/components/auth/client", () => client);

const nav = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/login",
  useSearchParams: () => new URLSearchParams(),
}));

const limited = (seconds: number | null) =>
  new ApiError(429, "rate limit exceeded", "rate limit exceeded", undefined, seconds);

const SENTENCE = {
  en: { head: "Too many requests came from this address within a minute.", wait: "Try again in 12 s.", minute: "Try again in a minute." },
  tr: { head: "Bu adresten bir dakika içinde çok fazla istek geldi.", wait: "12 sn sonra yeniden deneyin.", minute: "Bir dakika sonra yeniden deneyin." },
} as const;

const CASES = [
  { lang: "en", seconds: 12, tail: SENTENCE.en.wait },
  { lang: "en", seconds: null, tail: SENTENCE.en.minute },
  { lang: "tr", seconds: 12, tail: SENTENCE.tr.wait },
  { lang: "tr", seconds: null, tail: SENTENCE.tr.minute },
] as const;

// The number format may use a non-breaking space; the DOM matchers fold it, a plain string does not.
const flat = (text: string) => text.replace(/\s+/g, " ");

function expectSentence(node: HTMLElement, c: (typeof CASES)[number]) {
  expect(node).toHaveTextContent(SENTENCE[c.lang].head);
  expect(node).toHaveTextContent(c.tail);
  expect(node).not.toHaveTextContent(/rate limit exceeded|could not be read|okunamadı|unreachable/i);
}

const roles = [{ name: "admin", permissions: ["admin:*"] }];
const account = {
  id: "1",
  username: "ada",
  auth_source: "LOCAL",
  display_name: "Ada",
  email: "ada@example.com",
  roles: ["admin"],
  disabled: false,
  disabled_at: null,
  last_login_at: null,
  locked_until: null,
  must_change_password: false,
};
const directoryConfig = {
  enabled: true,
  urls: ["ldaps://dc1.example"],
  start_tls: false,
  ca_pem: null,
  bind_template: "cn={username},dc=example",
  timeout_seconds: 5,
  user_search_base: "ou=people,dc=example",
  version: 3,
  updated_by: "ada",
  updated_at: "2026-10-15T12:00:00Z",
};

beforeEach(() => {
  Object.values(client).forEach((fn) => fn.mockReset());
  nav.replace.mockReset();
  setTestLocale("en");
  document.cookie = "ba_locale=en; Path=/";
  client.listUsers.mockResolvedValue([account]);
  client.listRoles.mockResolvedValue(roles);
  client.getDirectory.mockResolvedValue(directoryConfig);
});

describe("errorTexts", () => {
  it.each(CASES)("is the sentence for a 429 in $lang with Retry-After $seconds", (c) => {
    const say = createTextTranslator(c.lang, { errors: c.lang === "en" ? errorsEn : errorsTr });
    const lines = errorTexts(limited(c.seconds), say, c.lang);
    expect(lines).toHaveLength(1);
    expect(flat(lines[0])).toContain(SENTENCE[c.lang].head);
    expect(flat(lines[0])).toContain(c.tail);
  });

  it("is English for a 429 when no translator is passed", () => {
    expect(flat(errorTexts(limited(12))[0])).toContain("Try again in 12 s.");
    expect(flat(errorTexts(limited(null))[0])).toContain("Try again in a minute.");
  });

  it("keeps a 500 detail and a non-API failure as they were", () => {
    expect(errorTexts(new ApiError(500, "boom", "boom"))).toEqual(["boom"]);
    expect(errorTexts(new Error("x"))).toEqual([
      "The request could not be sent. Check your connection and try again.",
    ]);
  });
});

describe("users", () => {
  describe.each(CASES)("roles read at 429 in $lang, Retry-After $seconds", (c) => {
    it("shows the sentence under the roles notice", async () => {
      setTestLocale(c.lang);
      client.listRoles.mockRejectedValue(limited(c.seconds));
      render(<UsersPage />);
      expect(await screen.findByRole("table")).toBeInTheDocument();
      expectSentence(await screen.findByRole("alert"), c);
    });
  });

  it("keeps the server detail for a roles 500", async () => {
    client.listRoles.mockRejectedValue(new ApiError(500, "roles down", "roles down"));
    render(<UsersPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("roles down");
  });

  it.each(CASES)("shows the sentence on a reset-password 429 in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.resetUserPassword.mockRejectedValue(limited(c.seconds));
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: c.lang === "en" ? "Manage ada" : /ada/ }));
    const reset = screen.getByRole("form", {
      name: c.lang === "en" ? "Reset password" : /Parola/,
    });
    const boxes = within(reset).getAllByLabelText(/password|parola/i, { selector: "input" });
    for (const box of boxes) await user.type(box, "Correct-Horse-1");
    await user.click(within(reset).getAllByRole("button").at(-1) as HTMLElement);
    expectSentence(await screen.findByRole("alert"), c);
  });

  it("keeps the server detail for a reset 422", async () => {
    client.resetUserPassword.mockRejectedValue(new ApiError(422, "Unprocessable", ["Only local."]));
    const user = userEvent.setup();
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "Manage ada" }));
    const reset = screen.getByRole("form", { name: "Reset password" });
    await user.type(within(reset).getByLabelText("New password"), "Correct-Horse-1");
    await user.type(within(reset).getByLabelText("Confirm new password"), "Correct-Horse-1");
    await user.click(within(reset).getByRole("button", { name: "Reset password for ada" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only local.");
  });
});

describe("directory", () => {
  it.each(CASES)("shows the sentence on a save 429 in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.saveDirectory.mockRejectedValue(limited(c.seconds));
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", {
      name: c.lang === "en" ? "Directory configuration" : /Dizin/,
    });
    await user.click(within(form).getAllByRole("button").at(-1) as HTMLElement);
    expect(client.saveDirectory).toHaveBeenCalled();
    expectSentence(await screen.findByRole("alert"), c);
  });

  it.each(CASES)("shows the sentence on a test-connection 429 in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.testDirectory.mockRejectedValue(limited(c.seconds));
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const test = await screen.findByRole("form", {
      name: c.lang === "en" ? "Test connection" : /Bağlantı/,
    });
    const boxes = within(test).getAllByRole("textbox").concat(
      Array.from(test.querySelectorAll<HTMLElement>("input[type=password]")),
    );
    for (const box of boxes) await user.type(box, "ada");
    await user.click(within(test).getAllByRole("button").at(-1) as HTMLElement);
    expectSentence(await screen.findByRole("alert"), c);
  });

  it("keeps the server detail for a save 409", async () => {
    client.saveDirectory.mockRejectedValue(new ApiError(409, "stale", "stale"));
    const user = userEvent.setup();
    render(<DirectoryPage />);
    const form = await screen.findByRole("form", { name: "Directory configuration" });
    await user.click(within(form).getByRole("button", { name: "Save directory" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("stale");
  });
});

function staticSession(): SessionValue {
  return {
    status: "anonymous",
    me: null,
    error: null,
    reload: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn(),
  };
}

async function signIn(lang: "en" | "tr") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(lang === "en" ? "Username" : /Kullanıcı/), "ada");
  await user.type(screen.getByLabelText(lang === "en" ? "Password" : /Parola/), "secret-value");
  const form = screen.getByRole("form");
  await user.click(within(form).getAllByRole("button").at(-1) as HTMLElement);
}

describe("sign-in", () => {
  it.each(CASES)("shows the sentence once on a login 429 in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.login.mockRejectedValue(limited(c.seconds));
    render(
      <StaticSession value={staticSession()}>
        <LoginForm />
      </StaticSession>,
    );
    await signIn(c.lang);
    const alert = await screen.findByRole("alert");
    expectSentence(alert, c);
    // No separate "wait, then try again" heading: the sentence already says it.
    expect(alert).not.toHaveTextContent(/Wait, then try again|Bekleyip tekrar/);
    expect((alert.textContent ?? "").split(SENTENCE[c.lang].head)).toHaveLength(2);
  });

  it.each(CASES)("shows the sentence when /me is 429 after sign-in in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: [],
    });
    const value = staticSession();
    value.reload = vi.fn().mockRejectedValue(limited(c.seconds));
    render(
      <StaticSession value={value}>
        <LoginForm />
      </StaticSession>,
    );
    await signIn(c.lang);
    const alert = await screen.findByRole("alert");
    expectSentence(alert, c);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("keeps the signed-in-but-unreadable text when /me fails otherwise", async () => {
    client.login.mockResolvedValue({
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: [],
    });
    const value = staticSession();
    value.reload = vi.fn().mockRejectedValue(new ApiError(500, "boom", "boom"));
    render(
      <StaticSession value={value}>
        <LoginForm />
      </StaticSession>,
    );
    await signIn("en");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Signed in, but the account could not be read. Try again.",
    );
  });

  it("keeps a 401 as the backend sent it", async () => {
    client.login.mockRejectedValue(new ApiError(401, "Invalid username or password", "Invalid username or password"));
    render(
      <StaticSession value={staticSession()}>
        <LoginForm />
      </StaticSession>,
    );
    await signIn("en");
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password");
  });
});

describe("password change", () => {
  const ready = (): SessionValue => ({
    ...staticSession(),
    status: "ready",
    me: {
      subject: "ada",
      email: "ada@example.com",
      permissions: ["admin:*"],
      effective_permissions: ["admin:*"],
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: ["admin"],
    },
  });

  it.each(CASES)("shows the sentence on a 429 in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.changePassword.mockRejectedValue(limited(c.seconds));
    const user = userEvent.setup();
    render(
      <StaticSession value={ready()}>
        <PasswordChange required={false} />
      </StaticSession>,
    );
    const form = screen.getByRole("form");
    const inputs = Array.from(form.querySelectorAll<HTMLElement>("input"));
    for (const input of inputs) await user.type(input, "new-password-1");
    await user.click(within(form).getAllByRole("button").at(-1) as HTMLElement);
    expect(client.changePassword).toHaveBeenCalled();
    expectSentence(await screen.findByRole("alert"), c);
  });

  it("keeps a wrong-current-password detail", async () => {
    client.changePassword.mockRejectedValue(new ApiError(401, "current password is wrong", "current password is wrong"));
    const user = userEvent.setup();
    render(
      <StaticSession value={ready()}>
        <PasswordChange required={false} />
      </StaticSession>,
    );
    const form = screen.getByRole("form");
    for (const input of Array.from(form.querySelectorAll<HTMLElement>("input"))) {
      await user.type(input, "new-password-1");
    }
    await user.click(within(form).getAllByRole("button").at(-1) as HTMLElement);
    expect(await screen.findByRole("alert")).toHaveTextContent("current password is wrong");
  });
});

describe("sign-out", () => {
  function mount() {
    client.fetchSession.mockResolvedValue({
      subject: "ada",
      email: "ada@example.com",
      permissions: [],
      effective_permissions: [],
      username: "ada",
      auth_source: "LOCAL",
      must_change_password: false,
      roles: [],
    });
    render(
      <SessionProvider>
        <SignOutButton />
      </SessionProvider>,
    );
  }

  it.each(CASES)("shows the sentence on a logout 429 in $lang, $seconds", async (c) => {
    setTestLocale(c.lang);
    client.logout.mockRejectedValue(limited(c.seconds));
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button"));
    expectSentence(await screen.findByRole("alert"), c);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("keeps the server message on a logout 500", async () => {
    client.logout.mockRejectedValue(new ApiError(500, "logout broke", "logout broke"));
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("logout broke");
  });
});
