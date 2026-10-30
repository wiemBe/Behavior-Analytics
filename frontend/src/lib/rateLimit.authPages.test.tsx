import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DirectoryPage } from "@/app/admin/directory/DirectoryPage";
import { UsersPage } from "@/app/admin/users/UsersPage";
import { ApiError } from "@/lib/api";
import { setTestLocale } from "../../vitest.setup";

// POL-6: the users and directory loads are client reads. A 429 shows rateLimitedText, checked
// before the 403 and the shared load-failed lines, with and without Retry-After, in en and tr.

const auth = vi.hoisted(() => ({
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
}));
vi.mock("@/components/auth/client", () => auth);

const limited = (seconds: number | null) =>
  new ApiError(429, "rate limit exceeded", "rate limit exceeded", undefined, seconds);

const EN = "Too many requests came from this address within a minute.";
const EN_WAIT = "Try again in 12 s.";
const EN_MINUTE = "Try again in a minute.";
const TR = "Bu adresten bir dakika içinde çok fazla istek geldi.";
const TR_WAIT = "12 sn sonra yeniden deneyin.";
const TR_MINUTE = "Bir dakika sonra yeniden deneyin.";

beforeEach(() => {
  vi.clearAllMocks();
  setTestLocale("en");
  auth.listRoles.mockResolvedValue([]);
});

const pages: Array<{ name: string; show: () => void; arm: (err: ApiError) => void }> = [
  {
    name: "users",
    show: () => {
      render(<UsersPage />);
    },
    arm: (err) => {
      auth.listUsers.mockRejectedValue(err);
    },
  },
  {
    name: "directory",
    show: () => {
      render(<DirectoryPage />);
    },
    arm: (err) => {
      auth.getDirectory.mockRejectedValue(err);
    },
  },
];

describe.each(pages)("$name", (page) => {
  it("says rate limited with the wait, in English", async () => {
    page.arm(limited(12));
    page.show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(EN);
    expect(alert).toHaveTextContent(EN_WAIT);
    expect(alert).not.toHaveTextContent(/rate limit exceeded|unreachable/i);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says in a minute without Retry-After, in English", async () => {
    page.arm(limited(null));
    page.show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(EN);
    expect(alert).toHaveTextContent(EN_MINUTE);
    expect(alert).not.toHaveTextContent(/rate limit exceeded|unreachable/i);
  });

  it("says rate limited with the wait, in Turkish", async () => {
    setTestLocale("tr");
    page.arm(limited(12));
    page.show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(TR);
    expect(alert).toHaveTextContent(TR_WAIT);
    expect(alert).not.toHaveTextContent(/erişilemiyor/i);
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
  });

  it("says in a minute without Retry-After, in Turkish", async () => {
    setTestLocale("tr");
    page.arm(limited(null));
    page.show();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(TR);
    expect(alert).toHaveTextContent(TR_MINUTE);
    expect(alert).not.toHaveTextContent(/erişilemiyor/i);
  });
});
