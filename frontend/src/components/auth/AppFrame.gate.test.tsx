import { render, screen } from "@testing-library/react";
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

const client = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("./client", () => client);

function frame() {
  return render(
    <SessionProvider>
      <AppFrame>
        <p>page body</p>
      </AppFrame>
    </SessionProvider>,
  );
}

// A gate state sits in the same centred column as the sign-in form, and the notice is the
// column's only child, so it is sized by its text and not stretched by the viewport (POL-8).
function expectInColumn(notice: HTMLElement) {
  const column = notice.parentElement as HTMLElement;
  expect(column.className).toMatch(/column/);
  expect(column.children).toHaveLength(1);
  expect((column.parentElement as HTMLElement).className).toMatch(/gate/);
}

beforeEach(() => {
  setTestLocale("en");
  client.fetchSession.mockReset();
  client.logout.mockReset();
});

describe("the session gate is sized to its content", () => {
  it("puts the 429 sentence and the retry button in the column", async () => {
    client.fetchSession.mockRejectedValue(
      new ApiError(429, "rate limit exceeded", "rate limit exceeded", undefined, 60),
    );
    frame();
    const alert = await screen.findByRole("alert");
    expectInColumn(alert);
    expect(alert).toHaveTextContent("Too many requests came from this address within a minute.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("puts the generic failure in the column with its text unchanged", async () => {
    client.fetchSession.mockRejectedValue(new ApiError(500, "boom"));
    frame();
    const alert = await screen.findByRole("alert");
    expectInColumn(alert);
    expect(alert).toHaveTextContent("The session could not be read.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("puts the sign-in-required notice in the column", async () => {
    client.fetchSession.mockRejectedValue(new ApiError(401, "Authentication required."));
    frame();
    const notice = await screen.findByRole("status");
    expectInColumn(notice);
    expect(notice).toHaveTextContent("Sign-in is required.");
  });
});
