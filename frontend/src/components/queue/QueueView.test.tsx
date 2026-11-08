import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type QueueItem, type QueuePage, type QueueSource } from "@/lib/api";
import { parseQueueQuery } from "@/lib/queue";

import { QueueView } from "./QueueView";

const mocks = vi.hoisted(() => ({ list: vi.fn(), seen: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, queueApi: { list: mocks.list, seen: mocks.seen } };
});

const AS_OF = "2026-11-07T12:00:00.000000Z";
const MARKER = "2026-11-07T06:12:00.000000Z";

function source(kind: QueueSource["kind"], over: Partial<QueueSource> = {}): QueueSource {
  return { kind, state: "OK", count: 1, new_count: 0, permission: null, as_of: null, reason_code: null, ...over };
}

function sources(over: Partial<Record<QueueSource["kind"], Partial<QueueSource>>> = {}): QueueSource[] {
  return (["anomaly", "alert", "offense", "source_review", "catchall_sender"] as const).map((k) =>
    source(k, over[k]),
  );
}

function item(over: Partial<QueueItem> = {}): QueueItem {
  return {
    kind: "anomaly",
    family: "log_source",
    id: "an-1",
    title: "VOLUME_SPIKE on src-1",
    title_code: null,
    title_params: null,
    subject: "src-1",
    detector: "VOLUME_SPIKE",
    severity: "HIGH",
    magnitude: null,
    occurrence_count: null,
    state: "OPEN",
    triage: { status: "NEW", version: 0 },
    owner: null,
    owner_kind: null,
    waiting_since: "2026-11-07T09:32:02Z",
    age_seconds: 8878,
    new_since_last_visit: true,
    change: "NEW",
    alert: { id: "al-1", status: "OPEN" },
    link: "/anomalies/an-1",
    ...over,
  };
}

function page(over: Partial<QueuePage> = {}): QueuePage {
  const items = over.items ?? [item()];
  return {
    as_of: AS_OF,
    marker: { compare_from: MARKER, kind: "STORED", marked_at: MARKER },
    sources: sources(),
    total: items.length,
    total_exact: true,
    limit: 50,
    offset: 0,
    filters_applied: { scope: "all", kind: [], family: [], severity: [], new_only: false, since: null },
    items,
    ...over,
  };
}

function view(params: Record<string, string | string[] | undefined> = {}) {
  return <QueueView query={parseQueueQuery(params)} />;
}

async function open(p: QueuePage, params: Record<string, string | string[] | undefined> = {}) {
  mocks.list.mockResolvedValue(p);
  const r = render(view(params));
  await screen.findByRole("table", { name: "Queue" }).catch(() => undefined);
  return r;
}

beforeEach(() => {
  mocks.list.mockReset();
  mocks.seen.mockReset();
  mocks.seen.mockResolvedValue({ marked_at: AS_OF, previous_marked_at: MARKER, advanced: true });
});

describe("opening the queue moves the marker (D-59)", () => {
  it("loads without since, then posts as_of, the read marker and all_sources_ok once", async () => {
    await open(page());
    await waitFor(() => expect(mocks.seen).toHaveBeenCalledTimes(1));
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(mocks.list.mock.calls[0][0]).not.toContain("since=");
    expect(mocks.seen).toHaveBeenCalledWith({ as_of: AS_OF, expected: MARKER, all_sources_ok: true });
    expect(await screen.findByText(/Your marker moved to 2026-11-07 12:00:00 UTC/)).toBeInTheDocument();
  });

  it("sends expected null on a first visit", async () => {
    await open(page({ marker: { compare_from: "2026-11-06T12:00:00Z", kind: "DEFAULT_WINDOW", marked_at: null } }));
    await waitFor(() => expect(mocks.seen).toHaveBeenCalledTimes(1));
    expect(mocks.seen.mock.calls[0][0].expected).toBeNull();
    expect(screen.getByText(/last 24 h — first visit/)).toBeInTheDocument();
  });

  it("posts when a source is forbidden or not collected: nothing failed to load", async () => {
    await open(
      page({
        sources: sources({
          alert: { state: "FORBIDDEN", count: null, new_count: null, permission: "read:alerts" },
          offense: { state: "NOT_COLLECTED", count: null, new_count: null, reason_code: "NO_CONSOLE" },
        }),
      }),
    );
    await waitFor(() => expect(mocks.seen).toHaveBeenCalledTimes(1));
  });

  it("does not post while a source failed, and says the marker was kept because of it", async () => {
    await open(
      page({
        total_exact: false,
        sources: sources({ offense: { state: "FAILED", count: null, new_count: null, reason_code: "LOAD_FAILED" } }),
      }),
    );
    expect(await screen.findByText(/Your marker was kept at 2026-11-07 06:12:00 UTC because Offenses did not load/)).toBeInTheDocument();
    expect(mocks.seen).not.toHaveBeenCalled();
  });

  it("does not post under a chip override", async () => {
    await open(page({ marker: { compare_from: "2026-11-07T04:00:00Z", kind: "OVERRIDE", marked_at: MARKER } }), { compare: "8h" });
    expect(await screen.findByText(/Your marker was not moved while a comparison time is chosen/)).toBeInTheDocument();
    expect(mocks.list.mock.calls[0][0]).toContain("since=");
    expect(screen.getByText(/compared with 8 h ago/)).toBeInTheDocument();
    expect(mocks.seen).not.toHaveBeenCalled();
  });

  it("does not post from a narrowed view", async () => {
    await open(page(), { scope: "mine" });
    expect(await screen.findByText(/narrowed by a scope, filter or page/)).toBeInTheDocument();
    expect(mocks.seen).not.toHaveBeenCalled();
  });

  it("keeps the New label on screen after the marker moved", async () => {
    await open(page());
    await waitFor(() => expect(mocks.seen).toHaveBeenCalledTimes(1));
    await screen.findByText(/Your marker moved to/);
    const row = screen.getByRole("link", { name: /^New: / }).closest("tr")!;
    expect(within(row).getByText("New")).toBeInTheDocument();
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });

  it("asks later loads of the visit to compare with the previous marker, not the moved one", async () => {
    const { rerender } = await open(page());
    await waitFor(() => expect(mocks.seen).toHaveBeenCalledTimes(1));
    await screen.findByText(/Your marker moved to/);
    mocks.list.mockResolvedValue(page({ marker: { compare_from: MARKER, kind: "OVERRIDE", marked_at: AS_OF } }));
    rerender(view({ scope: "unassigned" }));
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
    expect(mocks.list.mock.calls[1][0]).toContain(`since=${encodeURIComponent(MARKER)}`);
    expect(mocks.seen).toHaveBeenCalledTimes(1);
    // The line still says "your last visit", not "compared with".
    await screen.findByText(/since 2026-11-07 06:12:00 UTC \(your last visit\)/);
  });
});

describe("a conflicting marker", () => {
  it("says another session moved it and reloads from scratch", async () => {
    mocks.seen.mockRejectedValueOnce(
      new ApiError(409, "moved", {
        code: "queue.marker.changed",
        marker: { marked_at: "2026-11-07T11:50:00Z", previous_marked_at: null },
      }),
    );
    await open(page());
    expect(await screen.findByText(/Another session moved your marker to 2026-11-07 11:50:00 UTC/)).toBeInTheDocument();
    mocks.seen.mockResolvedValue({ marked_at: AS_OF, previous_marked_at: null, advanced: true });
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
    expect(mocks.list.mock.calls[1][0]).not.toContain("since=");
    await waitFor(() => expect(mocks.seen).toHaveBeenCalledTimes(2));
  });

  it("words a source_failed refusal", async () => {
    mocks.seen.mockRejectedValueOnce(new ApiError(409, "x", { code: "queue.marker.source_failed", marker: {} }));
    await open(page());
    expect(await screen.findByText(/did not move your marker because a source failed to load/)).toBeInTheDocument();
  });

  it("words any other failure and keeps the labels", async () => {
    mocks.seen.mockRejectedValueOnce(new ApiError(500, "boom"));
    await open(page());
    expect(await screen.findByText(/Your marker could not be moved/)).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
  });
});

describe("the marker line", () => {
  it("states the last visit in UTC", async () => {
    await open(page());
    expect(screen.getByText("Showing what changed since 2026-11-07 06:12:00 UTC (your last visit)")).toBeInTheDocument();
  });

  it("offers the three comparison chips and a way back while one is chosen", async () => {
    await open(page({ marker: { compare_from: "2026-11-07T04:00:00Z", kind: "OVERRIDE", marked_at: MARKER } }), { compare: "8h" });
    const group = screen.getByRole("group", { name: "Compare with" });
    expect(within(group).getByRole("link", { name: "Compare with 8 hours ago" })).toHaveAttribute("aria-current", "true");
    expect(within(group).getByRole("link", { name: "Compare with 24 hours ago" })).toHaveAttribute("href", "/queue?compare=24h");
    expect(within(group).getByRole("link", { name: "Compare with 7 days ago" })).toHaveAttribute("href", "/queue?compare=7d");
    expect(within(group).getByRole("link", { name: "Back to last visit" })).toHaveAttribute("href", "/queue");
  });
});

describe("the sources strip", () => {
  it("words every non-OK state with what it means", async () => {
    await open(
      page({
        total_exact: false,
        sources: [
          source("anomaly", { state: "FAILED", count: null, new_count: null, reason_code: "LOAD_FAILED" }),
          source("alert", { state: "FORBIDDEN", count: null, new_count: null, permission: "read:alerts" }),
          source("offense", { state: "NOT_COLLECTED", count: null, new_count: null, reason_code: "NO_CONSOLE" }),
          source("source_review", { state: "STALE", as_of: "2026-11-07T08:00:00Z", reason_code: "COLLECTION_STALE", count: 3 }),
          source("catchall_sender", { state: "PARTIAL", reason_code: "ALERT_LINK_FAILED", count: 2 }),
        ],
      }),
    );
    const strip = screen.getByRole("region", { name: "Sources" });
    expect(within(strip).getByText(/Its rows are missing and the total below is a lower bound/)).toBeInTheDocument();
    expect(within(strip).getByText(/Needs the read:alerts permission/)).toBeInTheDocument();
    expect(within(strip).getByText(/No QRadar console is configured/)).toBeInTheDocument();
    expect(within(strip).getByText(/Last collected 4 h ago \(2026-11-07 08:00:00 UTC\)/)).toBeInTheDocument();
    expect(within(strip).getByText(/alert link could not be looked up/)).toBeInTheDocument();
    expect(within(strip).queryByText(/0 waiting/)).toBeNull();
  });

  it("words the three partial reasons apart", async () => {
    for (const [code, text] of [
      ["OWNER_UNMAPPED", /no QRadar username/],
      ["ALERTS_NOT_READABLE", /lacks the read:alerts permission/],
      ["ALERT_LINK_FAILED", /could not be looked up/],
    ] as const) {
      const { unmount } = await open(page({ sources: sources({ offense: { state: "PARTIAL", reason_code: code } }) }));
      expect(screen.getByRole("region", { name: "Sources" })).toHaveTextContent(text);
      unmount();
    }
  });

  it("links each source to the route that owns its data", async () => {
    await open(page());
    expect(screen.getByRole("link", { name: "Open Offenses" })).toHaveAttribute("href", "/offenses");
    expect(screen.getByRole("link", { name: "Open Catch-all senders" })).toHaveAttribute("href", "/fleet/catchall");
  });
});

describe("the table", () => {
  it("labels New and Updated in text, shows the alert chip and the UTC time with an age", async () => {
    await open(
      page({
        items: [
          item(),
          item({ id: "off", kind: "offense", family: null, title: "Offense 41", change: "UPDATED", alert: null, triage: null, owner: "alice", owner_kind: "qradar", link: "/offenses/41?instance_id=i", state: "OPEN" }),
          item({ id: "rev", kind: "source_review", family: null, title: "src-1", severity: null, change: null, alert: null, triage: null, state: "UNCLASSIFIED", link: "/fleet/sources?q=src-1" }),
        ],
      }),
    );
    const table = screen.getByRole("table", { name: "Queue" });
    expect(within(table).getByText("New")).toBeInTheDocument();
    expect(within(table).getByText("Updated")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open the Open alert for VOLUME_SPIKE on src-1" })).toHaveAttribute("href", "/alerts/al-1");
    expect(within(table).getByText("QRadar: alice")).toBeInTheDocument();
    expect(within(table).getByText("Unclassified")).toBeInTheDocument();
    expect(within(table).getAllByText("2 h 27 min ago")).toHaveLength(3);
    expect(within(table).getAllByText(/UTC/).length).toBeGreaterThan(0);
    expect(within(table).getByRole("link", { name: "Updated: Offense 41" })).toHaveAttribute("href", "/offenses/41?instance_id=i");
  });

  it("states the range and that the total is a lower bound when a source failed", async () => {
    await open(
      page({ total: 1, total_exact: false, sources: sources({ alert: { state: "FAILED", count: null, new_count: null, reason_code: "LOAD_FAILED" } }) }),
    );
    expect(screen.getByText("Showing 1–1 of at least 1")).toBeInTheDocument();
    // The pager must not state an exact total that is only a lower bound.
    expect(screen.getByRole("navigation", { name: "Pagination" })).toHaveTextContent("1–1 of at least 1");
  });

  it("states an exact total in the pager when every source loaded", async () => {
    await open(page({ total: 1 }));
    expect(screen.getByRole("navigation", { name: "Pagination" })).toHaveTextContent(/^1–1 of 1/);
  });

  it("says Showing 51–100 of N on a later page and warns past the 1000-row cap", async () => {
    await open(page({ total: 1500, offset: 50, items: Array.from({ length: 50 }, (_, i) => item({ id: `a${i}` })) }), { offset: "50" });
    expect(screen.getByText("Showing 51–100 of 1,500")).toBeInTheDocument();
    expect(screen.getByText("The queue holds more than 1000 rows. Narrow it with a filter.")).toBeInTheDocument();
  });
});

describe("states", () => {
  it("shows a loading line first", () => {
    mocks.list.mockReturnValue(new Promise(() => undefined));
    render(view());
    expect(screen.getByRole("status")).toHaveTextContent("Loading the queue");
  });

  it("names what failed and offers Reload when the read fails", async () => {
    mocks.list.mockRejectedValue(new TypeError("fetch failed"));
    render(view());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The queue could not be loaded.");
    expect(alert).toHaveTextContent("Your marker was not moved");
    expect(mocks.seen).not.toHaveBeenCalled();
    mocks.list.mockResolvedValue(page());
    fireEvent.click(within(alert).getByRole("button", { name: "Reload" }));
    await screen.findByRole("table", { name: "Queue" });
  });

  it.each([
    [403, /may not open the queue/],
    [401, /session has expired/],
    [500, /backend failed while building the queue/],
  ])("words a %i", async (status, text) => {
    mocks.list.mockRejectedValue(new ApiError(status, "x"));
    render(view());
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("says nothing is waiting in Mine, only when every readable source loaded", async () => {
    await open(page({ items: [], total: 0, sources: sources({ anomaly: { count: 0 } }) }), { scope: "mine" });
    expect(screen.getByText(/Nothing is waiting in Mine\. Every source you may read loaded at 2026-11-07 12:00:00 UTC/)).toBeInTheDocument();
  });

  it("does not claim a clean queue over a source it could not read", async () => {
    await open(
      page({
        items: [],
        total: 0,
        total_exact: false,
        sources: sources({ offense: { state: "NOT_COLLECTED", count: null, new_count: null, reason_code: "NO_CONSOLE" } }),
      }),
    );
    expect(screen.getByText(/that is not a clean result: Offenses could not be read in full/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing is waiting/)).toBeNull();
  });

  it("says nothing is new and offers the whole queue when New only is empty", async () => {
    await open(page({ items: [], total: 0 }), { new: "1" });
    expect(screen.getByText(/Nothing new or updated since 2026-11-07 06:12:00 UTC/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show everything that is waiting" })).toHaveAttribute("href", "/queue");
  });

  it("says which filters matched nothing and clears them", async () => {
    await open(page({ items: [], total: 0 }), { kind: "alert", severity: "LOW" });
    expect(screen.getByText(/Nothing waiting matches these filters/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear filters" })).toHaveAttribute("href", "/queue");
  });

  it("notes an ignored address value", async () => {
    await open(page(), { scope: "everyone" });
    expect(screen.getByText("Ignored an unknown scope.")).toBeInTheDocument();
  });
});

describe("the address is the view", () => {
  it("makes scope tabs, filters and paging links that keep the other choices", async () => {
    await open(page({ total: 120, items: Array.from({ length: 50 }, (_, i) => item({ id: `a${i}` })) }), {
      kind: "alert",
      severity: "HIGH",
    });
    const scopes = screen.getByRole("navigation", { name: "Scope" });
    expect(within(scopes).getByRole("link", { name: "Mine" })).toHaveAttribute("href", "/queue?scope=mine&kind=alert&severity=HIGH");
    expect(within(scopes).getByRole("link", { name: /^All/ })).toHaveAttribute("aria-current", "page");
    const filters = screen.getByRole("group", { name: "Filters" });
    expect(within(filters).getByRole("link", { name: "Alerts" })).toHaveAttribute("aria-current", "true");
    expect(within(filters).getByRole("link", { name: "Alerts" })).toHaveAttribute("href", "/queue?severity=HIGH");
    expect(within(filters).getByRole("link", { name: "New or updated only" })).toHaveAttribute("href", "/queue?kind=alert&severity=HIGH&new=1");
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute("href", "/queue?kind=alert&severity=HIGH&offset=50");
  });

  it("reloads when the address changes", async () => {
    const { rerender } = await open(page());
    mocks.list.mockResolvedValue(page());
    await act(async () => {
      rerender(view({ kind: "offense" }));
    });
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
    expect(mocks.list.mock.calls[1][0]).toContain("kind=offense");
  });
});
