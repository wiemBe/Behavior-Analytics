import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession } from "./session";
import { PageHeader } from "@/components/ui/PageHeader";
import type { CallerInfo } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/rules",
  useSearchParams: () => new URLSearchParams(),
}));

const client = vi.hoisted(() => ({ fetchSession: vi.fn(), logout: vi.fn() }));
vi.mock("./client", () => client);

const api = vi.hoisted(() => ({ recordGuideSeen: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  recordGuideSeen: api.recordGuideSeen,
}));

function me(guide_seen: string[] | null | undefined): CallerInfo {
  return {
    subject: "ada",
    email: null,
    permissions: ["admin:*"],
    effective_permissions: [],
    username: "ada",
    auth_source: "LOCAL",
    guide_seen,
  };
}

// Two pages the analyst moves between in one session: the shell and its provider stay mounted.
function Pages() {
  const [page, setPage] = useState<"rules" | "alerts">("rules");
  const session = useSession();
  return (
    <div>
      <button type="button" onClick={() => setPage("alerts")}>to alerts</button>
      <button type="button" onClick={() => setPage("rules")}>to rules</button>
      <button type="button" onClick={() => void session.reload()}>reload</button>
      <button type="button" onClick={() => void session.signOut()}>sign out</button>
      <p data-testid="seen">{JSON.stringify(session.me?.guide_seen)}</p>
      {session.status === "ready" ? <PageHeader key={page} title={page} guide={page} /> : null}
    </div>
  );
}

function app() {
  return (
    <SessionProvider>
      <Pages />
    </SessionProvider>
  );
}

beforeEach(() => {
  client.fetchSession.mockReset();
  client.logout.mockReset();
  api.recordGuideSeen.mockReset();
  api.recordGuideSeen.mockResolvedValue(undefined);
});

describe("the guide link across pages of one session", () => {
  it("shows on the first visit, is gone on the return, and is recorded once per section", async () => {
    // The server never changes its answer here: it is the provider that must remember.
    client.fetchSession.mockResolvedValue(me([]));
    render(app());
    expect(await screen.findByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "to alerts" }));
    expect(await screen.findByRole("link", { name: "Guide: Alerts" })).toBeInTheDocument();
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(2);

    await userEvent.click(screen.getByRole("button", { name: "to rules" }));
    expect(screen.getByRole("heading", { name: "rules" })).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("seen")).toHaveTextContent('["rules","alerts"]');
  });

  it("keeps a section shown in this session when a slower /me answer lacks it", async () => {
    client.fetchSession.mockResolvedValue(me([]));
    render(app());
    expect(await screen.findByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    client.fetchSession.mockResolvedValue(me(["alerts"]));
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(screen.getByTestId("seen")).toHaveTextContent('["alerts","rules"]');
    // The current view keeps its link even so.
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
  });

  it("leaves a null list null: an account that cannot store it always shows the link", async () => {
    client.fetchSession.mockResolvedValue(me(null));
    render(app());
    expect(await screen.findByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "to alerts" }));
    await userEvent.click(screen.getByRole("button", { name: "to rules" }));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(screen.getByTestId("seen")).toHaveTextContent("null");
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
  });

  it("forgets the session's marks when the account signs out", async () => {
    client.fetchSession.mockResolvedValue(me([]));
    client.logout.mockResolvedValue(undefined);
    render(app());
    expect(await screen.findByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    // Another account signs in on the same provider: its own list is the only truth.
    client.fetchSession.mockResolvedValue(me([]));
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    // The new account has not been shown it: the link comes back and is recorded for them.
    expect(await screen.findByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(2);
  });
});
