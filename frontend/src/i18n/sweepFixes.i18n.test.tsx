// Words the I18N-12 sweep found still English on a Turkish page (class "missed"), each pinned in
// Turkish with the English text unchanged next to it.
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BaselineQuality, CollectionHealth } from "@/lib/sourceDetail";

import { setTestLocale } from "../../vitest.setup";
import { UsersPage } from "@/app/admin/users/UsersPage";
import LogSourcesPage from "@/app/log-sources/page";
import OffensesPage from "@/app/offenses/page";
import { SourceAdvanced } from "@/components/behavior/SourceAdvanced";
import { SourceQuality } from "@/components/behavior/SourceQuality";
import { IpsIncidentHeader } from "@/components/ips/IpsIncidentHeader";
import { ipsDetail } from "@/components/ips/fixtures";
import { api, type LogSourceDetail, type LogSourceSummary, type SourceBehavior, type OffenseAggregates, type OffenseSummary } from "@/lib/api";

const auth = vi.hoisted(() => ({ listUsers: vi.fn(), listRoles: vi.fn() }));
vi.mock("@/components/auth/client", () => auth);
vi.mock("@/components/auth/session", () => ({
  useSession: () => ({ status: "ready", me: { permissions: ["admin:users"] } }),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  auth.listUsers.mockReset();
  auth.listRoles.mockReset();
  setTestLocale("tr");
});

describe("an IPS incident's severity", () => {
  it("reads in Turkish and keeps the English code in English", () => {
    const { container, unmount } = render(
      <IpsIncidentHeader anomaly={ipsDetail({ severity: "HIGH" })} canOpenSource={false} />,
    );
    expect(container.querySelector(".pill-quiet")).toHaveTextContent("YÜKSEK");
    unmount();
    setTestLocale("en");
    const english = render(
      <IpsIncidentHeader anomaly={ipsDetail({ severity: "HIGH" })} canOpenSource={false} />,
    );
    expect(english.container.querySelector(".pill-quiet")).toHaveTextContent("HIGH");
  });
});

const source = (over: Partial<LogSourceSummary> = {}): LogSourceSummary =>
  ({
    id: "s-1", qradar_id: 227, name: "Core Firewall", type_name: "Check Point Firewall",
    criticality: "HIGH", owner: "SOC", enabled: true, monitoring_enabled: true,
    maintenance_mode: false, qradar_status: "SUCCESS", last_event_time: null,
    open_anomaly_count: 0, ...over,
  }) as LogSourceSummary;

describe("the log source inventory's criticality", () => {
  it("reads in Turkish; an unknown level and an inherited name show as sent", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([
      source(),
      source({ id: "s-2", name: "B", criticality: "constructor" }),
      source({ id: "s-3", name: "C", criticality: "SEVERE" }),
    ]);
    render(await LogSourcesPage());
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("YÜKSEK");
    expect(rows[2]).toHaveTextContent("constructor");
    expect(rows[3]).toHaveTextContent("SEVERE");
  });
});

describe("the offense age", () => {
  it("reads its unit in Turkish and in English", async () => {
    const offense = {
      qradar_offense_id: 42, description: "d", status: "OPEN", severity: 7, magnitude: 8,
      assigned_to: null, event_count: 5, start_time: "2026-10-20T06:00:00Z", age_seconds: 39600,
      id: "o-1", categories: [], sla_breached: false,
    } as unknown as OffenseSummary;
    const aggregates = {
      generated_at: "2026-10-20T20:00:00Z", open_count: 1, critical_count: 0,
      unassigned_count: 0, sla_breached_count: 0, oldest_age_seconds: 7200,
    } as unknown as OffenseAggregates;
    vi.spyOn(api, "offenses").mockResolvedValue({ items: [offense], total: 1, limit: 25, offset: 0 });
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates);
    const { container, unmount } = render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(container.textContent).toContain("11 sa");
    expect(container.textContent).not.toMatch(/\b11 h\b/);
    unmount();
    setTestLocale("en");
    const english = render(await OffensesPage({ searchParams: Promise.resolve({}) }));
    expect(english.container.textContent).toContain("11 h");
    expect(english.container.textContent).toContain("2 h");
  });
});

describe("the account source", () => {
  it("reads Yerel for LOCAL and keeps LDAP; an unknown source shows as sent", async () => {
    const account = (id: string, username: string, auth_source: string) => ({
      id, username, auth_source, display_name: null, email: null, roles: [], disabled: false,
      disabled_at: null, last_login_at: null, locked_until: null, must_change_password: false,
    });
    auth.listRoles.mockResolvedValue([]);
    auth.listUsers.mockResolvedValue([
      account("1", "ada", "LOCAL"),
      account("2", "bea", "LDAP"),
      account("3", "cy", "constructor"),
    ]);
    render(<UsersPage />);
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("row", { name: /ada/ })).toHaveTextContent("Yerel");
    expect(within(table).getByRole("row", { name: /bea/ })).toHaveTextContent("LDAP");
    expect(within(table).getByRole("row", { name: /cy/ })).toHaveTextContent("constructor");
    expect(screen.getByRole("radio", { name: "Yerel" })).toBeInTheDocument();
  });
});

describe("the QRadar log source status on the source page", () => {
  const baseline = { status: "OK", label: "Reliable", tone: "ok", explanation: "x", cell: null } as unknown as BaselineQuality;
  const collection = {
    status: "CURRENT", label: "Current", tone: "ok", explanation: "y", expectedBuckets: 12,
    observedBuckets: 12, incompleteBuckets: 0, lagSeconds: 30,
  } as unknown as CollectionHealth;
  const quality = (status: string | null) =>
    render(
      <SourceQuality
        baseline={baseline} collection={collection} expectedLow={1} expectedHigh={2}
        lastBucketAt="2026-07-20T10:00:00Z" qradarStatus={status}
      />,
    );

  it("reads in Turkish in the quality panel, and as sent when unknown", () => {
    const { unmount } = quality("SUCCESS");
    expect(screen.getByText("BAŞARILI")).toBeInTheDocument();
    unmount();
    const odd = quality("constructor");
    expect(screen.getByText("constructor")).toBeInTheDocument();
    odd.unmount();
    setTestLocale("en");
    quality("SUCCESS");
    expect(screen.getByText("SUCCESS")).toBeInTheDocument();
  });

  it("reads in Turkish in the advanced panel, and shows a dash for none", () => {
    const meta = (qradar_status: string | null) =>
      ({ qradar_status, maintenance_mode: false, timezone: null }) as unknown as LogSourceDetail;
    const advanced = (status: string | null) =>
      render(
        <SourceAdvanced
          behavior={{} as SourceBehavior} meta={meta(status)} buckets={[]} cells={[]} cellsFailed={false}
        />,
      );
    const { unmount } = advanced("WARNING");
    expect(screen.getByText("UYARI")).toBeInTheDocument();
    unmount();
    advanced(null);
    expect(screen.queryByText("UYARI")).toBeNull();
  });
});
