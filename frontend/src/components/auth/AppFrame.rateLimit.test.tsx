import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import { AppFrame } from "./AppFrame";
import { SessionProvider } from "./session";
import { ApiError } from "@/lib/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/alerts",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  return {
    ...actual,
    shellApi: {
      healthReady: vi.fn().mockResolvedValue({ status: "ready", database: "ok" }),
      overview: vi.fn().mockResolvedValue({
        instance_status: "HEALTHY",
        instance_version: "7.5",
        alerts: { open: 0, acknowledged: 0, by_severity: {} },
      }),
      searchCadence: vi.fn().mockResolvedValue([]),
      version: vi.fn().mockResolvedValue({ version: "0.1.0", git_sha: null, migration_head: null }),
    },
  };
});

const client = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("./client", () => client);

const EN = "Too many requests came from this address within a minute.";
const EN_WAIT = "Try again in 60 s.";
const EN_MINUTE = "Try again in a minute.";
const TR = "Bu adresten bir dakika içinde çok fazla istek geldi.";
const TR_WAIT = "60 sn sonra yeniden deneyin.";
const TR_MINUTE = "Bir dakika sonra yeniden deneyin.";
const OUTAGE = "If it keeps failing, the backend may be unreachable.";
const UNCONFIRMED = "Nothing is shown until the account can be confirmed.";

function limited(seconds: number | null) {
  return new ApiError(429, "rate limit exceeded", "rate limit exceeded", undefined, seconds);
}

function frame() {
  return render(
    <SessionProvider>
      <AppFrame>
        <p>page body</p>
      </AppFrame>
    </SessionProvider>,
  );
}

beforeEach(() => {
  setTestLocale("en");
  client.fetchSession.mockReset();
  client.logout.mockReset();
});

describe("the session gate on a 429", () => {
  it.each([
    ["en", 60, EN, EN_WAIT, "Try again"],
    ["en", null, EN, EN_MINUTE, "Try again"],
    ["tr", 60, TR, TR_WAIT, "Tekrar dene"],
    ["tr", null, TR, TR_MINUTE, "Tekrar dene"],
  ] as const)(
    "says the address is limited in %s when Retry-After is %s",
    async (locale, seconds, lead, wait, again) => {
      setTestLocale(locale);
      client.fetchSession.mockRejectedValue(limited(seconds));
      frame();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(lead);
      expect(alert).toHaveTextContent(wait);
      expect(alert).not.toHaveTextContent("The session could not be read.");
      expect(alert).not.toHaveTextContent("Oturum okunamadı.");
      expect(alert).not.toHaveTextContent("rate limit exceeded");
      expect(alert).not.toHaveTextContent(OUTAGE);
      expect(alert).not.toHaveTextContent("erişilemiyor");
      expect(alert).not.toHaveTextContent(UNCONFIRMED);
      expect(screen.queryByText("page body")).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: again }));
      expect(client.fetchSession).toHaveBeenCalledTimes(2);
    },
  );

  it("keeps today's text for a 500", async () => {
    client.fetchSession.mockRejectedValue(new ApiError(500, "boom"));
    frame();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The session could not be read.");
    expect(alert).toHaveTextContent(
      "Nothing is shown until the account can be confirmed. The server said: boom. Try again. If it keeps failing, the backend may be unreachable.",
    );
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps today's text for a network failure", async () => {
    client.fetchSession.mockRejectedValue(new Error("socket hang up"));
    frame();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The session could not be read.");
    expect(alert).toHaveTextContent(
      "Nothing is shown until the account can be confirmed. Try again. If it keeps failing, the backend may be unreachable.",
    );
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
  });

  it("still shows sign-in when /me answers 401", async () => {
    client.fetchSession.mockRejectedValue(new ApiError(401, "Authentication required."));
    frame();
    expect(await screen.findByText("Sign-in is required.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.queryByText(EN)).not.toBeInTheDocument();
  });
});
