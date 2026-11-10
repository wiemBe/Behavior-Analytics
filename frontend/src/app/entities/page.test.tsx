import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EntitiesPage from "./page";
import {
  ApiError,
  api,
  type EntityTimeline,
  type EntityTimelineItem,
  type EntityTimelineLaneOut,
} from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - no value: the page is a form (a GET form, IP preselected), and no read is made;
//  - a value longer than the API accepts: no read, the form kept;
//  - the answer: a heading of kind and canonical value, the window control, one lane table and one
//    merged list; PARTIAL, NOT_COLLECTED and TRUNCATED page states are worded;
//  - empty: "No stored mention" followed by the lane table, never a bare "nothing found";
//  - the API refuses the input (422, coded): the server's sentence, the form kept, and for an
//    unknown kind no kind preselected;
//  - 401, 403, 429 and other failures: the shared load-error sentences.

const NOW = Date.parse("2026-11-10T12:00:00Z");

function lane(source: EntityTimelineLaneOut["source"], over: Partial<EntityTimelineLaneOut> = {}): EntityTimelineLaneOut {
  return { source, state: "OK", reason_code: null, total: 0, permission: null, ...over };
}

function lanes(over: Partial<Record<EntityTimelineLaneOut["source"], Partial<EntityTimelineLaneOut>>> = {}) {
  return (["EXPLANATION", "WAF_TOP", "IPS_TOP", "IPS_ENTITY", "OFFENSE", "LOG_SOURCE"] as const).map((s) =>
    lane(s, over[s]),
  );
}

function item(over: Partial<EntityTimelineItem> = {}): EntityTimelineItem {
  return {
    occurred_at: "2026-11-10T08:02:00Z",
    occurred_end: null,
    source: "OFFENSE",
    role: "SOURCE_IP",
    title: "Suspicious authentication activity",
    title_code: null,
    title_params: null,
    href: "/offenses/7001?instance_id=11111111-1111-1111-1111-111111111111",
    severity: 7,
    measurement: null,
    ...over,
  };
}

function timeline(over: Partial<EntityTimeline> = {}): EntityTimeline {
  return {
    entity: { kind: "IP", value: "192.0.2.44", value_norm: "192.0.2.44" },
    window: { start: "2026-11-09T12:00:00Z", end: "2026-11-10T12:00:00Z" },
    state: "OK",
    reason_code: null,
    lanes: lanes({ OFFENSE: { total: 1 } }),
    items: [item()],
    dangling: 0,
    truncated: false,
    next_cursor: null,
    ...over,
  };
}

function coded(status: number, text: string, code: string, params: Record<string, unknown> = {}) {
  return new ApiError(status, text, text, { detail: text, detail_code: code, detail_params: params });
}

async function renderPage(params: Record<string, string> = {}) {
  return render(await EntitiesPage({ searchParams: Promise.resolve(params) }));
}

let read: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.restoreAllMocks();
  read = vi.spyOn(api, "entityTimeline").mockResolvedValue(timeline());
});
afterEach(() => vi.useRealTimers());

describe("the form", () => {
  it("is the page when no value is given: a GET form onto the same route, IP preselected, no read", async () => {
    const { container } = await renderPage();
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { level: 1, name: "Entity timeline" })).toBeInTheDocument();
    const form = container.querySelector("form")!;
    expect(form).toHaveAttribute("method", "get");
    expect(form).toHaveAttribute("action", "/entities");
    expect(screen.getByRole("textbox", { name: /Value/ })).toHaveValue("");
    expect(screen.getByRole("radio", { name: "IP address" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Host" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "User" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Show timeline" })).toBeInTheDocument();
    expect(screen.getByText(/not a search of raw events and not a QRadar search/)).toBeInTheDocument();
  });

  it("keeps the typed value and refuses to read when it is longer than the API accepts", async () => {
    await renderPage({ value: "x".repeat(300) });
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("longer than 255 characters");
    expect(screen.getByRole("textbox", { name: /Value/ })).toHaveValue("x".repeat(255));
  });
});

describe("the answer", () => {
  it("asks the API for the typed value, the kind, a window ending now and the cursor", async () => {
    await renderPage({ value: "alice", kind: "user", range: "1h", cursor: "abc.def" });
    expect(read).toHaveBeenCalledWith({
      value: "alice",
      kind: "user",
      start: "2026-11-10T11:00:00.000Z",
      end: "2026-11-10T12:00:00.000Z",
      cursor: "abc.def",
    });
  });

  it("defaults to 24 hours and sends no kind when the URL has none", async () => {
    await renderPage({ value: "192.0.2.44" });
    expect(read).toHaveBeenCalledWith({
      value: "192.0.2.44",
      kind: undefined,
      start: "2026-11-09T12:00:00.000Z",
      end: "2026-11-10T12:00:00.000Z",
      cursor: undefined,
    });
  });

  it("titles the page with the kind and the canonical value, and prints the window in UTC", async () => {
    read.mockResolvedValue(
      timeline({ entity: { kind: "USER", value: " Alice ", value_norm: "alice" } }),
    );
    await renderPage({ value: " Alice ", kind: "user" });
    expect(screen.getByRole("heading", { level: 1, name: "User alice" })).toBeInTheDocument();
    const window = screen.getByText(/to/, { selector: "p.entity-window" });
    expect(window).toHaveTextContent("2026-11-09 12:00:00 UTC to 2026-11-10 12:00:00 UTC");
    expect(screen.getByRole("radio", { name: "User" })).toBeChecked();
  });

  it("preselects the kind the API classified when the URL named none", async () => {
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByRole("radio", { name: "IP address" })).toBeChecked();
  });

  it("lists the merged timeline with an absolute UTC time first, then the age", async () => {
    await renderPage({ value: "192.0.2.44" });
    const table = screen.getByRole("table", { name: "Stored mentions of the entity, newest first" });
    const row = within(table).getAllByRole("row")[1];
    const time = within(row).getByText("11-10 08:02 UTC");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-11-10T08:02:00Z");
    expect(row).toHaveTextContent("3 h 58 min ago");
    expect(within(row).getByText("QRadar offenses")).toBeInTheDocument();
    expect(within(row).getByText("Source address")).toBeInTheDocument();
    const link = within(row).getByRole("link", { name: "Suspicious authentication activity" });
    expect(link).toHaveAttribute("href", "/offenses/7001?instance_id=11111111-1111-1111-1111-111111111111");
  });

  it("shows a role from a catalog and never the raw enum", async () => {
    read.mockResolvedValue(
      timeline({ items: [item({ role: "DESTINATION_IP" }), item({ role: "NOT_A_ROLE", title: "b" })] }),
    );
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByText("Destination address")).toBeInTheDocument();
    expect(screen.getByText("Other role")).toBeInTheDocument();
    expect(screen.queryByText("DESTINATION_IP")).toBeNull();
    expect(screen.queryByText("NOT_A_ROLE")).toBeNull();
  });
});

describe("severity", () => {
  it("renders a number as the offense list does and a named severity as a pill, never one as the other", async () => {
    read.mockResolvedValue(
      timeline({
        items: [
          item({ severity: 7, title: "numeric row" }),
          item({ severity: "HIGH", source: "EXPLANATION", title: "named row", href: "/anomalies/a1" }),
          item({ severity: null, title: "no severity row" }),
        ],
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    const rows = screen.getAllByRole("row");
    const numeric = rows.find((r) => r.textContent?.includes("numeric row"))!;
    const named = rows.find((r) => r.textContent?.includes("named row"))!;
    const none = rows.find((r) => r.textContent?.includes("no severity row"))!;
    const numericCell = within(numeric).getAllByRole("cell")[4];
    expect(numericCell).toHaveTextContent(/^7$/);
    expect(numericCell.querySelector(".pill")).toBeNull();
    expect(within(numericCell).getByTitle("QRadar severity 7 of 10")).toBeInTheDocument();
    const namedCell = within(named).getAllByRole("cell")[4];
    expect(namedCell.querySelector(".pill.crit")).toHaveTextContent("HIGH");
    expect(within(none).getAllByRole("cell")[4]).toHaveTextContent("—");
  });
});

describe("a record's title and link", () => {
  it("prefers the message code and its parameters over the stored sentence", async () => {
    read.mockResolvedValue(
      timeline({
        items: [
          item({
            title: "STORED SENTENCE",
            title_code: "anomaly.detector.volume_baseline_samples",
            title_params: { samples: 3 },
            href: "/anomalies/a1",
          }),
        ],
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    expect(
      screen.getByRole("link", {
        name: "baseline has 3 sample(s), below the minimum required for a defensible verdict",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("STORED SENTENCE")).toBeNull();
  });

  it("falls back to the stored sentence when the code is unknown", async () => {
    read.mockResolvedValue(
      timeline({ items: [item({ title: "Stored sentence", title_code: "no.such.code", title_params: {} })] }),
    );
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByRole("link", { name: "Stored sentence" })).toBeInTheDocument();
  });

  it.each(["https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "offenses/1"])(
    "does not render %s as a link",
    async (href) => {
      read.mockResolvedValue(timeline({ items: [item({ href, title: "Row title" })] }));
      await renderPage({ value: "192.0.2.44" });
      expect(screen.getByText("Row title").closest("a")).toBeNull();
      expect(screen.queryByRole("link", { name: "Row title" })).toBeNull();
    },
  );

  it("marks a mention whose measurement is not exact", async () => {
    read.mockResolvedValue(
      timeline({
        items: [
          item({ measurement: "TOP_N", title: "row-top" }),
          item({ measurement: "EXACT", title: "row-exact" }),
          item({ measurement: "ADMITTED", title: "row-admitted" }),
          item({ measurement: null, title: "row-unspecified" }),
        ],
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    const cell = (title: string) =>
      within(screen.getAllByRole("row").find((r) => r.textContent?.includes(title))!).getAllByRole("cell")[5];
    expect(cell("row-top")).toHaveTextContent("Top contributor");
    expect(cell("row-admitted")).toHaveTextContent("Admitted entity");
    expect(cell("row-exact")).toHaveTextContent("Exact");
    expect(cell("row-exact").querySelector(".pill")).toBeNull();
    expect(cell("row-unspecified")).toHaveTextContent("—");
  });

  it("states how long a record lasted when the API gives its end", async () => {
    read.mockResolvedValue(timeline({ items: [item({ occurred_end: "2026-11-10T09:30:00Z" })] }));
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByText(/until/)).toHaveTextContent("until 11-10 09:30 UTC");
  });
});

describe("the lane table", () => {
  function laneRow(name: string) {
    const table = screen.getByRole("table", { name: "Entity timeline lanes" });
    return within(table).getByRole("row", { name: new RegExp(name) });
  }

  it("draws a forbidden lane as forbidden, naming the permission, not as not collected", async () => {
    read.mockResolvedValue(
      timeline({
        state: "PARTIAL",
        reason_code: "entity_timeline.reason.partial",
        lanes: lanes({
          OFFENSE: {
            state: "NOT_COLLECTED",
            reason_code: "entity_timeline.reason.permission",
            permission: "read:offenses",
          },
          WAF_TOP: { state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.family_off" },
        }),
        items: [],
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    const forbidden = laneRow("QRadar offenses");
    expect(forbidden).toHaveTextContent("Forbidden");
    expect(forbidden).toHaveTextContent("Not shown: this lane requires read:offenses.");
    expect(forbidden).not.toHaveTextContent("Not collected");
    const off = laneRow("WAF top clients");
    expect(off).toHaveTextContent("Not collected");
    expect(off).toHaveTextContent("This collection family is switched off.");
    expect(off).not.toHaveTextContent("Forbidden");
  });

  it("never draws an unmeasured total as 0, and draws a proven absence as measured none", async () => {
    read.mockResolvedValue(
      timeline({
        state: "PARTIAL",
        reason_code: "entity_timeline.reason.partial",
        lanes: lanes({
          EXPLANATION: { state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.no_coverage", total: null },
          WAF_TOP: { state: "PARTIAL", reason_code: "entity_timeline.reason.not_among_stored_top", total: null },
          LOG_SOURCE: { state: "OK", total: 0 },
          OFFENSE: { state: "OK", total: 3 },
        }),
        items: [item()],
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    const unmeasured = laneRow("Behavior anomaly contributors");
    expect(within(unmeasured).getAllByRole("cell")[1]).toHaveTextContent("—");
    expect(within(unmeasured).getAllByRole("cell")[1]).not.toHaveTextContent(/\b0\b/);
    const partial = laneRow("WAF top clients");
    expect(partial).toHaveTextContent("Partial");
    expect(partial).toHaveTextContent("cannot prove that the entity was absent");
    expect(within(partial).getAllByRole("cell")[1]).not.toHaveTextContent(/\b0\b/);
    const none = laneRow("Log sources");
    expect(none).toHaveTextContent("None stored");
    expect(within(none).getAllByRole("cell")[1]).toHaveTextContent("0");
    expect(none).toHaveTextContent("Collection covered this window");
    const counted = laneRow("QRadar offenses");
    expect(counted).toHaveTextContent("Measured");
    expect(within(counted).getAllByRole("cell")[1]).toHaveTextContent("3");
  });

  it("words a stale lane and keeps its count", async () => {
    read.mockResolvedValue(
      timeline({
        state: "PARTIAL",
        reason_code: "entity_timeline.reason.partial",
        lanes: lanes({ OFFENSE: { state: "STALE", reason_code: "entity_timeline.reason.offense_stale", total: 2 } }),
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    const stale = laneRow("QRadar offenses");
    expect(stale).toHaveTextContent("Stale");
    expect(stale).toHaveTextContent("QRadar offense collection is stale");
    expect(within(stale).getAllByRole("cell")[1]).toHaveTextContent("2");
  });

  it("never prints a reason code", async () => {
    read.mockResolvedValue(
      timeline({
        state: "PARTIAL",
        reason_code: "entity_timeline.reason.partial",
        lanes: lanes({ OFFENSE: { state: "PARTIAL", reason_code: "entity_timeline.reason.unheard_of", total: 1 } }),
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    expect(laneRow("QRadar offenses")).toHaveTextContent("No reason was recorded.");
    expect(document.body.textContent).not.toMatch(/entity_timeline\./);
  });
});

describe("page states", () => {
  it("says what PARTIAL means for an empty lane", async () => {
    read.mockResolvedValue(timeline({ state: "PARTIAL", reason_code: "entity_timeline.reason.partial" }));
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Partial. Some lanes below are bounded, stale, switched off or not collected.",
    );
  });

  it("says nothing below is a finding when every lane is NOT_COLLECTED", async () => {
    read.mockResolvedValue(
      timeline({
        state: "NOT_COLLECTED",
        reason_code: "entity_timeline.reason.not_collected",
        lanes: lanes().map((l) => ({ ...l, state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.family_off", total: null })),
        items: [],
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    const banners = screen.getAllByRole("status");
    expect(banners[0]).toHaveTextContent("Not collected. No lane measured this entity in this window");
    expect(screen.queryByText(/None stored/)).toBeNull();
    expect(screen.getAllByText("Not collected")).toHaveLength(6);
  });

  it("draws no banner when every lane was measured", async () => {
    await renderPage({ value: "192.0.2.44" });
    expect(document.querySelector(".banner")).toBeNull();
  });
});

describe("empty", () => {
  it("says no stored mention, never clean, and follows it with each lane's status", async () => {
    read.mockResolvedValue(
      timeline({
        state: "PARTIAL",
        reason_code: "entity_timeline.reason.partial",
        items: [],
        lanes: lanes({
          WAF_TOP: { state: "PARTIAL", reason_code: "entity_timeline.reason.not_among_stored_top", total: null },
          OFFENSE: { state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.permission", permission: "read:offenses" },
        }),
      }),
    );
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByText("No stored mention of 192.0.2.44 in this window.")).toBeInTheDocument();
    expect(screen.getByText(/not a finding that it was absent/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Stored mentions of the entity, newest first" })).toBeNull();
    const lanesTable = screen.getByRole("table", { name: "Entity timeline lanes" });
    expect(lanesTable).toBeInTheDocument();
    const notice = screen.getByText("No stored mention of 192.0.2.44 in this window.");
    expect(notice.compareDocumentPosition(lanesTable) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText(/healthy|clean|normal/i)).toBeNull();
  });
});

describe("paging", () => {
  const truncated = () =>
    timeline({
      state: "TRUNCATED",
      reason_code: "entity_timeline.reason.page_truncated",
      truncated: true,
      next_cursor: "c1.sig-_9",
      lanes: lanes({ OFFENSE: { state: "OK", total: null } }),
    });

  it("links Older with the cursor and keeps value, kind and range", async () => {
    read.mockResolvedValue(truncated());
    await renderPage({ value: "a b/c", kind: "host", range: "7d" });
    const nav = screen.getByRole("navigation", { name: "Entity timeline pages" });
    const older = within(nav).getByRole("link", { name: "Older mentions" });
    expect(older).toHaveAttribute("href", "/entities?kind=host&value=a%20b%2Fc&range=7d&cursor=c1.sig-_9");
    expect(within(nav).queryByRole("link", { name: "Back to the newest" })).toBeNull();
    expect(nav).toHaveTextContent("Showing 1 mention, newest first.");
  });

  it("says the list is a page and that counts describe the page, not the window", async () => {
    read.mockResolvedValue(truncated());
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByRole("status")).toHaveTextContent("Truncated. This list is one page.");
    const table = screen.getByRole("table", { name: "Entity timeline lanes" });
    expect(within(table).getByRole("columnheader", { name: "On this page" })).toBeInTheDocument();
    expect(screen.getByText(/counts above are for this page only/)).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /QRadar offenses/ })).toHaveTextContent("Not counted on a paged list.");
  });

  it("does not claim a window-wide absence on a later page", async () => {
    read.mockResolvedValue(timeline({ lanes: lanes() }));
    await renderPage({ value: "192.0.2.44", cursor: "c1.sig" });
    expect(screen.queryByText("None stored")).toBeNull();
    expect(screen.getByRole("columnheader", { name: "On this page" })).toBeInTheDocument();
  });

  it("links back to the newest page without the cursor, and has no Older link on the last page", async () => {
    read.mockResolvedValue(timeline());
    await renderPage({ value: "192.0.2.44", kind: "ip", range: "1h", cursor: "c1.sig" });
    const nav = screen.getByRole("navigation", { name: "Entity timeline pages" });
    expect(within(nav).getByRole("link", { name: "Back to the newest" })).toHaveAttribute(
      "href",
      "/entities?kind=ip&value=192.0.2.44&range=1h",
    );
    expect(within(nav).queryByRole("link", { name: "Older mentions" })).toBeNull();
  });

  it("keeps value and kind in the range links and drops the cursor", async () => {
    read.mockResolvedValue(timeline());
    await renderPage({ value: "alice smith", kind: "user", range: "1h", cursor: "c1.sig" });
    const nav = screen.getByRole("navigation", { name: "Time range (UTC)" });
    expect(within(nav).getByRole("link", { name: "1 hour" })).toHaveAttribute(
      "href",
      "/entities?kind=user&value=alice%20smith&range=1h",
    );
    expect(within(nav).getByRole("link", { name: "24 hours" })).toHaveAttribute(
      "href",
      "/entities?kind=user&value=alice%20smith",
    );
    expect(within(nav).getByRole("link", { name: "7 days" })).toHaveAttribute(
      "href",
      "/entities?kind=user&value=alice%20smith&range=7d",
    );
    for (const link of within(nav).getAllByRole("link")) {
      expect(link.getAttribute("href")).not.toContain("cursor");
    }
    expect(within(nav).getByRole("link", { name: "1 hour" })).toHaveAttribute("aria-current", "page");
  });
});

describe("dangling records", () => {
  it("says how many stored records could no longer be opened, and nothing when none", async () => {
    read.mockResolvedValue(timeline({ dangling: 3 }));
    const { unmount } = await renderPage({ value: "192.0.2.44" });
    expect(screen.getByText("3 stored records could no longer be opened and are not listed.")).toBeInTheDocument();
    unmount();
    read.mockResolvedValue(timeline({ dangling: 1 }));
    const second = await renderPage({ value: "192.0.2.44" });
    expect(second.container).toHaveTextContent("1 stored record could no longer be opened and is not listed.");
    second.unmount();
    read.mockResolvedValue(timeline({ dangling: 0 }));
    const third = await renderPage({ value: "192.0.2.44" });
    expect(third.container).not.toHaveTextContent("could no longer be opened");
  });
});

describe("a refused input", () => {
  it("shows the server's sentence, keeps the form filled and asks for a kind when it could not be inferred", async () => {
    read.mockRejectedValue(
      coded(422, "kind is required when value is not an IP address", "entity_timeline.input.kind_required"),
    );
    await renderPage({ value: "web-01", range: "7d" });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The backend refused this search.");
    expect(alert).toHaveTextContent("kind is required when value is not an IP address");
    expect(alert).toHaveTextContent("Choose what “web-01” is");
    expect(screen.getByRole("textbox", { name: /Value/ })).toHaveValue("web-01");
    for (const name of ["IP address", "Host", "User"]) {
      expect(screen.getByRole("radio", { name })).not.toBeChecked();
    }
    expect(document.querySelector('input[name="range"]')).toHaveValue("7d");
    expect(screen.getByRole("textbox", { name: /Value/ })).toHaveAttribute("aria-describedby", alert.id);
    expect(screen.getByRole("heading", { level: 1, name: "web-01" })).toBeInTheDocument();
  });

  it("keeps the kind the analyst chose on another refusal and offers the newest page for a bad cursor", async () => {
    read.mockRejectedValue(coded(422, "cursor is invalid or has been modified", "entity_timeline.input.cursor_invalid"));
    await renderPage({ value: "alice", kind: "user", cursor: "tampered" });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("cursor is invalid or has been modified");
    expect(within(alert).getByRole("link", { name: "Open the newest page" })).toHaveAttribute(
      "href",
      "/entities?kind=user&value=alice",
    );
    expect(screen.getByRole("radio", { name: "User" })).toBeChecked();
  });

  it("shows the window refusal as sent", async () => {
    read.mockRejectedValue(
      coded(422, "the entity timeline window must not exceed 7 days", "entity_timeline.input.window_max", { days: 7 }),
    );
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByRole("alert")).toHaveTextContent("the entity timeline window must not exceed 7 days");
  });

  it("words a 422 that carries no sentence", async () => {
    read.mockRejectedValue(new ApiError(422, "Unprocessable Content", [{ loc: ["query", "value"] }]));
    await renderPage({ value: "192.0.2.44" });
    expect(screen.getByRole("alert")).toHaveTextContent("could not be accepted as typed");
  });
});

describe("a failed read", () => {
  it.each([
    [403, new ApiError(403, "secret"), "You do not have permission to view the entity timeline. Ask an administrator for the read:anomalies grant."],
    [401, new ApiError(401, "secret"), "Your session has expired. Sign in again to continue."],
    [429, new ApiError(429, "secret", undefined, undefined, 30), "Try again in 30 s."],
    [500, new ApiError(500, "secret"), "The entity timeline could not be loaded. The backend may be unreachable; reload the page to try again."],
    ["network", new Error("fetch failed"), "The entity timeline could not be loaded. The backend may be unreachable; reload the page to try again."],
  ])("names what failed for a %s failure, keeps the form and shows no data", async (_case, error, sentence) => {
    read.mockRejectedValue(error);
    await renderPage({ value: "192.0.2.44" });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    for (const gone of ["secret", "fetch failed", "!"]) expect(alert).not.toHaveTextContent(gone);
    expect(screen.getByRole("textbox", { name: /Value/ })).toHaveValue("192.0.2.44");
    expect(screen.queryByRole("table")).toBeNull();
  });
});
