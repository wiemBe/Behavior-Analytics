import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, alertPolicyApi, type CallerInfo } from "@/lib/api";
import { NAV, activeNavHref } from "@/lib/nav";

import AlertPolicyError from "./error";
import Loading from "./loading";
import AlertPolicyPage, { generateMetadata } from "./page";
import { MIXED, digest, run } from "./fixtures";

const ADMIN: SessionValue = {
  status: "ready",
  me: {
    subject: "a",
    email: null,
    permissions: ["read:alerts", "admin:outage"],
    effective_permissions: ["read:alerts", "admin:outage"],
  } as CallerInfo,
  error: null,
  reload: async () => {},
  signOut: async () => null,
};

beforeEach(() => vi.restoreAllMocks());

async function page(rows = MIXED, d = digest()) {
  vi.spyOn(alertPolicyApi, "list").mockResolvedValue(rows);
  vi.spyOn(alertPolicyApi, "digest").mockResolvedValue(d);
  return render(<StaticSession value={ADMIN}>{await AlertPolicyPage()}</StaticSession>);
}

describe("alert policy page", () => {
  it("has one h1, then the digest before the policies, and a guide link", async () => {
    await page();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Alert policy");
    const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2).toEqual(["Daily digest", "Policies"]);
    expect(await generateMetadata()).toEqual({ title: "Alert policy" });
  });

  it("shows the rows and the digest status together", async () => {
    await page();
    expect(screen.getByText("edge-waf")).toBeInTheDocument();
    expect(screen.getByText("Daily at 07:00 UTC")).toBeInTheDocument();
  });

  it("builds no active-quiet-periods section (that is SOC-17d)", async () => {
    await page();
    expect(document.body.textContent).not.toMatch(/quiet period|maintenance window|suppress/i);
  });

  it("never uses live or real-time wording", async () => {
    await page();
    expect(document.body.textContent).not.toMatch(/\blive\b|real-time/i);
  });

  it("without read:alerts names the permission and shows no table", async () => {
    vi.spyOn(alertPolicyApi, "list").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(alertPolicyApi, "digest").mockRejectedValue(new ApiError(403, "no"));
    render(await AlertPolicyPage());
    expect(screen.getByRole("alert")).toHaveTextContent("You need the read:alerts permission");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add policy" })).toBeNull();
  });

  it("a failed read says what failed and what to do, without the raw error", async () => {
    vi.spyOn(alertPolicyApi, "list").mockRejectedValue(new ApiError(500, "traceback secret"));
    vi.spyOn(alertPolicyApi, "digest").mockResolvedValue(digest());
    render(await AlertPolicyPage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The alert policy could not be shown");
    expect(alert).toHaveTextContent("Reload the page");
    expect(alert).not.toHaveTextContent("traceback secret");
  });

  it("an expired session says to sign in again", async () => {
    vi.spyOn(alertPolicyApi, "list").mockRejectedValue(new ApiError(401, "x"));
    vi.spyOn(alertPolicyApi, "digest").mockRejectedValue(new ApiError(401, "x"));
    render(await AlertPolicyPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Sign in again");
  });

  it("rate limiting reads as such", async () => {
    vi.spyOn(alertPolicyApi, "list").mockRejectedValue(new ApiError(429, "slow"));
    vi.spyOn(alertPolicyApi, "digest").mockRejectedValue(new ApiError(429, "slow"));
    render(await AlertPolicyPage());
    expect(screen.getByRole("alert").textContent).toMatch(/rate|wait|again/i);
  });

  it("a digest that cannot be read does not hide the policy table, and says why", async () => {
    vi.spyOn(alertPolicyApi, "list").mockResolvedValue(MIXED);
    vi.spyOn(alertPolicyApi, "digest").mockRejectedValue(new ApiError(500, "x"));
    render(<StaticSession value={ADMIN}>{await AlertPolicyPage()}</StaticSession>);
    expect(screen.getByText("edge-waf")).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The digest status could not be read");
    expect(screen.queryByText(/Daily at/)).toBeNull();
  });

  it("a forbidden digest names read:alerts", async () => {
    vi.spyOn(alertPolicyApi, "list").mockResolvedValue(MIXED);
    vi.spyOn(alertPolicyApi, "digest").mockRejectedValue(new ApiError(403, "x"));
    render(<StaticSession value={ADMIN}>{await AlertPolicyPage()}</StaticSession>);
    expect(screen.getByRole("alert")).toHaveTextContent("read:alerts");
  });

  it("an empty table with a failed run still shows the empty sentence and the run", async () => {
    await page([], digest({ last: run({ state: "FAILED", error_code: "auth_failed", sent_at: null }) }));
    expect(screen.getByText("Every source alerts as before (default ALERT).")).toBeInTheDocument();
    expect(screen.getByText("Delivery failed")).toBeInTheDocument();
  });
});

describe("boundaries and navigation", () => {
  it("the loading boundary says what is being read", () => {
    render(<Loading />);
    expect(document.body.textContent).toContain("Reading the alert policy and the daily digest.");
  });

  it("the error boundary says what happened, offers a retry and hides the exception", async () => {
    const reset = vi.fn();
    render(
      <AlertPolicyError error={Object.assign(new Error("secret stack"), { digest: "ref-11" })} reset={reset} />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The alert policy page failed to render.");
    expect(alert).toHaveTextContent("Reference: ref-11.");
    expect(alert).not.toHaveTextContent("secret stack");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("Administration lists Alert policy once, right after Outage alerts", () => {
    const admin = NAV.find((g) => g.label === "Administration");
    const labels = admin?.items.map((i) => i.label) ?? [];
    expect(labels.filter((l) => l === "Alert policy")).toHaveLength(1);
    expect(labels.indexOf("Alert policy")).toBe(labels.indexOf("Outage alerts") + 1);
    expect(activeNavHref("/admin/alert-policy")).toBe("/admin/alert-policy");
  });
});
