// SOC-7b: the alert list's filters, counts, range chips and reproducible address.
//
// The page is rendered the way the route renders it (the server component awaited, the client
// filter bar inside it). `useNavigate` is replaced so a click or a submit can be read as the
// address it would load.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "./page";
import { ApiError, api, type Alert, type AlertFacets, type AlertPage, type LogSourceSummary, type Page } from "@/lib/api";

const navigate = vi.fn();
vi.mock("@/hooks/useNavigate", () => ({ useNavigate: () => navigate }));

const NOW = Date.parse("2026-11-05T19:05:00Z");
const SRC = "00000000-0000-4000-8000-000000000001";

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a-1",
    fingerprint: "anomaly:1",
    title: "Event volume spike on Core Firewall",
    description: null,
    severity: "HIGH",
    status: "OPEN",
    source_type: "log_source",
    opened_at: "2026-11-05T18:00:00Z",
    first_seen_at: "2026-11-05T18:00:00Z",
    last_seen_at: null,
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 2,
    evidence_snapshot: {},
    source_anomaly_ids: [],
    ...over,
  };
}

function listPage(items: Alert[], over: Partial<AlertPage> = {}): AlertPage {
  return { items, total: items.length, limit: 100, offset: 0, ...over };
}

function facets(over: Partial<AlertFacets> = {}): AlertFacets {
  return {
    severity: [
      { value: "CRITICAL", count: 2 },
      { value: "HIGH", count: 0 },
      { value: "MEDIUM", count: 7 },
      { value: "LOW", count: 0 },
      { value: "INFO", count: 0 },
    ],
    family: [
      { value: "log_source", count: 5 },
      { value: "waf", count: 4 },
      { value: "ips", count: 0 },
      { value: "property_watch", count: 0 },
      { value: "identity", count: 0 },
      { value: "catchall_sender", count: 0 },
      { value: "custom_rule", count: 0 },
      { value: "scheduled_search", count: 0 },
      { value: "qradar_instance", count: 0 },
    ],
    status: [
      { value: "OPEN", count: 6 },
      { value: "ACKNOWLEDGED", count: 3 },
      { value: "RESOLVED", count: 0 },
    ],
    total: 9,
    ...over,
  };
}


function pageOf(items: LogSourceSummary[], total = items.length): Page<LogSourceSummary> {
  return { items, total, limit: 100, offset: 0 };
}

let list: ReturnType<typeof vi.spyOn>;
let counts: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  navigate.mockReset();
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  list = vi.spyOn(api, "alerts").mockResolvedValue(listPage([alert()]));
  counts = vi.spyOn(api, "alertFacets").mockResolvedValue(facets());
  vi.spyOn(api, "logSources").mockResolvedValue(pageOf([
    { id: SRC, name: "Perimeter firewalls" } as never,
    { id: "00000000-0000-4000-8000-000000000002", name: "Public portal WAF" } as never,
  ]));
});

async function renderAt(params: Record<string, string | string[] | undefined> = {}) {
  return render(await AlertsPage({ searchParams: Promise.resolve(params) }));
}

function hrefOf(name: string | RegExp): string {
  return screen.getByRole("link", { name }).getAttribute("href")!;
}

describe("the address becomes the request", () => {
  it("sends every filter to the list and the same filters to the counts", async () => {
    await renderAt({
      status: "OPEN",
      severity: ["HIGH", "CRITICAL"],
      family: "log_source",
      log_source_id: SRC,
      q: " edge ",
      range: "24h",
      sort: "severity",
      order: "asc",
    });
    const filters = {
      status: "OPEN",
      severity: ["CRITICAL", "HIGH"],
      log_source_id: [SRC],
      family: "log_source",
      q: "edge",
      since: "2026-11-04T19:05:00.000Z",
    };
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ ...filters, sort: "severity", order: "asc", limit: 100, offset: 0 }),
    );
    expect(counts).toHaveBeenCalledWith(expect.objectContaining(filters));
  });

  it("sends nothing for a blank search", async () => {
    await renderAt({ q: "   " });
    expect(list.mock.calls[0][0].q).toBeUndefined();
  });

  it("drops an invalid value, says so, and still shows the list", async () => {
    await renderAt({ severity: ["BOGUS", "HIGH"], range: "2d", sort: "colour" });
    const note = screen.getByText(/Unknown severity ignored\./);
    expect(note).toHaveTextContent("Unknown sort ignored.");
    expect(note).toHaveTextContent("Unknown range ignored");
    expect(list.mock.calls[0][0].severity).toEqual(["HIGH"]);
    expect(JSON.stringify(list.mock.calls[0][0])).not.toMatch(/BOGUS|colour|2d/);
    expect(screen.getByRole("link", { name: /Event volume spike/ })).toBeInTheDocument();
  });

  it("offers no per-type filter: the backend answers family only", async () => {
    await renderAt();
    expect(screen.queryByLabelText(/type/i)).toBeNull();
    expect(within(screen.getByLabelText("Kind")).getByRole("option", { name: /Log-source anomalies/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Kind")).toBeInTheDocument();
  });
});

describe("facet counts", () => {
  it("prints a count beside every status, severity and family value, zeros included", async () => {
    await renderAt();
    const status = screen.getByLabelText("Alert status") as HTMLSelectElement;
    expect(within(status).getByRole("option", { name: "Open (6)" })).toBeInTheDocument();
    expect(within(status).getByRole("option", { name: "Resolved (0)" })).toBeInTheDocument();
    expect(within(status).getByRole("option", { name: "All statuses (9)" })).toBeInTheDocument();
    const family = screen.getByLabelText("Kind");
    expect(within(family).getByRole("option", { name: "WAF anomalies (4)" })).toBeInTheDocument();
    expect(within(family).getByRole("option", { name: "IPS anomalies (0)" })).toBeInTheDocument();
    expect(within(family).getByRole("option", { name: "Identity (0)" })).toBeInTheDocument();
    const severity = screen.getByRole("group", { name: "Severity" });
    expect(within(severity).getByRole("link", { name: "CRITICAL: 2 alerts" })).toHaveTextContent("2");
    // A zero is a measurement: it is drawn as 0 and is not hidden or worded as missing.
    expect(within(severity).getByRole("link", { name: "HIGH: 0 alerts" })).toHaveTextContent("0");
  });

  it("draws no count at all when the count read failed, and says so", async () => {
    counts.mockRejectedValue(new ApiError(500, "boom"));
    await renderAt();
    expect(screen.getByText(/Counts unavailable/)).toBeInTheDocument();
    const severity = screen.getByRole("group", { name: "Severity" });
    for (const link of within(severity).getAllByRole("link")) {
      expect(link.textContent).not.toMatch(/\d/);
    }
    const status = screen.getByLabelText("Alert status");
    expect(within(status).getByRole("option", { name: "Open" })).toBeInTheDocument();
    expect(within(status).queryByRole("option", { name: /\(0\)/ })).toBeNull();
    // The list is unaffected.
    expect(screen.getByRole("link", { name: /Event volume spike/ })).toBeInTheDocument();
  });

  it("shows a selected severity with its count and an unselected one with its measured 0", async () => {
    await renderAt({ severity: "CRITICAL" });
    const severity = screen.getByRole("group", { name: "Severity" });
    const critical = within(severity).getByRole("link", { name: "CRITICAL: 2 alerts" });
    expect(critical).toHaveAttribute("aria-current", "true");
    expect(critical).toHaveTextContent("2");
    expect(within(severity).getByRole("link", { name: "HIGH: 0 alerts" })).toHaveTextContent("0");
  });

  it("offers the counts a second severity would add while one is selected", async () => {
    counts.mockResolvedValue(
      facets({
        // The server counts each facet with every filter except its own, so the other
        // severities keep the counts they have under the remaining filters.
        total: 4,
        severity: [
          { value: "CRITICAL", count: 2 },
          { value: "HIGH", count: 4 },
          { value: "MEDIUM", count: 7 },
          { value: "LOW", count: 1 },
          { value: "INFO", count: 0 },
        ],
      }),
    );
    await renderAt({ severity: "HIGH" });

    const severity = screen.getByRole("group", { name: "Severity" });
    expect(within(severity).getByRole("link", { name: "CRITICAL: 2 alerts" })).toHaveTextContent("2");
    expect(within(severity).getByRole("link", { name: "MEDIUM: 7 alerts" })).toHaveTextContent("7");
    expect(within(severity).getByRole("link", { name: "LOW: 1 alert" })).toHaveTextContent("1");
  });

  it("counts All statuses as every status, not as the status-filtered total", async () => {
    counts.mockResolvedValue(
      facets({
        // `total` is the list's total under status=OPEN; the status facet is counted with
        // the status filter dropped, so its sum (6 + 3 + 0) is what choosing All returns.
        total: 4,
        status: [
          { value: "OPEN", count: 6 },
          { value: "ACKNOWLEDGED", count: 3 },
          { value: "RESOLVED", count: 0 },
        ],
      }),
    );
    await renderAt({ status: "OPEN" });

    const status = screen.getByLabelText("Alert status") as HTMLSelectElement;
    expect(within(status).getByRole("option", { name: "All statuses (9)" })).toBeInTheDocument();
    expect(within(status).queryByRole("option", { name: "All statuses (4)" })).toBeNull();
    expect(within(status).getByRole("option", { name: "Open (6)" })).toBeInTheDocument();
  });

  it("labels the family control as a kind of alert and the chip says so", async () => {
    await renderAt({ family: "log_source" });
    expect(screen.getByRole("link", { name: "Remove filter Kind: Log-source anomalies" })).toBeInTheDocument();
  });

  it("keeps the list when the log source names could not be read and shows the ids", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(500, "boom"));
    await renderAt({ log_source_id: SRC });
    expect(screen.getByText(/source names could not be loaded/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Log source: 00000000… \(unknown source\)/ }))
      .toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Event volume spike/ })).toBeInTheDocument();
  });
});

describe("time chips", () => {
  it("link to a relative range and keep the other filters", async () => {
    await renderAt({ severity: "HIGH", status: "OPEN" });
    expect(hrefOf("Last 24 hours")).toBe("/alerts?status=OPEN&severity=HIGH&range=24h");
    expect(hrefOf("Last hour")).toBe("/alerts?status=OPEN&severity=HIGH&range=1h");
    expect(hrefOf("Last 7 days")).toContain("range=7d");
    expect(hrefOf("Last 30 days")).toContain("range=30d");
    expect(screen.getByRole("button", { name: "Custom" })).toBeInTheDocument();
  });

  it("marks the active chip, prints the absolute UTC window and lets the chip clear itself", async () => {
    await renderAt({ range: "24h" });
    expect(screen.getByRole("link", { name: "Last 24 hours" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("link", { name: "Last hour" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent(
      "2026-11-04 19:05 to 2026-11-05 19:05 UTC (now)",
    );
    expect(hrefOf("Last 24 hours")).toBe("/alerts");
  });

  it("says there is no time bound when none is set", async () => {
    await renderAt();
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent("All time");
  });

  it("reveals the Custom inputs and applies them as absolute UTC", async () => {
    await renderAt({ severity: "HIGH" });
    expect(screen.queryByLabelText("Start time (UTC)")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Start time (UTC)"), {
      target: { value: "2026-11-05T09:00" },
    });
    fireEvent.change(screen.getByLabelText("End time (UTC)"), {
      target: { value: "2026-11-05T12:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(navigate).toHaveBeenCalledWith(
      "/alerts?severity=HIGH&since=2026-11-05T09%3A00%3A00Z&until=2026-11-05T12%3A30%3A00Z",
    );
  });

  it("opens Custom for a Custom window and fills the inputs", async () => {
    await renderAt({ since: "2026-11-05T00:00:00Z", until: "2026-11-05T12:00:00Z" });
    expect(screen.getByLabelText("Start time (UTC)")).toHaveValue("2026-11-05T00:00");
    expect(screen.getByLabelText("End time (UTC)")).toHaveValue("2026-11-05T12:00");
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent(
      "2026-11-05 00:00 to 2026-11-05 12:00 UTC",
    );
  });

  it("keeps a chip as range=... when the search is applied", async () => {
    await renderAt({ range: "7d" });
    fireEvent.change(screen.getByLabelText("Search alert titles"), { target: { value: "waf" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(navigate).toHaveBeenCalledWith("/alerts?q=waf&range=7d");
  });
});

describe("the controls navigate to the address of the view they make", () => {
  it("a severity link toggles that severity in the address", async () => {
    await renderAt({ severity: "HIGH" });
    expect(hrefOf(/^CRITICAL/)).toBe("/alerts?severity=CRITICAL&severity=HIGH");
    expect(hrefOf(/^HIGH/)).toBe("/alerts");
    expect(screen.getByRole("link", { name: /^HIGH/ })).toHaveAttribute("aria-current", "true");
  });

  it("the family select writes family and starts at page one", async () => {
    await renderAt({ status: "OPEN", offset: "100" });
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "waf" } });
    expect(navigate).toHaveBeenCalledWith("/alerts?status=OPEN&family=waf");
  });

  it("the status select writes status", async () => {
    await renderAt({ family: "waf" });
    fireEvent.change(screen.getByLabelText("Alert status"), { target: { value: "RESOLVED" } });
    expect(navigate).toHaveBeenCalledWith("/alerts?status=RESOLVED&family=waf");
  });

  it("asks for the names of the selected sources by id and never for the inventory", async () => {
    const other = "00000000-0000-4000-8000-000000000002";
    await renderAt({ log_source_id: [SRC, other] });
    const sources = vi.mocked(api.logSources);
    expect(sources).toHaveBeenCalledTimes(1);
    expect(sources).toHaveBeenCalledWith({ ids: [SRC, other], limit: 2 });
    expect(screen.getByRole("link", { name: "Remove filter Log source: Perimeter firewalls" }))
      .toBeInTheDocument();
    // The old picker listed the whole inventory; there is none now.
    expect(screen.queryByLabelText("Log source")).toBeNull();
  });

  it("makes no source read at all when no source is selected", async () => {
    await renderAt({ severity: "HIGH" });
    expect(api.logSources).not.toHaveBeenCalled();
  });

  it("shows the id and says 'unknown source' for an id the inventory does not return", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue(pageOf([]));
    await renderAt({ log_source_id: SRC });
    const chip = screen.getByRole("link", {
      name: "Remove filter Log source: 00000000… (unknown source)",
    });
    expect(chip).toHaveTextContent("Log source: 00000000… (unknown source)");
    expect(within(chip).getByText("(unknown source)")).toHaveClass("muted");
  });

  it("names each active filter in a chip that removes just that filter", async () => {
    await renderAt({ severity: ["CRITICAL", "HIGH"], log_source_id: SRC, q: "edge" });
    const chip = screen.getByRole("link", { name: "Remove filter Severity: CRITICAL" });
    expect(chip).toHaveAttribute("href", `/alerts?severity=HIGH&log_source_id=${SRC}&q=edge`);
    expect(
      screen.getByRole("link", { name: "Remove filter Log source: Perimeter firewalls" }),
    ).toHaveAttribute("href", "/alerts?severity=CRITICAL&severity=HIGH&q=edge");
    expect(screen.getByRole("link", { name: "Clear all" })).toHaveAttribute("href", "/alerts");
  });

  it("sorts by a column header and reverses it on the second click", async () => {
    await renderAt({ status: "OPEN" });
    expect(screen.getByRole("columnheader", { name: /Severity/ })).toHaveAttribute("aria-sort", "none");
    expect(hrefOf(/Severity$/)).toBe("/alerts?status=OPEN&sort=severity");
  });

  it("marks the sorted column and offers the opposite order", async () => {
    await renderAt({ sort: "severity", order: "desc" });
    expect(screen.getByRole("columnheader", { name: /Severity/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    expect(screen.getByRole("link", { name: /Severity/ })).toHaveAttribute(
      "href",
      "/alerts?sort=severity&order=asc",
    );
  });

  it("carries repeated filters and the chip through the pagination links", async () => {
    list.mockResolvedValue(listPage([alert()], { total: 250 }));
    await renderAt({ severity: ["HIGH", "CRITICAL"], range: "7d" });
    const next = screen.getByRole("link", { name: "Next" }).getAttribute("href")!;
    expect(next).toBe("/alerts?severity=CRITICAL&severity=HIGH&range=7d&offset=100");
  });
});

describe("states", () => {
  it("says no alert matches the filters and offers to clear them", async () => {
    list.mockResolvedValue(listPage([]));
    await renderAt({ family: "ips", severity: "INFO" });
    const notice = screen.getByText(/No alert matches these filters\./).closest("div")!;
    expect(within(notice).getByRole("link", { name: "Clear filters" })).toHaveAttribute(
      "href",
      "/alerts",
    );
  });

  it("says nothing was raised when nothing is filtered", async () => {
    list.mockResolvedValue(listPage([]));
    await renderAt();
    expect(screen.getByText("No alerts have been raised.")).toBeInTheDocument();
    expect(screen.queryByText(/matches these filters/)).toBeNull();
  });

  it("shows the backend's refusal in words, never a bare 422", async () => {
    list.mockRejectedValue(new ApiError(422, "range exceeds the maximum of 365 days", "range exceeds the maximum of 365 days"));
    await renderAt({ since: "2020-01-01T00:00:00Z" });
    const alertBox = screen.getByRole("alert");
    expect(alertBox).toHaveTextContent("The backend refused these filters: range exceeds the maximum of 365 days");
    expect(alertBox).toHaveTextContent("Change or clear them.");
    expect(alertBox.textContent).not.toMatch(/422/);
  });

  it("names the permission when the list is forbidden", async () => {
    list.mockRejectedValue(new ApiError(403, "forbidden"));
    await renderAt({ severity: "HIGH" });
    expect(screen.getByRole("alert")).toHaveTextContent("read:alerts");
  });

  it("keeps the filter bar on screen when the list read fails", async () => {
    list.mockRejectedValue(new ApiError(500, "boom"));
    await renderAt({ severity: "HIGH" });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByLabelText("Search alert titles")).toBeInTheDocument();
  });
});

describe("Copy link", () => {
  it("is on the page and copies the view's own address, clean", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await renderAt({ severity: ["HIGH", "BOGUS"], range: "24h", q: "  " });
    fireEvent.click(screen.getByRole("button", { name: "Copy a link to this view" }));
    await screen.findByText("Link copied");
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/alerts?severity=HIGH&range=24h`);
  });
});

describe("the log source search picker", () => {
  const OTHER = "00000000-0000-4000-8000-000000000002";
  const box = () => screen.getByRole("combobox", { name: "Add a log source" });
  const type = (value: string) => fireEvent.change(box(), { target: { value } });

  it("reads nothing before two characters and never asks for the unfiltered list", async () => {
    await renderAt();
    type("p");
    await new Promise((r) => setTimeout(r, 400));
    expect(api.logSources).not.toHaveBeenCalled();
    expect(screen.getByText("Type at least 2 characters to search log sources.")).toBeInTheDocument();
  });

  it("asks the server for the first 20 matches of the typed text, once after the pause", async () => {
    await renderAt();
    type("pe");
    type("per");
    expect(api.logSources).not.toHaveBeenCalled();
    expect(await screen.findByRole("option", { name: "Perimeter firewalls" })).toBeInTheDocument();
    expect(api.logSources).toHaveBeenCalledTimes(1);
    expect(api.logSources).toHaveBeenCalledWith(
      { q: "per", limit: 20 },
      expect.any(AbortSignal),
    );
    expect(screen.getByText("2 matches")).toBeInTheDocument();
  });

  it("adds the chosen source as a filter, keeping what is typed in the search", async () => {
    await renderAt({ severity: "HIGH" });
    fireEvent.change(screen.getByLabelText("Search alert titles"), { target: { value: "edge" } });
    type("per");
    fireEvent.click(await screen.findByRole("option", { name: "Perimeter firewalls" }));
    expect(navigate).toHaveBeenCalledWith(`/alerts?severity=HIGH&log_source_id=${SRC}&q=edge`);
  });

  it("is operable from the keyboard and does not offer a source twice", async () => {
    await renderAt({ log_source_id: SRC });
    vi.mocked(api.logSources).mockClear();
    type("po");
    const option = await screen.findByRole("option", { name: "Public portal WAF" });
    expect(screen.queryByRole("option", { name: "Perimeter firewalls" })).toBeNull();
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(box()).toHaveAttribute("aria-activedescendant", option.id);
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(navigate).toHaveBeenCalledWith(`/alerts?log_source_id=${SRC}&log_source_id=${OTHER}`);
  });

  it("says how many of how many are listed when the search is broader than 20", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue(pageOf([{ id: SRC, name: "Edge 1" } as never], 1234));
    await renderAt();
    type("edge");
    expect(await screen.findByText("1 of 1,234 — refine the search")).toBeInTheDocument();
  });

  it("says no source matches", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue(pageOf([]));
    await renderAt();
    type("zzz");
    expect(await screen.findByText("No log source matches “zzz”.")).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Matching log sources" })).queryAllByRole("option")).toHaveLength(0);
  });

  it("says the search failed and what to do, and keeps the rest of the page", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(500, "boom"));
    await renderAt();
    type("per");
    expect(await screen.findByText(/could not be searched\. Check the connection, then retry\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Event volume spike/ })).toBeInTheDocument();
  });

  it("retries the same search from the Retry button", async () => {
    const read = vi.spyOn(api, "logSources").mockRejectedValueOnce(new ApiError(500, "boom"));
    await renderAt();
    type("per");
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("option", { name: "Perimeter firewalls" })).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("names the permission when the search is forbidden", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(403, "no"));
    await renderAt();
    type("per");
    expect(await screen.findByText(/needs the read:log_sources permission/)).toBeInTheDocument();
  });
});
