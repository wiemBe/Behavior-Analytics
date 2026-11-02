import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ recordGuideSeen: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  recordGuideSeen: api.recordGuideSeen,
}));

import { StaticSession, type SessionValue } from "@/components/auth/session";
import type { CallerInfo } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { GuideLink } from "./GuideLink";

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

function session(over: Partial<SessionValue> = {}): SessionValue {
  return {
    status: "ready",
    me: me([]),
    error: null,
    reload: async () => {},
    signOut: async () => null,
    ...over,
  };
}

function inSession(value: SessionValue, section = "rules") {
  return (
    <StaticSession value={value}>
      <GuideLink section={section} />
    </StaticSession>
  );
}

beforeEach(() => {
  api.recordGuideSeen.mockReset();
  api.recordGuideSeen.mockResolvedValue(undefined);
});

describe("the page-header guide link shows once per account", () => {
  it("shows while the section is unseen, and records it exactly once", () => {
    const { rerender } = render(inSession(session()));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toHaveAttribute("href", "/guide/rules");
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(1);
    expect(api.recordGuideSeen).toHaveBeenCalledWith("rules");
    // Re-renders (the session object changes on every provider update) do not record again.
    rerender(inSession(session()));
    rerender(inSession(session({ me: me(["alerts"]) })));
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(1);
  });

  it("does not record again when the session's note function changes identity", () => {
    const { rerender } = render(inSession(session({ noteGuideSeen: () => {} })));
    rerender(inSession(session({ noteGuideSeen: () => {} })));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(1);
  });

  it("records once under strict mode", () => {
    render(<StrictMode>{inSession(session())}</StrictMode>);
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(1);
  });

  it("is hidden when the section is already seen, and records nothing", () => {
    render(inSession(session({ me: me(["alerts", "rules"]) })));
    expect(screen.queryByRole("link")).toBeNull();
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
  });

  it("stays for the rest of this page view once the session says it was seen", () => {
    const { rerender } = render(inSession(session()));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    rerender(inSession(session({ me: me(["rules"]) })));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    // The next visit mounts the page again: now it is gone.
    const next = render(inSession(session({ me: me(["rules"]) })));
    expect(next.container.querySelector("a")).toBeNull();
  });

  it("tells the session so a later page in the same session starts hidden", () => {
    const noteGuideSeen = vi.fn();
    render(inSession(session({ noteGuideSeen })));
    expect(noteGuideSeen).toHaveBeenCalledWith("rules");
    expect(noteGuideSeen).toHaveBeenCalledTimes(1);
  });

  it("shows nothing while the session is still being read, then decides", () => {
    const { rerender } = render(inSession(session({ status: "loading", me: null })));
    expect(screen.queryByRole("link")).toBeNull();
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
    rerender(inSession(session({ me: me(["rules"]) })));
    expect(screen.queryByRole("link")).toBeNull();
    const { container } = render(inSession(session({ status: "loading", me: null })));
    expect(container.querySelector("a")).toBeNull();
  });

  it("always shows, and records nothing, when the account cannot store it (null)", () => {
    render(inSession(session({ me: me(null) })));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
  });

  it("treats a backend that omits the list like null", () => {
    render(inSession(session({ me: me(undefined) })));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
  });

  it("shows with no session provider at all, and records nothing", () => {
    render(<GuideLink section="rules" />);
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
  });

  it("never shows or records a failed save: the link stays and nothing is announced", async () => {
    const failure = Promise.reject(new Error("network down"));
    failure.catch(() => {
      /* keeps this test's own promise from being reported; the spy below proves the link's handler */
    });
    const handled = vi.spyOn(failure, "catch");
    api.recordGuideSeen.mockReturnValue(failure);
    const { container } = render(inSession(session()));
    await act(async () => {
      await Promise.resolve();
    });
    expect(handled).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.textContent).toBe("Guide");
  });

  it("neither shows nor records a section the guide does not have", () => {
    render(inSession(session(), "no-such-section"));
    expect(screen.queryByRole("link")).toBeNull();
    expect(api.recordGuideSeen).not.toHaveBeenCalled();
  });

  it("decides each section on its own when a page changes section", () => {
    const { rerender } = render(inSession(session({ me: me(["alerts"]) }), "alerts"));
    expect(screen.queryByRole("link")).toBeNull();
    rerender(inSession(session({ me: me(["alerts"]) }), "rules"));
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    expect(api.recordGuideSeen).toHaveBeenCalledTimes(1);
    expect(api.recordGuideSeen).toHaveBeenCalledWith("rules");
  });
});

describe("PageHeader carries the same rule", () => {
  it("renders the link on a first visit and the title only on the second", () => {
    const first = render(
      <StaticSession value={session()}>
        <PageHeader title="Rules" guide="rules" />
      </StaticSession>,
    );
    expect(screen.getByRole("link", { name: "Guide: Rules" })).toBeInTheDocument();
    first.unmount();
    render(
      <StaticSession value={session({ me: me(["rules"]) })}>
        <PageHeader title="Rules" guide="rules" />
      </StaticSession>,
    );
    expect(screen.getByRole("heading", { name: "Rules" })).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
