import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminPage from "./page";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, api, type CallerInfo } from "@/lib/api";
import { consoleOf, decision } from "@/components/history/fixtures";

// The hub is an index. With no session it fails closed and lists the five
// ungated destinations. Users and Directory appear only for a ready session
// that holds admin:users or admin:auth. The tests pin the heading, the
// description, the order, and the absence of the stale "Phase N" wording.

const SECTIONS = [
  {
    name: "Search cadence",
    href: "/admin/search-cadence",
    description: "How often each search family queries QRadar",
  },
  {
    name: "Classification rules",
    href: "/admin/classification-rules",
    description: "Which rules decide a log source's class, and in what order",
  },
  {
    name: "Outage alerts",
    href: "/admin/outage-policy",
    description: "Which silent sources raise an alert, and which are only recorded",
  },
  {
    name: "WAF sources",
    href: "/admin/waf-sources",
    description: "Which QRadar properties each WAF source is read from",
  },
  {
    name: "IPS sources",
    href: "/admin/ips-sources",
    description: "Which QRadar properties each IPS source is read from",
  },
];

function adminSections() {
  return screen.getByRole("list", { name: "Administration sections" });
}

function caller(permissions: string[]): CallerInfo {
  return {
    subject: "ada",
    email: "ada@example.com",
    permissions,
    effective_permissions: permissions,
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["admin"],
  };
}

function ready(permissions: string[]): SessionValue {
  return {
    status: "ready",
    me: caller(permissions),
    error: null,
    reload: vi.fn(),
    signOut: vi.fn(),
  };
}

function names(): string[] {
  return within(adminSections())
    .getAllByRole("link")
    .map((link) => link.textContent ?? "");
}

beforeEach(() => {
  // The notice reads the history-backfill status for a caller who may read it.
  vi.spyOn(api, "historyBackfill").mockResolvedValue([]);
});

describe("administration hub", () => {
  it("has exactly one h1, named Administration, and states what the page controls", () => {
    render(<AdminPage />);
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Administration");
    expect(
      screen.getByText(
        "Settings that control collection, source classification and outage alerting.",
      ),
    ).toBeInTheDocument();
  });

  it("lists exactly five sections while the session is unknown, in order, each a link", () => {
    render(<AdminPage />);
    const links = within(adminSections()).getAllByRole("link");
    expect(links).toHaveLength(5);
    expect(links.map((a) => a.textContent)).toEqual(SECTIONS.map((s) => s.name));
  });

  it("points each section at its own destination", () => {
    render(<AdminPage />);
    const links = within(adminSections()).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(SECTIONS.map((s) => s.href));
  });

  it("gives each section its one-line description", () => {
    render(<AdminPage />);
    const items = within(adminSections()).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    SECTIONS.forEach((section, i) => {
      expect(within(items[i]).getByText(section.description)).toBeInTheDocument();
    });
  });

  it("carries no stale phase wording", () => {
    render(<AdminPage />);
    expect(document.body.textContent ?? "").not.toMatch(/Phase \d/);
  });

  it("adds Users only for admin:users", () => {
    render(
      <StaticSession value={ready(["admin:users"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(names()).toEqual([...SECTIONS.map((section) => section.name), "Users"]);
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/admin/users");
    expect(
      screen.getByText("Who can sign in, and the roles an administrator assigned"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Directory" })).not.toBeInTheDocument();
  });

  it("adds Directory only for admin:auth", () => {
    render(
      <StaticSession value={ready(["admin:auth"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(names()).toEqual([...SECTIONS.map((section) => section.name), "Directory"]);
    expect(screen.getByRole("link", { name: "Directory" })).toHaveAttribute("href", "/admin/directory");
    expect(
      screen.getByText("Where directory accounts bind, and a connection test that stores nothing"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("adds both when the caller holds admin:*", () => {
    render(
      <StaticSession value={ready(["admin:*"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(names()).toEqual([
      ...SECTIONS.map((section) => section.name),
      "History backfill",
      "Users",
      "Directory",
    ]);
  });

  it("adds History backfill only for read:history_backfill, which read:* and admin:* grant", () => {
    const { unmount } = render(
      <StaticSession value={ready(["read:history_backfill"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(names()).toEqual([...SECTIONS.map((section) => section.name), "History backfill"]);
    expect(screen.getByRole("link", { name: "History backfill" })).toHaveAttribute(
      "href",
      "/admin/history-backfill",
    );
    unmount();
    render(
      <StaticSession value={ready(["read:*"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(names()).toContain("History backfill");
  });

  it("hides History backfill from an account that cannot read it, and from an unknown session", () => {
    const { unmount } = render(
      <StaticSession value={ready(["admin:users"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(screen.queryByRole("link", { name: "History backfill" })).not.toBeInTheDocument();
    unmount();
    render(<AdminPage />);
    expect(screen.queryByRole("link", { name: "History backfill" })).not.toBeInTheDocument();
  });

  it("adds neither for a read permission", () => {
    render(
      <StaticSession value={ready(["read:offenses"])}>
        <AdminPage />
      </StaticSession>,
    );
    expect(names()).toEqual(SECTIONS.map((section) => section.name));
  });
});

describe("administration hub: the history-backfill notice", () => {
  const NOTICE = "History backfill is not decided.";

  function renderFor(permissions: string[]) {
    return render(
      <StaticSession value={ready(permissions)}>
        <AdminPage />
      </StaticSession>,
    );
  }

  it("says so, with a link, while a console is undecided and the caller can read it", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    renderFor(["read:history_backfill"]);
    expect(await screen.findByText(NOTICE)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open History backfill" })).toHaveAttribute(
      "href",
      "/admin/history-backfill",
    );
  });

  it("is silent once every console has decided", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockResolvedValue([
      consoleOf({ decision: decision(), mode: "ACTIVE" }),
    ]);
    renderFor(["read:history_backfill"]);
    await waitFor(() => expect(read).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(NOTICE)).not.toBeInTheDocument());
  });

  it("is never asked for, and never shown, to a caller who cannot read it", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    renderFor(["admin:users"]);
    render(<AdminPage />);
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });

  it("shows nothing when the status cannot be read", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockRejectedValue(new ApiError(500, "boom"));
    renderFor(["read:history_backfill"]);
    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
