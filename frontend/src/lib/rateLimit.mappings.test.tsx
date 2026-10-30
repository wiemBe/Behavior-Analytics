import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ClassificationRulesPage from "@/app/admin/classification-rules/page";
import SearchCadencePage from "@/app/admin/search-cadence/page";
import { readError as ipsReadError } from "@/app/admin/ips-sources/readError";
import { readError as outageReadError } from "@/app/admin/outage-policy/outage";
import { readError as wafReadError } from "@/app/admin/waf-sources/readError";
import AnomalyDetailPage from "@/app/anomalies/[id]/page";
import SourcesIndexPage from "@/app/behavior/sources/page";
import FleetPage from "@/app/fleet/page";
import LogSourceDetailPage from "@/app/log-sources/[id]/page";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { HistoryBackfillPage } from "@/components/history/HistoryBackfillPage";
import { ApiError, api, fleetApi } from "@/lib/api";
import { setTestLocale } from "../../vitest.setup";

// POL-5 round 1: every place that maps a failed read to text says "rate limited" on a 429, with
// the wait when Retry-After came, and not "the backend may be unreachable". One block per family.

const limited = (seconds: number | null) =>
  new ApiError(429, "rate limit exceeded", undefined, undefined, seconds);

const EN = "Too many requests came from this address within a minute.";
const EN_WAIT = "Try again in 12 s.";
const EN_MINUTE = "Try again in a minute.";
const TR = "Bu adresten bir dakika içinde çok fazla istek geldi.";
const TR_WAIT = "12 sn sonra yeniden deneyin.";

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("en");
});

describe("server list pages", () => {
  const pages: Array<[string, () => Promise<unknown>, () => void]> = [
    [
      "behavior sources",
      () => SourcesIndexPage({ searchParams: Promise.resolve({}) }),
      () => vi.spyOn(api, "sourceBehaviors"),
    ],
    [
      "fleet",
      () => FleetPage({ searchParams: Promise.resolve({}) }),
      () => vi.spyOn(fleetApi, "summary"),
    ],
    [
      "classification rules",
      () => ClassificationRulesPage(),
      () => vi.spyOn(fleetApi, "rules"),
    ],
    [
      "search cadence",
      () => SearchCadencePage(),
      () => vi.spyOn(api, "searchCadence"),
    ],
  ];

  it.each(pages)("%s says rate limited, with the wait", async (_name, call, spy) => {
    (spy() as unknown as { mockRejectedValue: (e: unknown) => void }).mockRejectedValue(limited(12));
    render((await call()) as never);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(EN);
    expect(alert).toHaveTextContent(EN_WAIT);
    expect(alert).not.toHaveTextContent(/unreachable/i);
  });

  it("says in a minute without Retry-After, and in Turkish", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(limited(null));
    const first = render((await SourcesIndexPage({ searchParams: Promise.resolve({}) })) as never);
    expect(screen.getByRole("alert")).toHaveTextContent(EN_MINUTE);
    first.unmount();
    setTestLocale("tr");
    render((await SourcesIndexPage({ searchParams: Promise.resolve({}) })) as never);
    expect(screen.getByRole("alert")).toHaveTextContent(TR);
    expect(screen.getByRole("alert")).toHaveTextContent("Bir dakika sonra yeniden deneyin.");
  });
});

describe("detail pages", () => {
  it("log source detail says rate limited, not unreachable", async () => {
    vi.spyOn(api, "logSource").mockRejectedValue(limited(12));
    render(await LogSourceDetailPage({ params: Promise.resolve({ id: "s-1" }) }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(EN);
    expect(alert).toHaveTextContent(EN_WAIT);
    expect(alert).not.toHaveTextContent("could not be reached");
  });

  it("anomaly detail says rate limited in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "anomaly").mockRejectedValue(limited(12));
    render(await AnomalyDetailPage({ params: Promise.resolve({ id: "a-1" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent(TR);
    expect(screen.getByRole("alert")).toHaveTextContent(TR_WAIT);
  });
});

describe("administration read helpers", () => {
  it("IPS, WAF and outage reads name the rate limit", () => {
    expect(ipsReadError(limited(12), "sources")).toMatch(/Try again in 12\s+s\./);
    expect(wafReadError(limited(null), "sources")).toContain(EN_MINUTE);
    const outage = outageReadError(limited(12));
    expect(outage.forbidden).toBe(false);
    expect(outage.text).toContain(EN);
    expect(outage.text).toMatch(/Try again in 12\s+s\./);
    for (const text of [ipsReadError(limited(12), "sources"), wafReadError(limited(12), "sources"), outage.text]) {
      expect(text).not.toMatch(/unreachable/i);
    }
  });
});

describe("history backfill page (client read)", () => {
  it("says rate limited with a way to retry", async () => {
    vi.spyOn(api, "historyBackfill").mockRejectedValue(limited(12));
    const value: SessionValue = {
      status: "ready",
      me: {
        subject: "ada",
        email: null,
        permissions: ["admin:*"],
        effective_permissions: ["admin:*"],
        username: "ada",
        auth_source: "LOCAL",
        must_change_password: false,
        roles: ["admin"],
      },
      error: null,
      reload: vi.fn(),
      signOut: vi.fn(),
    };
    render(
      <StaticSession value={value}>
        <HistoryBackfillPage />
      </StaticSession>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(EN);
    expect(alert).toHaveTextContent(EN_WAIT);
    expect(alert).not.toHaveTextContent(/unreachable/i);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
