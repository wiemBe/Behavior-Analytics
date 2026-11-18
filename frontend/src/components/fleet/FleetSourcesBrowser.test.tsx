import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import {
  ApiError,
  fleetApi,
  type CallerInfo,
  type FleetSourcePage,
  type FleetSourceRow,
  type FleetSyncStatus,
} from "@/lib/api";
import { parseSourceListQuery, type RawQuery } from "@/lib/fleetSources";

import { FleetSourcesBrowser, type FleetSourcesFailure } from "./FleetSourcesBrowser";
import { SYNC_POLL_MAX, SYNC_POLL_MS } from "./FleetSyncNow";

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => nav }));

const NOW = "2026-11-18T10:00:00Z";

function row(over: Partial<FleetSourceRow> = {}): FleetSourceRow {
  return {
    id: "s1",
    instance_id: "i1",
    qradar_id: 101,
    name: "dmz-waf-01",
    type_name: "F5 BIG-IP",
    enabled: true,
    auto_discovered: true,
    first_seen_at: "2026-10-13T02:40:00Z",
    source_class: "WAF",
    basis: "NAME_REGEX",
    rule_id: null,
    override_class: null,
    override_by: null,
    override_at: null,
    conflicts: [],
    classified_at: null,
    review_status: "NEW",
    reviewed_at: null,
    reviewed_by: null,
    identifier: "10.1.1.5",
    type_id: 11,
    group_names: ["Perimeter", "DMZ"],
    group_count: 7,
    flow: "FLOWING",
    last_event_at: "2026-11-18T09:59:00Z",
    silent_for_seconds: null,
    current_eps: 12.5,
    expected_eps: 12,
    expected_eps_low: 8,
    expected_eps_high: 16,
    eps_state: "WITHIN_EXPECTED",
    waf_profile: { status: "VALID", enabled: true },
    ips_profile: null,
    identity_profile: { status: "UNVALIDATED", enabled: false },
    description: null,
    protocol_type_id: null,
    target_event_collector_id: null,
    credibility: null,
    qradar_status: null,
    qradar_created_at: null,
    qradar_modified_at: null,
    criticality: "MEDIUM",
    owner: null,
    monitoring_enabled: true,
    maintenance_mode: false,
    maintenance_until: null,
    groups: ["Perimeter", "DMZ"],
    ...over,
  };
}

function pageOf(items: FleetSourceRow[], total = items.length): FleetSourcePage {
  return {
    items,
    total,
    limit: 50,
    offset: 0,
    facets: {
      source_class: [
        { value: "WAF", count: 40 },
        { value: "IPS", count: 12 },
      ],
      criticality: [
        { value: "HIGH", count: 12 },
        { value: "LOW", count: 3 },
      ],
      owner: [
        { value: "net-team", count: 9 },
        { value: "soc", count: 4 },
      ],
      auto_discovered: [
        { value: "YES", count: 40 },
        { value: "NO", count: 5 },
      ],
      monitoring: [
        { value: "YES", count: 44 },
        { value: "NO", count: 1 },
      ],
      maintenance: [
        { value: "YES", count: 2 },
        { value: "NO", count: 43 },
      ],
      collector: [
        { value: "1", count: 30 },
        { value: "2", count: 15 },
      ],
      group: [
        { group_id: 5, name: "Perimeter", count: 30 },
        { group_id: 6, name: "DMZ", count: 9 },
      ],
      type: [
        { type_id: 11, name: "F5 BIG-IP", count: 40 },
        { type_id: 12, name: "Cisco Firepower", count: 12 },
      ],
      flow: [
        { value: "FLOWING", count: 50 },
        { value: "SILENT", count: 2 },
      ],
      profile: [
        { value: "NONE", count: 20 },
        { value: "WAF", count: 30 },
        { value: "IPS", count: 2 },
        { value: "IDENTITY", count: 0 },
      ],
    },
  };
}

function me(permissions: string[]): SessionValue {
  const caller: CallerInfo = { subject: "u", email: null, permissions, effective_permissions: permissions };
  return { status: "ready", me: caller, error: null, reload: async () => {}, signOut: async () => null };
}

function view(
  raw: RawQuery = {},
  page: FleetSourcePage | null = pageOf([row(), row({ id: "s2", name: "edge-ips-02", source_class: "IPS" })]),
  perms = ["log_source:write", "admin:*"],
  failure: FleetSourcesFailure | null = null,
) {
  return render(
    <StaticSession value={me(perms)}>
      <FleetSourcesBrowser query={parseSourceListQuery(raw)} page={page} failure={failure} nowIso={NOW} />
    </StaticSession>,
  );
}

const idle: FleetSyncStatus = { instances: [], running: false };

beforeEach(() => {
  vi.restoreAllMocks();
  nav.push.mockReset();
  nav.replace.mockReset();
  nav.refresh.mockReset();
  vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(idle);
});

describe("the table", () => {
  it("shows the columns, units, profiles and the group overflow", () => {
    view();
    const table = screen.getByRole("table");
    for (const h of ["Source", "Type", "Identifier", "QRadar ID", "Class", "Flow", "EPS (events/s)", "Profiles", "Groups"]) {
      expect(within(table).getByRole("columnheader", { name: new RegExp(h.replace(/[()/]/g, ".")) })).toBeInTheDocument();
    }
    const first = screen.getAllByRole("row")[1];
    expect(within(first).getByRole("link", { name: "dmz-waf-01" })).toHaveAttribute("href", "/log-sources/s1");
    expect(within(first).getByText("10.1.1.5")).toBeInTheDocument();
    expect(within(first).getByText("12.50")).toBeInTheDocument();
    expect(within(first).getByText(/expected 8\.00–16\.00/)).toBeInTheDocument();
    expect(within(first).getByText("WAF valid")).toBeInTheDocument();
    expect(within(first).getByText("Identity off")).toBeInTheDocument();
    expect(within(first).getByText("Perimeter, DMZ")).toBeInTheDocument();
    expect(within(first).getByText("+5")).toBeInTheDocument();
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
    expect(screen.queryByText(/real-time|live/i)).toBeNull();
  });

  it("never draws a zero where the EPS or the last event is missing", () => {
    view(
      {},
      pageOf([
        row({
          current_eps: null,
          expected_eps: null,
          expected_eps_low: null,
          expected_eps_high: null,
          eps_state: null,
          last_event_at: null,
          flow: "NEVER",
          identifier: null,
          waf_profile: null,
          identity_profile: null,
        }),
      ]),
    );
    const r = screen.getAllByRole("row")[1];
    expect(within(r).queryByText(/^0(\.00)?$/)).toBeNull();
    expect(within(r).getAllByTitle("Not measured")).toHaveLength(1);
    expect(within(r).getByTitle("No event seen")).toBeInTheDocument();
    expect(within(r).getByTitle("Not recorded")).toBeInTheDocument();
    expect(within(r).getByTitle("No profile")).toBeInTheDocument();
  });

  it("says NO_BASELINE and a silent source with its age", () => {
    view(
      {},
      pageOf([
        row({ eps_state: "NO_BASELINE", expected_eps: null, expected_eps_low: null, expected_eps_high: null }),
        row({ id: "s3", name: "quiet", flow: "SILENT", silent_for_seconds: 7200, current_eps: 0 }),
      ]),
    );
    expect(screen.getByTitle("No baseline")).toBeInTheDocument();
    expect(screen.getByText(/silent for 2/)).toBeInTheDocument();
  });

  it("marks an override and flags EPS below the band", () => {
    view(
      {},
      pageOf([row({ override_class: "WAF", override_by: "efe", eps_state: "BELOW_EXPECTED", current_eps: 1 })]),
    );
    expect(screen.getByText("Override by efe")).toBeInTheDocument();
    expect(screen.getAllByText(/Below expected/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("Below expected");
  });

  it("sorts by a header click through the URL and marks the sorted column", async () => {
    view({ sort: "eps", order: "desc" });
    expect(screen.getByRole("columnheader", { name: /EPS/ })).toHaveAttribute("aria-sort", "descending");
    await userEvent.click(screen.getByRole("button", { name: /^Type/ }));
    expect(nav.push).toHaveBeenCalledWith("/fleet/sources?sort=type&order=asc");
  });
});

describe("search and facets", () => {
  it("debounces the search into one URL change with q", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      view();
      const box = screen.getByRole("searchbox", { name: "Search sources" });
      expect(box).toHaveAttribute("placeholder", expect.stringContaining("QRadar ID"));
      await userEvent.type(box, "fw01");
      expect(nav.replace).not.toHaveBeenCalled();
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
      expect(nav.replace).toHaveBeenCalledTimes(1);
      expect(nav.replace).toHaveBeenCalledWith("/fleet/sources?q=fw01");
    } finally {
      vi.useRealTimers();
    }
  });

  it("toggles a facet value with its count and adds it to the URL", async () => {
    view({ class: "WAF" });
    await userEvent.click(screen.getByText("Class", { selector: "summary span" }));
    const panel = screen.getByRole("group", { name: "Class" });
    expect(within(panel).getByRole("checkbox", { name: /WAF/ })).toBeChecked();
    await userEvent.click(within(panel).getByRole("checkbox", { name: /IPS/ }));
    expect(nav.push).toHaveBeenCalledWith("/fleet/sources?class=WAF&class=IPS");
  });

  it("narrows the type facet with its own search", async () => {
    view();
    await userEvent.click(screen.getByText("Type", { selector: "summary span" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Find a type" }), "cisco");
    expect(screen.getByRole("checkbox", { name: /Cisco Firepower/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /F5 BIG-IP/ })).toBeNull();
  });

  it("shows active filters as chips that remove one filter or clear all", async () => {
    view({ q: "fw", class: ["WAF"], flow: ["SILENT"], type_id: ["12"] });
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getByText("Search: fw")).toBeInTheDocument();
    expect(within(chips).getByText("Type: Cisco Firepower")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove filter Flow: Silent" }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources?q=fw&class=WAF&type_id=12");
    await userEvent.click(within(chips).getByRole("button", { name: "Clear all" }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources");
  });

  it("builds the CSV link from the filters and notes the cap", () => {
    view({ q: "fw", class: ["WAF"], sort: "eps" }, pageOf([row()], 12000));
    const href = screen.getByRole("link", { name: "Export CSV" }).getAttribute("href")!;
    expect(href).toContain("/fleet/sources.csv?");
    expect(href).toContain("q=fw");
    expect(href).toContain("class=WAF");
    expect(href).toContain("sort=eps");
    expect(href).not.toContain("offset");
    expect(screen.getByText(/first 10,000 of 12,000/)).toBeInTheDocument();
  });

  it("changes the page size through the URL", async () => {
    view();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Per page/ }), "100");
    expect(nav.push).toHaveBeenCalledWith("/fleet/sources?limit=100");
  });
});

describe("paging", () => {
  it("shows the exact range and links onward with the filters kept", () => {
    view({ class: "WAF", limit: "25" }, { ...pageOf([row()], 7912), limit: 25 });
    expect(screen.getByText("1–25 of 7,912")).toBeInTheDocument();
    const next = screen.getByRole("link", { name: /Next/ });
    const href = next.getAttribute("href")!;
    expect(href).toContain("class=WAF");
    expect(href).toContain("limit=25");
    expect(href).toContain("offset=25");
  });
});

describe("states", () => {
  it("says the inventory is empty with no filters", () => {
    view({}, pageOf([]));
    expect(screen.getByText("No log sources are in the inventory.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
  });

  it("names the filters that matched nothing and offers Clear all", async () => {
    view({ q: "zzz", flow: ["SILENT"] }, pageOf([]));
    expect(screen.getByText("No source matches these filters.")).toBeInTheDocument();
    expect(screen.getByText(/search, Flow/)).toBeInTheDocument();
    const clear = screen.getAllByRole("button", { name: "Clear all" });
    await userEvent.click(clear[clear.length - 1]);
    expect(nav.push).toHaveBeenCalledWith("/fleet/sources");
  });

  it("says what failed and offers a retry that reloads", async () => {
    view({}, null, undefined, "unreachable");
    expect(screen.getByRole("alert")).toHaveTextContent("The source list could not be loaded.");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(nav.refresh).toHaveBeenCalled();
  });

  it("names the permission on a 403 and offers no retry", () => {
    view({}, null, undefined, "forbidden");
    expect(screen.getByRole("alert")).toHaveTextContent("read:fleet");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("says the session ended", () => {
    view({}, null, undefined, "session");
    expect(screen.getByRole("alert")).toHaveTextContent(/session has ended/i);
  });
});

describe("bulk class", () => {
  async function selectBoth() {
    await userEvent.click(screen.getByRole("checkbox", { name: "Select dmz-waf-01" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select edge-ips-02" }));
  }
  async function chooseAndReview(value: string) {
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "New class" }), value);
    await userEvent.click(screen.getByRole("button", { name: /Set class/ }));
  }

  it("selects all on the page, shows the count and confirms before sending", async () => {
    const spy = vi.spyOn(fleetApi, "bulkClassification").mockResolvedValue({ updated: 2, items: [] });
    view();
    await userEvent.click(screen.getByRole("checkbox", { name: "Select all 2 sources on this page" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "New class" }), "WAF");
    await userEvent.click(screen.getByLabelText("Mark reviewed"));
    await userEvent.click(screen.getByRole("button", { name: /Set class/ }));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Set the class of 2 sources to WAF?");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(spy).toHaveBeenCalledWith({ log_source_ids: ["s1", "s2"], class: "WAF", mark_reviewed: true });
    expect(await screen.findByText("Updated 2 sources.")).toBeInTheDocument();
    expect(nav.refresh).toHaveBeenCalled();
    expect(screen.queryByText("2 selected")).toBeNull();
  });

  it("clears the override with a null class", async () => {
    const spy = vi.spyOn(fleetApi, "bulkClassification").mockResolvedValue({ updated: 2, items: [] });
    view();
    await selectBoth();
    await chooseAndReview("CLEAR");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Clear the class override of 2 sources?");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(spy).toHaveBeenCalledWith({ log_source_ids: ["s1", "s2"], class: null, mark_reviewed: false });
  });

  it("cancels without a request", async () => {
    const spy = vi.spyOn(fleetApi, "bulkClassification");
    view();
    await selectBoth();
    await chooseAndReview("IPS");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it.each([
    [403, "no", /log_source:write/],
    [404, "2 log sources not found", /2 selected sources no longer exist/],
    [422, "too many", /Not applied|not applied: too many/i],
    [500, "boom", /not applied: boom/i],
  ])("shows the %s answer inline and keeps the selection", async (status, message, expected) => {
    vi.spyOn(fleetApi, "bulkClassification").mockRejectedValue(new ApiError(status, message));
    view();
    await selectBoth();
    await chooseAndReview("WAF");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("hides the selection controls and names the permission without log_source:write", () => {
    view({}, undefined, ["read:fleet"]);
    expect(within(screen.getByRole("table")).queryByRole("checkbox")).toBeNull();
    expect(screen.getByText(/needs log_source:write/)).toBeInTheDocument();
  });
});

describe("sync now", () => {
  const ranBefore: FleetSyncStatus = {
    running: false,
    instances: [
      {
        instance_id: "i1",
        instance_name: "console-a",
        status: "OK",
        last_run_at: "2026-11-18T08:00:00Z",
        last_success_at: "2026-11-18T08:00:00Z",
        consecutive_failures: 0,
      },
    ],
  };
  const ranAfter: FleetSyncStatus = {
    running: false,
    instances: [{ ...ranBefore.instances[0], last_run_at: "2026-11-18T10:01:00Z", last_success_at: "2026-11-18T10:01:00Z" }],
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function click() {
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
  }
  async function tick(times = 1) {
    for (let i = 0; i < times; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SYNC_POLL_MS);
      });
    }
  }

  it("shows the last sync time and queues without claiming success", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(ranBefore);
    const queue = vi.spyOn(fleetApi, "queueSync").mockResolvedValue({ queued: true, already_running: false, task_id: "t" });
    view();
    expect(await screen.findByText(/Last inventory sync:/)).toHaveTextContent("2026-11-18 08:00:00 UTC");
    await click();
    expect(queue).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Queued — the list refreshes when the sync finishes/)).toBeInTheDocument();
    expect(screen.queryByText(/finished/)).toBeNull();
    expect(screen.getByRole("button", { name: "Sync running" })).toBeDisabled();
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("refetches once the status shows a pass that ran after the click", async () => {
    const status = vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(ranBefore);
    vi.spyOn(fleetApi, "queueSync").mockResolvedValue({ queued: true, already_running: false, task_id: "t" });
    view();
    await screen.findByText(/Last inventory sync:/);
    await click();
    await tick();
    expect(nav.refresh).not.toHaveBeenCalled();
    status.mockResolvedValue({ ...ranAfter, running: true });
    await tick();
    expect(nav.refresh).not.toHaveBeenCalled();
    status.mockResolvedValue(ranAfter);
    await tick();
    expect(await screen.findByText(/The sync finished and the list was reloaded/)).toBeInTheDocument();
    expect(nav.refresh).toHaveBeenCalledTimes(1);
  });

  it("says a sync is already running", async () => {
    vi.spyOn(fleetApi, "queueSync").mockResolvedValue({ queued: false, already_running: true, task_id: null });
    view();
    await click();
    expect(screen.getByText(/already running/)).toBeInTheDocument();
  });

  it("says the sync failed and does not reload the list", async () => {
    const status = vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(ranBefore);
    vi.spyOn(fleetApi, "queueSync").mockResolvedValue({ queued: true, already_running: false, task_id: "t" });
    view();
    await screen.findByText(/Last inventory sync:/);
    await click();
    status.mockResolvedValue({
      running: false,
      instances: [{ ...ranAfter.instances[0], status: "FAILED", consecutive_failures: 1 }],
    });
    await tick();
    expect(await screen.findByRole("alert")).toHaveTextContent("The sync failed on console-a");
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("stops polling after a bounded wait and says it did not report", async () => {
    const status = vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(ranBefore);
    vi.spyOn(fleetApi, "queueSync").mockResolvedValue({ queued: true, already_running: false, task_id: "t" });
    view();
    await screen.findByText(/Last inventory sync:/);
    await click();
    await tick(SYNC_POLL_MAX + 1);
    expect(await screen.findByText(/No report after 2 minutes/)).toBeInTheDocument();
    const calls = status.mock.calls.length;
    await tick(3);
    expect(status.mock.calls.length).toBe(calls);
    expect(nav.refresh).not.toHaveBeenCalled();
  });

  it("names the permission when the queue answers 403", async () => {
    vi.spyOn(fleetApi, "queueSync").mockRejectedValue(new ApiError(403, "no"));
    view();
    await click();
    expect(await screen.findByRole("alert")).toHaveTextContent("admin:*");
    await waitFor(() => expect(screen.getByRole("button", { name: "Sync now" })).toBeEnabled());
  });

  it("is absent without admin and never reads the status", () => {
    const status = vi.spyOn(fleetApi, "syncStatus");
    view({}, undefined, ["log_source:write"]);
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
    expect(status).not.toHaveBeenCalled();
  });

  it("says when no sync has ever run and when the status cannot be read", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(idle);
    const { unmount } = view();
    expect(await screen.findByText("No inventory sync has run yet.")).toBeInTheDocument();
    unmount();
    vi.spyOn(fleetApi, "syncStatus").mockRejectedValue(new ApiError(500, "x"));
    view();
    expect(await screen.findByText("The last sync time could not be read.")).toBeInTheDocument();
  });
});

describe("QRadar status", () => {
  it("marks a disabled source with a neutral pill in its row, not only in the flow column", () => {
    view({}, pageOf([row({ enabled: false, flow: "DISABLED_IN_QRADAR", current_eps: null, last_event_at: null })]));
    const r = screen.getAllByRole("row")[1];
    const pill = within(within(r).getByRole("rowheader")).getByText("Disabled");
    expect(pill).toHaveClass("pill-quiet");
    expect(pill.className).not.toMatch(/crit|warn|bad/);
    expect(within(r).getAllByText("Disabled")).toHaveLength(2);
  });

  it("shows no pill on an enabled source", () => {
    view();
    expect(within(screen.getAllByRole("row")[1]).queryByText("Disabled")).toBeNull();
  });

  it("offers Enabled and Disabled with exact counts from the flow facet", () => {
    view(
      {},
      {
        ...pageOf([row()]),
        facets: {
          ...pageOf([]).facets,
          flow: [
            { value: "FLOWING", count: 90 },
            { value: "SILENT", count: 5 },
            { value: "NEVER", count: 3 },
            { value: "DISABLED_IN_QRADAR", count: 22 },
          ],
        },
      },
    );
    const group = screen.getByRole("group", { name: "QRadar status" });
    expect(within(group).getByRole("button", { name: /Enabled/ })).toHaveTextContent("98");
    expect(within(group).getByRole("button", { name: /Disabled/ })).toHaveTextContent("22");
    expect(within(group).getByRole("button", { name: /Disabled/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("sets and clears the filter through the URL", async () => {
    const { unmount } = view();
    await userEvent.click(screen.getByRole("button", { name: /Disabled/ }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources?flow=DISABLED_IN_QRADAR");
    unmount();
    view();
    await userEvent.click(screen.getByRole("button", { name: /^Enabled/ }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources?flow=FLOWING&flow=SILENT&flow=NEVER");
  });

  it("shows the active status as one chip and a pressed button, and removes it", async () => {
    view({ flow: ["DISABLED_IN_QRADAR"] });
    expect(within(screen.getByRole("group", { name: "QRadar status" })).getByRole("button", { name: /Disabled/ })).toHaveAttribute("aria-pressed", "true");
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getByText("QRadar: Disabled")).toBeInTheDocument();
    expect(within(chips).queryByText(/Flow:/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Remove filter QRadar: Disabled" }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources");
  });

  it("collapses the enabled complement into one chip", () => {
    view({ flow: ["FLOWING", "SILENT", "NEVER"] });
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getAllByRole("listitem")).toHaveLength(2);
    expect(within(chips).getByText("QRadar: Enabled")).toBeInTheDocument();
  });

  it("keeps an ordinary flow filter in the flow facet", () => {
    view({ flow: ["SILENT"] });
    expect(screen.getByRole("button", { name: /^Enabled/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Flow: Silent")).toBeInTheDocument();
  });
});

describe("group facet", () => {
  it("lists groups with counts and puts a chosen one in the URL", async () => {
    view();
    await userEvent.click(screen.getByText("Group", { selector: "summary span" }));
    const panel = screen.getByRole("group", { name: "Group" });
    expect(within(panel).getByRole("checkbox", { name: /Perimeter/ })).toHaveTextContent("");
    expect(within(panel).getByText("30")).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("checkbox", { name: /DMZ/ }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources?group=6");
  });

  it("shows a chosen group as a chip with its name and removes it", async () => {
    view({ group: ["5", "6"] });
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getByText("Group: Perimeter")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove filter Group: DMZ" }));
    expect(nav.push).toHaveBeenLastCalledWith("/fleet/sources?group=5");
  });

  it("asks the API for the groups whose name matches, without changing the list", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const spy = vi.spyOn(fleetApi, "sourceList").mockResolvedValue({
        ...pageOf([]),
        facets: { ...pageOf([]).facets, group: [{ group_id: 77, name: "Branch offices EMEA", count: 4 }] },
      });
      view();
      await userEvent.click(screen.getByText("Group", { selector: "summary span" }));
      await userEvent.type(screen.getByRole("searchbox", { name: "Find a group" }), "emea");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ group_q: "emea", limit: 1 }));
      expect(await screen.findByRole("checkbox", { name: /Branch offices EMEA/ })).toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: /Perimeter/ })).toBeNull();
      expect(nav.push).not.toHaveBeenCalled();
      expect(nav.replace).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("round-trips the group ids through the URL and counts them as filters", () => {
    const q = parseSourceListQuery({ group: ["5", "x", "6", "5"] });
    expect(q.group).toEqual([5, 6]);
  });
});

describe("more facets", () => {
  it.each([
    ["Criticality", /High/, "criticality=HIGH"],
    ["Owner", /soc/, "owner=soc"],
    ["Collector", /#2/, "collector=2"],
    ["Auto-discovered", /No/, "auto_discovered=NO"],
    ["Monitoring", /No/, "monitoring=NO"],
    ["Maintenance", /Yes/, "maintenance=YES"],
  ])("%s lists values with counts and sets the URL", async (name, option, param) => {
    view();
    await userEvent.click(screen.getByText(name, { selector: "summary span" }));
    const panel = screen.getByRole("group", { name });
    const box = within(panel).getByRole("checkbox", { name: option });
    expect(within(box.closest("label")!).getByText(/^\d+$/)).toBeInTheDocument();
    await userEvent.click(box);
    expect(nav.push).toHaveBeenLastCalledWith(`/fleet/sources?${param}`);
  });

  it("round-trips every new filter through the URL and shows one chip each", async () => {
    view({
      criticality: ["HIGH", "LOW"],
      owner: "soc",
      collector: ["2"],
      auto_discovered: "YES",
      monitoring: "NO",
      maintenance: "NO",
      criticality_x: "ignored",
    });
    const chips = screen.getByRole("list", { name: "Active filters" });
    for (const label of [
      "Criticality: High",
      "Criticality: Low",
      "Owner: soc",
      "Collector: #2",
      "Auto-discovered: Yes",
      "Monitoring: No",
      "Maintenance: No",
    ])
      expect(within(chips).getByText(label)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove filter Owner: soc" }));
    const url = nav.push.mock.calls.at(-1)![0] as string;
    expect(url).not.toContain("owner=");
    expect(url).toContain("criticality=HIGH&criticality=LOW");
    expect(url).toContain("collector=2");
    expect(url).toContain("auto_discovered=YES&monitoring=NO&maintenance=NO");
  });

  it("drops values that are not on the allow-lists", () => {
    const q = parseSourceListQuery({ criticality: "URGENT", auto_discovered: "MAYBE", collector: "x", cols: "owner,drop" });
    expect(q.criticality).toEqual([]);
    expect(q.auto_discovered).toEqual([]);
    expect(q.collector).toEqual([]);
    expect(q.cols).toEqual(["owner"]);
  });

  it("asks the API for owners that match the search without changing the list", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const spy = vi.spyOn(fleetApi, "sourceList").mockResolvedValue({
        ...pageOf([]),
        facets: { ...pageOf([]).facets, owner: [{ value: "network-ops", count: 7 }] },
      });
      view();
      await userEvent.click(screen.getByText("Owner", { selector: "summary span" }));
      await userEvent.type(screen.getByRole("searchbox", { name: "Find an owner" }), "netw");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ owner_q: "netw", limit: 1 }));
      expect(await screen.findByRole("checkbox", { name: /network-ops/ })).toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: /net-team/ })).toBeNull();
      expect(nav.push).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("column chooser", () => {
  const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");

  it("shows the compact table by default", () => {
    view();
    expect(headers().join("|")).not.toMatch(/Description|Protocol|Owner|Credibility/);
  });

  it("adds a chosen column to the URL in table order, and removes it again", async () => {
    view();
    await userEvent.click(screen.getByText("Columns", { selector: "summary span" }));
    const panel = screen.getByRole("group", { name: "Columns" });
    await userEvent.click(within(panel).getByRole("checkbox", { name: "Owner" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/fleet/sources?cols=owner");
  });

  it("keeps table order whatever the URL order, and keeps the other filters", () => {
    view({ cols: "owner,description,protocol", class: "WAF" });
    const h = headers().filter((x) => /Description|Protocol|Owner/.test(x));
    expect(h).toEqual(["Description", "Protocol ID", "Owner"]);
  });

  it("right-aligns number columns and writes missing values as a dash with its name", () => {
    view(
      { cols: "protocol,credibility,collector,owner,created,maintenance" },
      pageOf([row({ protocol_type_id: 12, credibility: 5, target_event_collector_id: null, owner: null })]),
    );
    const r = screen.getAllByRole("row")[1];
    const cells = within(r).getAllByRole("cell");
    const protocol = cells.find((c) => c.textContent === "12")!;
    expect(protocol).toHaveClass("num");
    expect(cells.find((c) => c.textContent === "5")).toHaveClass("num");
    expect(within(r).getAllByTitle("Not recorded").length).toBeGreaterThanOrEqual(3);
    expect(within(r).getAllByText("No").length).toBeGreaterThan(0);
  });

  it("shows when maintenance ends in UTC", () => {
    view(
      { cols: "maintenance" },
      pageOf([row({ maintenance_mode: true, maintenance_until: "2026-11-18T12:00:00Z" })]),
    );
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("UTC");
  });

  it("keeps the columns on the paging links and out of the CSV link", () => {
    view({ cols: "owner,description" }, { ...pageOf([row()], 300), limit: 50 });
    expect(screen.getByRole("link", { name: /Next/ }).getAttribute("href")).toContain("cols=description%2Cowner");
    expect(screen.getByRole("link", { name: "Export CSV" }).getAttribute("href")).not.toContain("cols");
  });
});

describe("row details", () => {
  function many() {
    return pageOf([
      row({
        description: "Perimeter WAF",
        protocol_type_id: 12,
        credibility: 5,
        owner: "net-team",
        group_count: 60,
        groups: ["Perimeter", "DMZ", "Public web"],
      }),
      row({ id: "s2", name: "edge-ips-02", qradar_id: 202, owner: null }),
    ]);
  }

  it("opens one row, shows every field and every group, and links to the source", async () => {
    view({}, many());
    const open = screen.getByRole("button", { name: "Details of dmz-waf-01" });
    expect(open).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(open);
    expect(open).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("group", { name: "Details" });
    for (const text of ["Perimeter WAF", "net-team", "Perimeter, DMZ, Public web", "+57", "10.1.1.5", "WAF valid"])
      expect(within(panel).getByText(new RegExp(text.replace("+", "\\+")))).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open source" })).toHaveAttribute("href", "/log-sources/s1");
  });

  it("keeps one open at a time", async () => {
    view({}, many());
    await userEvent.click(screen.getByRole("button", { name: "Details of dmz-waf-01" }));
    await userEvent.click(screen.getByRole("button", { name: "Details of edge-ips-02" }));
    expect(screen.getAllByRole("group", { name: "Details" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Details of dmz-waf-01" })).toHaveAttribute("aria-expanded", "false");
  });

  it("closes with Escape and returns focus to its button", async () => {
    view({}, many());
    const open = screen.getByRole("button", { name: "Details of dmz-waf-01" });
    open.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("group", { name: "Details" })).toBeInTheDocument();
    within(screen.getByRole("group", { name: "Details" })).getByRole("link").focus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Details" })).toBeNull();
    expect(screen.getByRole("button", { name: "Details of dmz-waf-01" })).toHaveFocus();
  });

  it("writes a missing value as a dash with its name", async () => {
    view({}, many());
    await userEvent.click(screen.getByRole("button", { name: "Details of edge-ips-02" }));
    const panel = screen.getByRole("group", { name: "Details" });
    expect(within(panel).getAllByTitle("Not recorded").length).toBeGreaterThan(3);
  });
});
