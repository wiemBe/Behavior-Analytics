import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OffenseDetailPage from "./page";
import { ApiError, api, type OffenseDetail, type OffenseHistoryPoint } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - a non-numeric id: the "Not a valid offense id" notice;
//  - a 404: "not been collected" (the offense may fall outside the window);
//  - any other read failure: "Could not load this offense";
//  - a failed history read: the offense still renders and the history section
//    degrades to its empty notice — a missing history is not a missing offense;
//  - the normal detail: header, summary cards, the attributes table, the entity
//    lists and the history table;
//  - honesty: a missing description says "No description.", missing attributes are
//    em dashes, an empty entity list says "None recorded.", and times are UTC.
// It asserts the magnitude tone class, which the page uses as meaning
// (magnitudeTone: >= 7 crit, >= 4 warn, else ok).

function offense(over: Partial<OffenseDetail> = {}): OffenseDetail {
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
    usernames: ["svc-backup"],
    source_addresses: ["203.0.113.50"],
    local_destination_addresses: ["10.0.0.5"],
    rule_ids: [77],
    log_source_ids: [227],
    destination_networks: ["Internal"],
    ...over,
  };
}

const history: OffenseHistoryPoint[] = [
  {
    captured_at: "2026-10-15T09:00:00Z",
    status: "OPEN",
    magnitude: 8,
    severity: 7,
    event_count: 1280,
    assigned_to: "analyst@example.com",
  },
  {
    captured_at: "2026-10-14T22:20:00Z",
    status: "OPEN",
    magnitude: null,
    severity: null,
    event_count: null,
    assigned_to: null,
  },
];

async function renderPage(id = "42") {
  return render(await OffenseDetailPage({ params: Promise.resolve({ id }) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "offenseHistory").mockResolvedValue(history);
});

describe("offense detail states", () => {
  it("rejects a non-numeric id without a request", async () => {
    const read = vi.spyOn(api, "offense");
    await renderPage("not-an-id");
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Offense" })).toBeInTheDocument();
    expect(screen.getByText("Not a valid offense id.")).toBeInTheDocument();
  });

  it("says a 404 offense was not collected and links back", async () => {
    vi.spyOn(api, "offense").mockRejectedValue(new ApiError(404, "missing"));
    await renderPage("99");
    expect(screen.getByRole("heading", { name: "Offense 99" })).toBeInTheDocument();
    expect(
      screen.getByText(/not been collected\. It may be outside the collection window\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to offenses" })).toHaveAttribute(
      "href",
      "/offenses",
    );
  });

  it("words any other read failure differently and does not leak it", async () => {
    vi.spyOn(api, "offense").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage("42");
    const notice = screen.getByText(/Could not load this offense/);
    expect(notice).toHaveTextContent("Try again shortly.");
    expect(notice).not.toHaveTextContent("secret");
  });

  it("keeps the offense when only the history read fails", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(offense());
    vi.spyOn(api, "offenseHistory").mockRejectedValue(new Error("down"));
    await renderPage();
    expect(
      screen.getByRole("heading", { name: "Offense 42", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The history could not be loaded.");
    expect(screen.getByRole("alert")).toHaveTextContent("Reload the page");
    expect(screen.queryByText(/No history yet\./)).toBeNull();
  });

  it("still says a successful read with no rows has no history yet", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(offense());
    vi.spyOn(api, "offenseHistory").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText(/No history yet\./)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("offense detail contents", () => {
  beforeEach(() => vi.spyOn(api, "offense").mockResolvedValue(offense()));

  it("names the offense and its description under a back link", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { name: "Offense 42", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Repeated authentication failures")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Offenses/ })).toHaveAttribute("href", "/offenses");
  });

  it("shows the six summary scores with their tones", async () => {
    await renderPage();
    const card = (label: string) =>
      screen.getByText(label, { selector: ".k" }).closest(".card") as HTMLElement;
    expect(within(card("Magnitude")).getByText("8")).toHaveClass("crit");
    expect(within(card("Status")).getByText("OPEN")).toBeInTheDocument();
    expect(within(card("Severity")).getByText("7")).toBeInTheDocument();
    expect(within(card("Credibility")).getByText("6")).toBeInTheDocument();
    expect(within(card("Relevance")).getByText("5")).toBeInTheDocument();
    expect(within(card("Age")).getByText("11h 0m")).toBeInTheDocument();
  });

  it("lists the attributes with UTC times and both counts", async () => {
    await renderPage();
    const row = (label: string) =>
      screen.getByText(label, { selector: "th" }).closest("tr") as HTMLElement;
    expect(row("Assigned to")).toHaveTextContent("analyst@example.com");
    expect(row("Offense type")).toHaveTextContent("Source IP");
    expect(row("Offense source")).toHaveTextContent("10.0.0.5");
    expect(row("Source network")).toHaveTextContent("Internal");
    expect(row("Events / Flows")).toHaveTextContent("1,280 / 12");
    expect(row("Sources / Destinations")).toHaveTextContent("3 / 9");
    expect(row("Started")).toHaveTextContent("2026-10-14 22:10:00 UTC");
    expect(row("Last updated")).toHaveTextContent("2026-10-15 08:55:00 UTC");
    // A never-closed offense is an em dash, not a zeroed time.
    expect(row("Closed")).toHaveTextContent("—");
    expect(row("Last collected")).toHaveTextContent("2026-10-15 09:00:00 UTC");
  });

  it("groups the entities with their counts", async () => {
    await renderPage();
    const block = (label: string) =>
      screen.getByRole("heading", { name: new RegExp(`^${label}`) }).closest(".detail-block") as HTMLElement;
    expect(within(block("Usernames")).getByText("svc-backup")).toBeInTheDocument();
    expect(within(block("Source addresses")).getByText("203.0.113.50")).toBeInTheDocument();
    expect(within(block("Contributing rule IDs")).getByText("77")).toBeInTheDocument();
    expect(within(block("Log source IDs")).getByText("227")).toBeInTheDocument();
  });

  it("shows the history snapshots newest first with em dashes for gaps", async () => {
    await renderPage();
    const table = screen.getAllByRole("table")[1];
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("2026-10-15 09:00:00 UTC");
    expect(rows[0]).toHaveTextContent("1,280");
    expect(rows[0]).toHaveTextContent("analyst@example.com");
    expect(rows[1]).toHaveTextContent("2026-10-14 22:20:00 UTC");
    expect(rows[1]).toHaveTextContent("unassigned");
    expect(within(rows[1]).getAllByText("—")).toHaveLength(3);
  });

  it("says every entity list is empty rather than showing nothing", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(
      offense({
        usernames: [],
        source_addresses: [],
        local_destination_addresses: [],
        categories: [],
        rule_ids: [],
        log_source_ids: [],
      }),
    );
    await renderPage();
    expect(screen.getAllByText("None recorded.")).toHaveLength(6);
  });

  it("falls back to 'No description.' and em dashes for missing attributes", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(
      offense({
        description: null,
        assigned_to: null,
        offense_type_name: null,
        offense_source: null,
        source_network: null,
      }),
    );
    await renderPage();
    expect(screen.getByText("No description.")).toBeInTheDocument();
    const row = (label: string) =>
      screen.getByText(label, { selector: "th" }).closest("tr") as HTMLElement;
    expect(row("Offense type")).toHaveTextContent("—");
    expect(row("Offense source")).toHaveTextContent("—");
    expect(row("Source network")).toHaveTextContent("—");
    expect(within(row("Assigned to")).getByText("unassigned")).toBeInTheDocument();
  });
});

describe("offense detail honesty defects", () => {
  // The backend reports an unmeasured count as null. The detail page draws 0 for
  // Event/Flow and Source/Destination counts when the value is missing, which the
  // guideline forbids. The correct behaviour cannot be asserted against today's
  // code, so it is recorded here for the restyle.
  it("draws an unmeasured Events / Flows count as an em dash, not zero", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(offense({ event_count: null, flow_count: null }));
    await renderPage();
    const row = screen.getByText("Events / Flows", { selector: "th" }).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("— / —");
    expect(row).not.toHaveTextContent("0");
  });

  it("draws an unmeasured Sources / Destinations count as an em dash, not zero", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(
      offense({ source_count: null, destination_count: null }),
    );
    await renderPage();
    const row = screen
      .getByText("Sources / Destinations", { selector: "th" })
      .closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("— / —");
    expect(row).not.toHaveTextContent("0");
  });

  it("keeps a measured zero as a zero", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(offense({ event_count: 0, flow_count: 0 }));
    await renderPage();
    const row = screen.getByText("Events / Flows", { selector: "th" }).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("0 / 0");
  });
});

describe("offense detail layout", () => {
  beforeEach(() => vi.spyOn(api, "offense").mockResolvedValue(offense()));

  it("puts the tables in scroll regions and right-aligns the history figures", async () => {
    const { container } = await renderPage();
    expect(screen.getByRole("region", { name: "Offense attributes" })).toBeInTheDocument();
    const region = screen.getByRole("region", { name: "Offense history" });
    expect(region.querySelectorAll("th.num")).toHaveLength(3);
    expect(region.querySelectorAll("td.num").length).toBeGreaterThan(0);
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("shows the magnitude as a bar beside its number, and no bar without a magnitude", async () => {
    const { container, unmount } = await renderPage();
    expect(container.querySelector(".card .mag-bar.crit.mag-w-8")).not.toBeNull();
    unmount();
    vi.spyOn(api, "offense").mockResolvedValue(offense({ magnitude: null }));
    const again = await renderPage();
    expect(again.container.querySelector(".mag-bar")).toBeNull();
  });

});

describe("offense detail failure roles", () => {
  it("gives an offense that was not collected a status role", async () => {
    vi.spyOn(api, "offense").mockRejectedValue(new ApiError(404, "missing"));
    await renderPage("99");
    expect(screen.getByRole("status")).toHaveTextContent("not been collected");
  });

  it("gives any other read failure an alert role", async () => {
    vi.spyOn(api, "offense").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage("42");
    expect(screen.getByRole("alert")).toHaveTextContent("Try again shortly.");
  });

  it("gives a non-numeric id a recovery line and a way back", async () => {
    await renderPage("abc");
    expect(screen.getByRole("alert")).toHaveTextContent("Open the offense from the list.");
    expect(screen.getByRole("link", { name: "Back to offenses" })).toHaveAttribute(
      "href",
      "/offenses",
    );
  });
});

describe("offense detail figures and metric group", () => {
  it("writes thousands separators in the counts and the history", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(
      offense({ event_count: 54760, flow_count: 1200000, source_count: 4000, destination_count: 12 }),
    );
    await renderPage();
    const row = (label: string) =>
      screen.getByText(label, { selector: "th" }).closest("tr") as HTMLElement;
    expect(row("Events / Flows")).toHaveTextContent("54,760 / 1,200,000");
    expect(row("Sources / Destinations")).toHaveTextContent("4,000 / 12");
  });

  it("folds the six summary cards to 3 + 3", async () => {
    vi.spyOn(api, "offense").mockResolvedValue(offense());
    const { container } = await renderPage();
    expect(container.querySelectorAll(".cards .card")).toHaveLength(6);
    expect(container.querySelector(".cards")).toHaveClass("offense-cards-3");
  });
});
