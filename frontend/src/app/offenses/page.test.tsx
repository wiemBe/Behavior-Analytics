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
//  - the read fails (either call in the Promise.all): a "Backend unreachable" notice
//    naming the offense sync command;
//  - no offenses and no filter: the "nothing collected yet" notice;
//  - no offenses with a search or status filter: the "match these filters" notice;
//  - the normal list: aggregate counters, the filter form, the row table, paging;
//  - honesty: a missing description / magnitude / severity / event count / start /
//    age is an em dash, never a zero, and an unassigned offense says "unassigned";
//  - paging: `limit`, `offset`, `search` and `status` go to the API, and offset plus
//    the filters are preserved in the paging links.
// The status and magnitude pills carry a tone class the page uses as meaning
// (OPEN -> warn, other -> ok; magnitude >= 7 -> crit, >= 4 -> warn, else ok), so
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
  it("explains a failed read and names the sync command", async () => {
    const read = vi.spyOn(api, "offenses").mockRejectedValue(new ApiError(500, "secret"));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage();
    expect(read).toHaveBeenCalledWith({ limit: 25, offset: 0, search: "", status: "" });
    expect(screen.getByRole("heading", { name: "Offenses" })).toBeInTheDocument();
    const notice = screen.getByText(/Backend unreachable/);
    expect(notice).toHaveTextContent("python -m app.cli.sync offenses");
    expect(notice).not.toHaveTextContent("secret");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says what is empty when nothing has been collected", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    await renderPage();
    expect(
      screen.getByText(/No offenses collected yet\. Run an offense sync to pull them from QRadar\./),
    ).toBeInTheDocument();
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

  it("counts the whole instance, not the page", async () => {
    await renderPage();
    const card = (label: string) => screen.getByText(label).closest(".card") as HTMLElement;
    expect(within(card("Total")).getByText("1")).toBeInTheDocument();
    expect(within(card("Active")).getByText("7")).toBeInTheDocument();
    expect(within(card("Critical (mag 8+)")).getByText("2")).toBeInTheDocument();
    expect(within(card("Unassigned")).getByText("3")).toBeInTheDocument();
    expect(within(card("Over 24h SLA")).getByText("1")).toBeInTheDocument();
    // Age carries its unit, not a bare number.
    expect(within(card("Oldest")).getByText("2d 0h")).toBeInTheDocument();
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
    expect(row).toHaveTextContent("11h 0m");
    // The header says (UTC); the cell does not repeat the zone.
    expect(row).toHaveTextContent("10-14 22:10");
    expect(row).not.toHaveTextContent("UTC");
  });

  it("tones the status and magnitude as the page means them", async () => {
    await renderPage();
    expect(screen.getByText("OPEN")).toHaveClass("warn");
    expect(screen.getByText("8", { selector: ".pill" })).toHaveClass("crit");
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
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was changed");
  });
});

describe("offense list metric group", () => {
  it("holds the six counters in a group that folds to 3 + 3", async () => {
    vi.spyOn(api, "offenses").mockResolvedValue(page([offense()]));
    vi.spyOn(api, "offenseAggregates").mockResolvedValue(aggregates());
    const { container } = await renderPage();
    expect(container.querySelectorAll(".cards .card")).toHaveLength(6);
    expect(container.querySelector(".cards")).toHaveClass("offenses-cards-3");
  });
});
