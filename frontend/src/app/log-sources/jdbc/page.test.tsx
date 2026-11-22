import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import JdbcSourcesPage from "./page";
import { JdbcRefresh } from "./JdbcRefresh";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import LogSourcesPage from "../page";
import {
  ApiError, api, type JdbcSnapshotOut, type JdbcSourcePage, type JdbcSourceRow,
} from "@/lib/api";

const SAFE = {
  database_type: "MSSQL", database_host: "db1.corp.example", tls_hostname: null, database_port: 1433,
  database_name: "billing", schema_name: null, table_name: null, select_list: "id, ts, msg",
  compare_field: null, polling_interval_seconds: 60, start_time_local: null,
  prepared_statements: null, predefined_query: null, eps_throttle: null, source_label: null,
  use_ssl: true, allow_untrusted: null, advanced_options: null,
};

function row(over: Record<string, unknown> = {}): JdbcSourceRow {
  const base: JdbcSourceRow = {
    id: "s-1", qradar_id: 7, name: "Billing DB", protocol_type_id: 8, protocol_name: "JDBC",
    qradar: {
      enabled: true, status: "CONNECTED", inconsistency: null,
      inventory_last_complete_at: "2026-11-05T18:00:00Z", inventory_last_attempt_at: "2026-11-05T18:00:00Z",
      inventory_state: "COMPLETE", inventory_stale: false, reason_code: null,
    },
    config: {
      support: "VERIFIED", state: "COMPLETE", failure_code: null, observed_at: "2026-11-05T18:00:00Z",
      last_success_at: "2026-11-05T18:00:00Z", stale: false, safe_fields: SAFE, field_states: {},
    },
    volume: {
      measurement: "EXACT", target_start: "2026-11-05T18:00:00Z", target_end: "2026-11-05T19:00:00Z",
      bucket_seconds: 3600, event_count: 120, average_eps: 0.03, collected_at: "2026-11-05T19:00:00Z",
      stale: false, reason_code: "measured", last_observation: null,
    },
    flow: { state: "RECEIVING", reason_code: "measured" },
    monitoring: { enabled: true },
  };
  return { ...base, ...over } as JdbcSourceRow;
}

const SNAP: JdbcSnapshotOut = {
  complete: true, state: "COMPLETE", stale: false, last_complete_at: "2026-11-05T18:00:00Z", consoles: 1,
};

function pageOf(items: JdbcSourceRow[], snap: Partial<JdbcSnapshotOut> = {}, total = items.length): JdbcSourcePage {
  return { items, total, limit: 100, offset: 0, snapshot: { ...SNAP, ...snap } };
}

const ADMIN = { status: "ready", me: { permissions: ["admin:*"], effective_permissions: [] } } as unknown as SessionValue;
const READER = { status: "ready", me: { permissions: ["read:log_sources"], effective_permissions: [] } } as unknown as SessionValue;

async function renderAt(raw: Record<string, string | string[] | undefined> = {}, session?: SessionValue) {
  const ui = await JdbcSourcesPage({ searchParams: Promise.resolve(raw) });
  return render(session ? <StaticSession value={session}>{ui as ReactElement}</StaticSession> : ui);
}

function mockPage(p: JdbcSourcePage) {
  return vi.spyOn(api, "jdbcSources").mockResolvedValue(p);
}

const body = () => screen.getByRole("table").querySelector("tbody") as HTMLElement;

beforeEach(() => vi.restoreAllMocks());

describe("JDBC list contents", () => {
  it("shows the eight columns and one linked row with units and an UTC time", async () => {
    const read = mockPage(pageOf([row()]));
    await renderAt();
    expect(read).toHaveBeenCalledWith({ limit: 100, offset: 0 });
    const headers = within(screen.getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Source", "Database", "Host", "QRadar enabled", "Connection", "Flow", "Events (count)", "Observed UTC"]);
    const r = within(body());
    expect(r.getByRole("link", { name: "Billing DB" })).toHaveAttribute("href", "/log-sources/s-1");
    expect(r.getByText("MSSQL · billing")).toBeInTheDocument();
    expect(r.getByText("db1.corp.example:1433")).toBeInTheDocument();
    expect(r.getByText("Receiving")).toBeInTheDocument();
    expect(r.getByText("120")).toHaveClass("num");
    expect(r.getByText("11-05 19:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("1 source")).toBeInTheDocument();
  });

  it("counts sources in the singular for one and the plural otherwise, with a grouped number", async () => {
    mockPage(pageOf([row()], {}, 1));
    const a = await renderAt();
    expect(screen.getByText("1 source")).toBeInTheDocument();
    expect(screen.queryByText("1 sources")).toBeNull();
    a.unmount();
    mockPage(pageOf([row(), row({ id: "s-2" })], {}, 1234));
    await renderAt();
    expect(screen.getByText("1,234 sources")).toBeInTheDocument();
  });

  it("brackets an IPv6 host so the port is unambiguous", async () => {
    mockPage(pageOf([row({ config: { ...row().config, safe_fields: { ...SAFE, database_host: "2001:db8::5" } } })]));
    await renderAt();
    expect(screen.getByText("[2001:db8::5]:1433")).toBeInTheDocument();
  });

  it("wraps very long database and select-list strings and never prints the select list", async () => {
    const long = "x".repeat(300);
    mockPage(pageOf([row({ config: { ...row().config, safe_fields: { ...SAFE, database_name: long, select_list: `${long}-sel` } } })]));
    await renderAt();
    const cell = screen.getByText(`MSSQL · ${long}`);
    expect(cell).toHaveClass("cell-wrap");
    expect(screen.queryByText(/-sel/)).toBeNull();
  });

  it("never renders a secret sentinel placed in a field the schema does not have", async () => {
    const hostile = row({ password: "SENTINEL-SECRET", config: { ...row().config, safe_fields: { ...SAFE, password: "SENTINEL-SECRET" }, remote_message: "SENTINEL-SECRET" } });
    mockPage(pageOf([hostile]));
    const { container } = await renderAt();
    expect(container.textContent).not.toContain("SENTINEL-SECRET");
  });

  it("links to the JDBC list from /log-sources", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    render(await LogSourcesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("link", { name: "View JDBC" })).toHaveAttribute("href", "/log-sources/jdbc");
  });
});

describe("JDBC list states", () => {
  it("proven empty snapshot", async () => {
    mockPage(pageOf([], {}, 0));
    await renderAt();
    expect(screen.getByText("No JDBC sources were found in the completed inventory.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("never collected", async () => {
    mockPage(pageOf([], { complete: false, state: "NOT_COLLECTED", last_complete_at: null }, 0));
    await renderAt();
    expect(screen.getByText("JDBC inventory has not been collected.")).toBeInTheDocument();
  });

  it("no filter match offers Clear filters", async () => {
    mockPage(pageOf([], {}, 0));
    await renderAt({ q: "zzz", enabled: "true" });
    expect(screen.getByText("No JDBC sources match these filters.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear filters" })).toHaveAttribute("href", "/log-sources/jdbc");
  });

  it("offset past the end offers the first page and keeps the filters", async () => {
    mockPage(pageOf([], {}, 40));
    await renderAt({ offset: "500", protocol: "8" });
    expect(screen.getByText("This page has no sources.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open first page" })).toHaveAttribute("href", "/log-sources/jdbc?protocol=8");
  });

  it("an API error says what failed and offers Retry", async () => {
    vi.spyOn(api, "jdbcSources").mockRejectedValue(new ApiError(500, "SENTINEL-SECRET"));
    await renderAt({ q: "a" });
    expect(screen.getByRole("alert")).toHaveTextContent("JDBC sources could not be loaded. Retry the request.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("SENTINEL-SECRET");
    expect(screen.getByRole("link", { name: "Retry request" })).toHaveAttribute("href", "/log-sources/jdbc?q=a");
    expect(screen.queryByText(/\d+ sources/)).toBeNull();
  });

  it("forbidden names read:log_sources and offers no refresh", async () => {
    vi.spyOn(api, "jdbcSources").mockRejectedValue(new ApiError(403, "x"));
    await renderAt({}, ADMIN);
    expect(screen.getByRole("alert")).toHaveTextContent("JDBC sources require read:log_sources.");
    expect(screen.queryByRole("button", { name: "Refresh JDBC" })).toBeNull();
  });

  it("partial inventory or config says so once, with details", async () => {
    mockPage(pageOf([row({ config: { ...row().config, state: "PARTIAL" } })], { state: "PARTIAL", complete: false }));
    await renderAt();
    expect(screen.getAllByText(/JDBC configuration is partial; some sources were not read\./)).toHaveLength(1);
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(within(body()).getByText("Receiving")).toBeInTheDocument();
  });

  it("truncated volume: flow is not judged, row says Not measured + Truncated, count stays a dash", async () => {
    mockPage(pageOf([row({ flow: { state: "NOT_MEASURED", reason_code: "truncated" }, volume: { ...row().volume, measurement: "TRUNCATED", event_count: null } })]));
    await renderAt();
    expect(screen.getByText(/Event measurement was truncated; flow is not judged\./)).toBeInTheDocument();
    const r = within(body());
    expect(r.getByText("Not measured")).toBeInTheDocument();
    expect(r.queryByText("0")).toBeNull();
    expect(r.getAllByText("Truncated").length).toBeGreaterThan(0);
  });

  it("insufficient data (partial measurement)", async () => {
    mockPage(pageOf([row({ flow: { state: "NOT_MEASURED", reason_code: "partial" }, volume: { ...row().volume, measurement: "PARTIAL", event_count: null } })]));
    await renderAt();
    expect(screen.getByText(/Flow cannot be judged for this interval\./)).toBeInTheDocument();
  });

  it("not collected volume is a dash named Not collected, never 0", async () => {
    mockPage(pageOf([row({ flow: { state: "NOT_MEASURED", reason_code: "not_collected" }, volume: { ...row().volume, measurement: "NOT_COLLECTED", event_count: null, average_eps: null, collected_at: null } })]));
    await renderAt();
    expect(screen.getByText(/Events were not measured for this interval\./)).toBeInTheDocument();
    const r = within(body());
    expect(r.queryByText("0")).toBeNull();
    expect(r.getAllByText("Not collected").length).toBeGreaterThanOrEqual(2);
  });

  it("stale inventory keeps the stored verdict: stale line with dates and Receiving together", async () => {
    mockPage(pageOf([row()], { stale: true, state: "FAILED", last_complete_at: "2026-11-01T10:00:00Z" }));
    await renderAt();
    const line = screen.getByText("QRadar inventory is stale.").closest("p") as HTMLElement;
    expect(line).toHaveTextContent("11-01 10:00 UTC");
    expect(line).toHaveTextContent("Attempt: Failed");
    expect(within(body()).getByText("Receiving")).toBeInTheDocument();
  });

  it("stale config or volume is named on the row and in one line", async () => {
    mockPage(pageOf([row({ config: { ...row().config, stale: true }, volume: { ...row().volume, stale: true } })]));
    await renderAt();
    expect(screen.getByText(/old counts cannot prove current arrival/)).toBeInTheDocument();
    expect(within(body()).getByText("Configuration stale")).toBeInTheDocument();
    expect(within(body()).getByText("Volume stale")).toBeInTheDocument();
  });

  it("disabled: no flow verdict, and disabled plus stale inventory stays disabled", async () => {
    mockPage(pageOf([row({ qradar: { ...row().qradar, enabled: false, status: "DISABLED_STATUS" }, flow: { state: "DISABLED_IN_QRADAR", reason_code: "disabled_in_qradar" }, volume: { ...row().volume, measurement: "NOT_COLLECTED", event_count: null, stale: true } })], { stale: true }));
    await renderAt();
    const r = within(body());
    expect(r.getByText("Disabled in QRadar")).toBeInTheDocument();
    expect(r.getAllByText("Disabled")).toHaveLength(2);
    expect(r.queryByText("Disabled status")).toBeNull();
    expect(r.queryByText("Receiving")).toBeNull();
    expect(screen.getByText("QRadar inventory is stale.")).toBeInTheDocument();
    expect(screen.queryByText(/Events were not measured/)).toBeNull();
    expect(r.queryByText("Volume stale")).toBeNull();
  });

  it("connected with an exact zero says so and keeps the 0 visible", async () => {
    mockPage(pageOf([row({ flow: { state: "NO_ATTRIBUTED_EVENTS", reason_code: "measured" }, volume: { ...row().volume, event_count: 0, average_eps: 0 } })]));
    await renderAt();
    const r = within(body());
    expect(r.getByText("Connected; no attributed events")).toBeInTheDocument();
    expect(r.getByText("0")).toHaveClass("num");
  });

  it("WARN is a Warning in Connection without a diagnosis", async () => {
    mockPage(pageOf([row({ qradar: { ...row().qradar, status: "WARNING" } })]));
    await renderAt();
    expect(within(body()).getByText("Warning")).toHaveClass("pill", "warn");
  });

  it("an unsupported protocol draws em dashes with an accessible name", async () => {
    mockPage(pageOf([row({ protocol_type_id: 20, config: { ...row().config, support: "UNVERIFIED_PROTOCOL", state: "UNVERIFIED_PROTOCOL", safe_fields: null } })]));
    await renderAt();
    const r = within(body());
    expect(r.getAllByText("—")).toHaveLength(2);
    expect(r.getAllByText("Unverified protocol")).toHaveLength(2);
  });
});

describe("JDBC list filters, paging and refresh", () => {
  it("is a GET form whose controls and pagination are reachable by keyboard", async () => {
    mockPage(pageOf([row()], {}, 250));
    await renderAt({ q: "bill", enabled: "true", flow_state: "RECEIVING", protocol: "8" });
    const form = screen.getByRole("search");
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/log-sources/jdbc");
    expect(within(form).getByRole("searchbox", { name: "Search JDBC sources" })).toHaveValue("bill");
    expect(within(form).getByRole("combobox", { name: "QRadar enabled" })).toHaveValue("true");
    expect(within(form).getByRole("combobox", { name: "Flow" })).toHaveValue("RECEIVING");
    expect(within(form).getByRole("combobox", { name: "Protocol" })).toHaveValue("8");
    const user = userEvent.setup();
    await user.tab();
    const seen = new Set<Element | null>();
    for (let i = 0; i < 14; i += 1) {
      seen.add(document.activeElement);
      await user.tab();
    }
    const nav0 = screen.getByRole("navigation", { name: "Pagination" });
    for (const reachable of [
      within(form).getByRole("searchbox"), within(form).getByRole("combobox", { name: "Flow" }),
      within(form).getByRole("button", { name: "Apply filters" }),
      screen.getByRole("region", { name: "JDBC sources" }), within(nav0).getByRole("link", { name: "Next" }),
    ]) expect(seen.has(reachable)).toBe(true);
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/log-sources/jdbc?q=bill&protocol=8&enabled=true&flow_state=RECEIVING&offset=100",
    );
  });

  it("shows the console filter only with more than one console", async () => {
    mockPage(pageOf([row()], { consoles: 1 }));
    const a = await renderAt();
    expect(screen.queryByLabelText("Console ID")).toBeNull();
    a.unmount();
    mockPage(pageOf([row()], { consoles: 2 }));
    await renderAt();
    expect(screen.getByLabelText("Console ID")).toBeInTheDocument();
  });

  it("offers Refresh to an administrator and not to a read-only session", async () => {
    mockPage(pageOf([row()]));
    const a = await renderAt({}, ADMIN);
    expect(screen.getByRole("button", { name: "Refresh JDBC" })).toBeInTheDocument();
    a.unmount();
    await renderAt({}, READER);
    expect(screen.queryByRole("button", { name: "Refresh JDBC" })).toBeNull();
  });

  it.each([
    ["queued", { queued: true, already_running: false, task_ids: ["t"] }, "Refresh queued."],
    ["already running", { queued: false, already_running: true, task_ids: [] }, "A refresh is already running."],
    ["switch off", new ApiError(409, "off"), "JDBC collection is off. Turn it on under Administration → Features."],
    ["queue down", new ApiError(503, "x"), "Refresh could not be queued. Try again."],
  ])("refresh outcome: %s", async (_name, result, text) => {
    const call = vi.spyOn(api, "refreshJdbc");
    if (result instanceof Error) call.mockRejectedValue(result);
    else call.mockResolvedValue(result);
    render(<StaticSession value={ADMIN}><JdbcRefresh /></StaticSession>);
    fireEvent.click(screen.getByRole("button", { name: "Refresh JDBC" }));
    const isError = result instanceof ApiError && result.status !== 409;
    await waitFor(() => expect(screen.getByRole(isError ? "alert" : "status")).toHaveTextContent(text));
    const line = screen.getByRole(isError ? "alert" : "status");
    expect(line).toHaveClass(isError ? "crit" : "change-status");
    if (result instanceof ApiError && result.status === 409) {
      expect(line).toHaveClass("neutral");
      expect(line).not.toHaveClass("crit");
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByRole("link", { name: "Administration → Features" })).toHaveAttribute("href", "/admin/features");
    }
  });
});

const ABSENT = row({
  id: "s-9", name: "Ledger RO",
  qradar: { ...row().qradar, absent_since: "2026-11-04T06:30:00Z" },
  flow: { state: "ABSENT_FROM_INVENTORY", reason_code: "not_collected" },
  volume: {
    ...row().volume, measurement: "NOT_COLLECTED", event_count: null, average_eps: null, collected_at: null, stale: true,
    reason_code: "not_collected",
  },
});

describe("JDBC list absent sources", () => {
  it("Absent in completed inventory: the flow cell names the state and the UTC absent_since, count and time stay dashes", async () => {
    mockPage(pageOf([ABSENT]));
    await renderAt({ include_absent: "true" });
    const r = within(body());
    expect(r.getByText("Absent in completed inventory")).toBeInTheDocument();
    expect(r.getByText("Since", { exact: false })).toHaveTextContent("Since 11-04 06:30 UTC");
    expect(r.queryByText("Receiving")).toBeNull();
    expect(r.queryByText("Volume stale")).toBeNull();
    expect(r.queryByText("0")).toBeNull();
    expect(r.getAllByText("Not collected").length).toBeGreaterThanOrEqual(2);
  });

  it("absent row marks stored enabled/connection as last stored", async () => {
    mockPage(pageOf([ABSENT, row()]));
    await renderAt({ include_absent: "true" });
    const [absentRow, presentRow] = Array.from(body().querySelectorAll("tr")).map((tr) => within(tr as HTMLElement));
    expect(absentRow.getByText("Enabled")).toBeInTheDocument();
    expect(absentRow.getByText("Connected")).toBeInTheDocument();
    const marks = absentRow.getAllByText("Last stored");
    expect(marks).toHaveLength(2);
    expect(marks.every((m) => m.classList.contains("cell-sub"))).toBe(true);
    expect(marks.map((m) => m.closest("td"))).toEqual(
      [absentRow.getByText("Enabled").closest("td"), absentRow.getByText("Connected").closest("td")],
    );
    expect(absentRow.getAllByText(", not the current value")).toHaveLength(2);
    expect(presentRow.queryByText("Last stored")).toBeNull();
  });

  it("summary counts exclude absent: one line counts the absent source and no flow or stale count includes it", async () => {
    const stale = { ...ABSENT, config: { ...ABSENT.config, state: "PARTIAL" as const, stale: true } };
    mockPage(pageOf([stale]));
    await renderAt({ include_absent: "true" });
    const notes = screen.getByRole("list");
    expect(within(notes).getAllByRole("listitem")).toHaveLength(1);
    expect(notes).toHaveTextContent("Some sources are absent from the completed inventory");
    expect(notes).toHaveTextContent("Affected on this page: 1");
    expect(notes).not.toHaveTextContent("Events were not measured");
    expect(notes).not.toHaveTextContent("stale");
    expect(notes).not.toHaveTextContent("partial");
  });

  it("the toggle is a checkbox that sends include_absent=true only when checked, and keeps paging", async () => {
    mockPage(pageOf([row()], {}, 250));
    await renderAt();
    const box = screen.getByRole("checkbox", { name: "Include sources absent from inventory" });
    expect(box).not.toBeChecked();
    expect(box).toHaveAttribute("name", "include_absent");
    expect(box).toHaveAttribute("value", "true");
    vi.restoreAllMocks();
    const read = mockPage(pageOf([ABSENT, row()], {}, 250));
    await renderAt({ include_absent: "true" });
    expect(read).toHaveBeenCalledWith({ limit: 100, offset: 0, include_absent: true });
    expect(screen.getAllByRole("checkbox", { name: "Include sources absent from inventory" }).at(-1)).toBeChecked();
    const nav = screen.getAllByRole("navigation", { name: "Pagination" }).at(-1) as HTMLElement;
    expect(within(nav).getByRole("link", { name: "Next" })).toHaveAttribute("href", "/log-sources/jdbc?include_absent=true&offset=100");
  });

  it("empty with the toggle off says absent sources are hidden and links to the toggle, without a count", async () => {
    mockPage(pageOf([], {}, 0));
    await renderAt();
    expect(screen.getByText("No JDBC sources were found in the completed inventory.")).toBeInTheDocument();
    expect(screen.getByText("Sources absent from it are hidden by default.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Include absent sources" })).toHaveAttribute("href", "/log-sources/jdbc?include_absent=true");
    expect(screen.queryByText(/\d+ (are )?absent/)).toBeNull();
  });

  it("unknown state still falls back to the fixed Unavailable label", async () => {
    mockPage(pageOf([row({ flow: { state: "SOMETHING_NEW", reason_code: null } })]));
    await renderAt();
    expect(within(body()).getByText("Unavailable")).toBeInTheDocument();
    expect(within(body()).queryByText("SOMETHING_NEW")).toBeNull();
  });
});
