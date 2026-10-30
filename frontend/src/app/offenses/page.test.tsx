import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OffensesPage from "./page";
import {
  ApiError,
  api,
  type OffenseAggregates,
  type OffenseSummary,
  type Page,
} from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails (either call in the Promise.all): the 403 / 401 / failure sentence from `loadErrorMessage`,
//    naming the offense sync command;
//  - no offenses and no filter: the "nothing collected yet" notice;
//  - no offenses with a search or status filter: the "match these filters" notice;
//  - the normal list: aggregate counters, the filter form, the row table, paging;
//  - honesty: a missing description / magnitude / severity / event count / start /
//    age is an em dash, never a zero, and an unassigned offense says "unassigned";
//  - paging: `limit`, `offset`, `search` and `status` go to the API, and offset plus
//    the filters are preserved in the paging links.
// The status and magnitude pills carry a tone class the page uses as meaning
// (OPEN -> warn, CLOSED -> ok, any other status neutral; magnitude >= 7 -> crit,
// >= 4 -> warn, below 4 neutral), so
// those classes are asserted here.

function offense(over: Partial<OffenseSummary> = {}): OffenseSummary {
  return {
    instance_id: "11111111-1111-1111-1111-111111111111",
    qradar_offense_id: 42,
    captured_at: "2026-10-15T09:00:00Z",
    description: "Repeated authentication failures",
    status: "OPEN",
    magnitude: 8,
    severity: 7,
    credibility: 6,
    relevance: 5,
    assigned_to: "analyst@example.com",
    is_assigned: true,
    offense_type_name: "Source IP",
    offense_source: "10.0.0.5",
    source_network: "Internal",
    event_count: 1280,
    flow_count: 12,
    source_count: 3,
    destination_count: 9,
    start_time: "2026-10-14T22:10:00Z",
    last_updated_time: "2026-10-15T08:55:00Z",
    close_time: null,
    age_seconds: 39600,
    categories: ["Authentication"],
    ...over,
  };
}

function aggregates(over: Partial<OffenseAggregates> = {}): OffenseAggregates {
  return {
    active: 7,
    critical: 2,
    unassigned: 3,
    exceeding_sla: 1,
    oldest_age_seconds: 172800,
    average_magnitude: 5.4,
    sla_hours: 24,
    critical_magnitude: 8,
    ...over,
  };
}

function page(items: OffenseSummary[], over: Partial<Page<OffenseSummary>> = {}): Page<OffenseSummary> {
  return { items, total: items.length, limit: 25, offset: 0, ...over };
}

async function renderPage(params: Record<string, string> = {}) {
  return render(await OffensesPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => vi.restoreAllMocks());

describe("offense list states", () => {
  it("keeps the heading and the query when a read fails", async () => {
    const read = vi.spyOn(api, "offenses").mockRejectedValue(new ApiError(500, "secret"));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage();
    expect(read).toHaveBeenCalledWith({ limit: 25, offset: 0, search: "", status: "" });
    expect(screen.getByRole("heading", { name: "Offenses" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Offenses could not be loaded.");
  });

  it.each([
    [403, new ApiError(403, "secret"), "You do not have permission to view offenses. Ask an administrator for the read:offenses grant."],
    [401, new ApiError(401, "secret"), "Your session has expired. Sign in again to continue."],
    [500, new ApiError(500, "secret"), "Offenses could not be loaded. The backend may be unreachable; reload the page to try again."],
    ["network", new Error("fetch failed"), "Offenses could not be loaded. The backend may be unreachable; reload the page to try again."],
  ])("names what failed for a %s read failure, with no developer instruction", async (_case, error, sentence) => {
    vi.spyOn(api, "offenses").mockRejectedValue(error);
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    for (const gone of ["Backend unreachable", "Start the stack", "POST", "secret", "!"]) {
      expect(alert).not.toHaveTextContent(gone);
    }
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says what is empty when nothing has been collected", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage();
    expect(
      screen.getByText(
        "No offenses have been collected from QRadar yet, so this list is empty. Offense collection is set up by an administrator.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/OPS-R10/)).toBeNull();
    expect(screen.queryByText(/operations guide/)).toBeNull();
    expect(screen.queryByText(/offense sync/)).toBeNull();
    expect(screen.queryByText(/POST/)).toBeNull();
  });

  it("distinguishes an empty filtered list from an uncollected one", async () => {
    const read = vi.spyOn(api, "offenses").mockResolvedValue(page([], { total: 0 }));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage({ search: "ssh", status: "OPEN" });
    expect(read).toHaveBeenCalledWith({ limit: 25, offset: 0, search: "ssh", status: "OPEN" });
    expect(screen.getByText("No offenses match these filters.")).toBeInTheDocument();
    expect(screen.queryByText(/collected yet/)).toBeNull();
  });
});

describe("offense list contents", () => {
  beforeEach(() => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
  });

  it("labels the filtered total apart from the open instance counts", async () => {
    await renderPage();
    const card = (label: string) => screen.getByText(label).closest(".card") as HTMLElement;
    expect(screen.getByRole("heading", { name: "Open offenses, whole instance" })).toBeInTheDocument();
    expect(within(card("Matching offenses")).getByText("1")).toBeInTheDocument();
    expect(within(card("Active")).getByText("7")).toBeInTheDocument();
    expect(within(card("Critical (mag 8+)")).getByText("2")).toBeInTheDocument();
    expect(within(card("Unassigned")).getByText("3")).toBeInTheDocument();
    expect(within(card("Over 24h SLA")).getByText("1")).toBeInTheDocument();
    // Age carries its unit, not a bare number.
    expect(within(card("Oldest")).getByText("2 d")).toBeInTheDocument();
  });

  it("gives a positive counter its alarm tone", async () => {
    await renderPage();
    expect(screen.getByText("Critical (mag 8+)").closest(".card")?.querySelector(".v")).toHaveClass(
      "crit",
    );
    expect(screen.getByText("Unassigned").closest(".card")?.querySelector(".v")).toHaveClass("warn");
  });

  it("shows every column and links the id to the detail", async () => {
    await renderPage();
    const table = screen.getByRole("table");
    for (const header of [
      "ID",
      "Description",
      "Mag",
      "Sev",
      "Status",
      "Assigned",
      "Events",
      "Started (UTC)",
      "Age",
    ]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByRole("link", { name: "42" })).toHaveAttribute("href", "/offenses/42");
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Repeated authentication failures");
    expect(row).toHaveTextContent("analyst@example.com");
    expect(row).toHaveTextContent("11 h");
    // Full instant: the year and the zone stay in the cell. The header still says (UTC).
    expect(row).toHaveTextContent("2026-10-14 22:10:00 UTC");
    const started = within(row).getByRole("time");
    expect(started.closest("td")).not.toHaveClass("nowrap");
  });

  it("tones the status and magnitude as the page means them", async () => {
    await renderPage();
    expect(screen.getByText("OPEN")).toHaveClass("warn");
    expect(screen.getByText("OPEN")).not.toHaveClass("ok");
    expect(screen.getByText("8", { selector: ".pill" })).toHaveClass("crit");
  });

  it("keeps a low magnitude and a non-closed status neutral", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(
      page([offense({ magnitude: 3, status: "HIDDEN" })]),
    );
    await renderPage();
    const mag = screen.getByText("3", { selector: ".pill" });
    expect(mag).not.toHaveClass("ok");
    expect(mag).not.toHaveClass("warn");
    expect(mag).not.toHaveClass("crit");
    const status = screen.getByText("HIDDEN");
    expect(status).toHaveClass("pill");
    expect(status).not.toHaveClass("ok");
    expect(status).not.toHaveClass("warn");
  });

  it("tones only a closed offense as ok", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense({ status: "CLOSED" })]));
    await renderPage();
    expect(screen.getByText("CLOSED")).toHaveClass("ok");
  });

  it("draws a missing value as an em dash, never a zero", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(
      page([
        offense({
          description: null,
          magnitude: null,
          severity: null,
          event_count: null,
          start_time: null,
          age_seconds: null,
          assigned_to: null,
        }),
      ]),
    );
    await renderPage();
    const row = screen.getByRole("table").querySelector("tbody tr") as HTMLElement;
    expect(within(row).getByText("unassigned")).toBeInTheDocument();
    // description, magnitude, severity, events, started and age.
    expect(within(row).getAllByText("—")).toHaveLength(6);
    expect(row).not.toHaveTextContent(/\b0\b/);
  });
});

describe("offense paging and filters", () => {
  it("writes the offset to the URL and preserves the filters", async () => {
    const read = vi.spyOn(api, "offenses").mockResolvedValue(
      page([offense()], { total: 120, limit: 25, offset: 50 }),
    );
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage({ offset: "50", search: "scan", status: "OPEN" });
    expect(read).toHaveBeenCalledWith({ limit: 25, offset: 50, search: "scan", status: "OPEN" });
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    expect(within(nav).getByText("51–75 of 120")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Previous" })).toHaveAttribute(
      "href",
      "/offenses?search=scan&status=OPEN&offset=25",
    );
    expect(within(nav).getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/offenses?search=scan&status=OPEN&offset=75",
    );
  });

  it("carries the current filters into the form and offers a clear link", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage({ search: "scan", status: "OPEN" });
    expect(screen.getByRole("searchbox", { name: "Search offenses" })).toHaveValue("scan");
    expect(screen.getByLabelText("Filter by status")).toHaveValue("OPEN");
    expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute("href", "/offenses");
  });
});

describe("nullable aggregate counts", () => {
  const card = (label: string) => screen.getByText(label).closest(".card") as HTMLElement;

  it("renders a null count as not collected, with no alarm tone", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(
      aggregates({
        active: null,
        critical: null,
        unassigned: null,
        exceeding_sla: null,
        oldest_age_seconds: null,
      }),
    );
    await renderPage();
    for (const label of ["Active", "Critical (mag 8+)", "Unassigned", "Over 24h SLA", "Oldest"]) {
      const stat = card(label);
      expect(within(stat).getByText("—")).toBeInTheDocument();
      expect(stat).not.toHaveTextContent("Not collected");
      const value = stat.querySelector(".v");
      expect(value).not.toHaveClass("crit");
      expect(value).not.toHaveClass("warn");
      expect(value).not.toHaveTextContent("0");
    }
    const notes = screen.getAllByText(/No offense snapshot has been collected yet/);
    expect(notes).toHaveLength(1);
    expect(notes[0].closest("[role='status']")).not.toBeNull();
    const matching = card("Matching offenses");
    expect(within(matching).getByText("—")).toBeInTheDocument();
    expect(matching).not.toHaveTextContent("0");
  });

  it("keeps a measured zero match as 0 when a snapshot exists and nothing matches", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(
      aggregates({ active: 0, critical: 0, unassigned: 0, exceeding_sla: 0 }),
    );
    await renderPage();
    expect(within(card("Matching offenses")).getByText("0")).toBeInTheDocument();
    expect(screen.queryByText(/No offense snapshot has been collected yet/)).not.toBeInTheDocument();
  });

  it("keeps a measured zero as 0, with no alarm tone and no not-collected note", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense({ status: "CLOSED" })]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(
      aggregates({ active: 0, critical: 0, unassigned: 0, exceeding_sla: 0 }),
    );
    await renderPage();
    for (const label of ["Active", "Critical (mag 8+)", "Unassigned", "Over 24h SLA"]) {
      const stat = card(label);
      expect(within(stat).getByText("0")).toBeInTheDocument();
      expect(stat).not.toHaveTextContent("Not collected");
      const value = stat.querySelector(".v");
      expect(value).not.toHaveClass("crit");
      expect(value).not.toHaveClass("warn");
    }
  });

  it("keeps a positive count and its alarm tone", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(
      aggregates({ active: 7, critical: 2, unassigned: 3, exceeding_sla: 1 }),
    );
    await renderPage();
    expect(within(card("Active")).getByText("7")).toBeInTheDocument();
    expect(card("Active")).not.toHaveTextContent("Not collected");
    expect(card("Critical (mag 8+)").querySelector(".v")).toHaveClass("crit");
    expect(within(card("Critical (mag 8+)")).getByText("2")).toBeInTheDocument();
    expect(card("Unassigned").querySelector(".v")).toHaveClass("warn");
    expect(card("Over 24h SLA").querySelector(".v")).toHaveClass("warn");
  });
});

describe("offense list layout", () => {
  beforeEach(() => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
  });

  it("has one h1 and a table in a labelled scroll region", async () => {
    const { container } = await renderPage();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Offenses" })).toContainElement(
      screen.getByRole("table"),
    );
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("right-aligns severity, events and age, header and cell", async () => {
    await renderPage();
    const table = screen.getByRole("table");
    for (const name of ["Sev", "Events", "Age"]) {
      expect(within(table).getByRole("columnheader", { name })).toHaveClass("num");
    }
    const row = within(table).getAllByRole("row")[1];
    expect(row.querySelectorAll("td.num")).toHaveLength(3);
    expect(row).toHaveTextContent("1,280");
  });

  it("draws the magnitude as a bar with the number beside it, none when unmeasured", async () => {
    const { container, unmount } = await renderPage();
    expect(container.querySelector("td .mag-bar.crit.mag-w-8")).not.toBeNull();
    expect(screen.getByText("8", { selector: ".pill" })).toBeInTheDocument();
    unmount();
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense({ magnitude: null })]));
    const again = await renderPage();
    expect(again.container.querySelector(".mag-bar")).toBeNull();
  });

  it("says in an alert what failed and what to do", async () => {
    vi.spyOn(api, "offenses").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
  });
});

describe("offense list metric group", () => {
  it("puts the filtered total beside the open instance group", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    const { container } = await renderPage();
    const groups = container.querySelectorAll(".cards");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveClass("offenses-matching");
    expect(groups[0].querySelectorAll(".card")).toHaveLength(1);
    expect(groups[1]).toHaveClass("offenses-open");
    expect(groups[1].querySelectorAll(".card")).toHaveLength(5);
    expect(container.querySelector(".offenses-cards-3")).toBeNull();
  });
});

describe("OffensesPage unknown counts", () => {
  const card = (label: string) => screen.getByText(label).closest(".card") as HTMLElement;

  it("notes open offenses with no magnitude and no age beside the counts they leave out", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(
      aggregates({ critical_unknown: 3, age_unknown: 2 }),
    );
    await renderPage();
    expect(card("Critical (mag 8+)").textContent).toContain("+\u00a03\u00a0with unknown magnitude");
    expect(card("Over 24h SLA").textContent).toContain("+\u00a02\u00a0with unknown age");
    expect(card("Active").textContent).not.toContain("unknown");
    expect(card("Unassigned").textContent).not.toContain("unknown");
  });

  it("adds nothing for zero or missing unknown counts", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(
      aggregates({ critical_unknown: 0, age_unknown: null }),
    );
    await renderPage();
    expect(document.body.textContent).not.toMatch(/with unknown/);
  });
});
