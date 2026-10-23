import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SearchCatalogPage from "./page";
import { ApiError, api, type ScheduledSearch } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails: the 403 / 401 / failure sentence from `loadErrorMessage`;
//  - no searches defined: the "No scheduled searches defined yet" notice;
//  - the normal catalog: the labelled, focusable table region, its columns, the
//    per-row link, the schedule label and the severity / enabled pills;
//  - honesty: a missing category is an em dash, an empty MITRE list is an em dash,
//    an unrecognised cron shows "Custom" with the raw cron kept visible, and a
//    disabled search is a warn pill reading "no".
// The severity pill class is asserted because the page uses it as meaning
// (sevTone: CRITICAL/HIGH -> crit, MEDIUM -> warn, LOW/INFO -> info).

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1",
    name: "Repeated authentication failures",
    description: "Counts failures per source",
    owner: "SOC",
    category: "Credential Access",
    mitre_techniques: ["T1110"],
    aql_query: 'SELECT count(*) FROM events WHERE username IS NOT NULL',
    query_version: 2,
    schedule_cron: "0 */4 * * *",
    schedule_timezone: "UTC",
    severity: "HIGH",
    threshold_value: 10,
    threshold_operator: "GT",
    timeout_seconds: 300,
    max_time_range_hours: 24,
    max_result_rows: 500,
    visualization_type: "table",
    enabled: true,
    last_run_at: "2026-10-15T04:00:00Z",
    next_run_at: "2026-10-15T08:00:00Z",
    consecutive_failures: 0,
    ...over,
  };
}

async function renderPage() {
  return render(await SearchCatalogPage());
}

const reader = {
  subject: "analyst",
  email: null,
  permissions: ["read:*", "search:execute"],
  effective_permissions: ["read:searches", "search:execute"],
};

const writer = {
  subject: "ada",
  email: null,
  permissions: ["search:write"],
  effective_permissions: ["read:searches", "search:write", "search:execute"],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "caller").mockResolvedValue(reader);
});

describe("search catalog states", () => {
  it("keeps the heading when the read fails", async () => {
    vi.spyOn(api, "searches").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Search Catalog" })).toBeInTheDocument();
  });

  it.each([
    [403, new ApiError(403, "secret"), "You do not have permission to view searches. Ask an administrator for the read:searches grant."],
    [401, new ApiError(401, "secret"), "Your session has expired. Sign in again to continue."],
    [500, new ApiError(500, "secret"), "Searches could not be loaded. The backend may be unreachable; reload the page to try again."],
    ["network", new Error("fetch failed"), "Searches could not be loaded. The backend may be unreachable; reload the page to try again."],
  ])("names what failed for a %s read failure, with no developer instruction", async (_case, error, sentence) => {
    vi.spyOn(api, "searches").mockRejectedValue(error);
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    for (const gone of ["Backend unreachable", "Start the stack", "POST", "secret", "!"]) {
      expect(alert).not.toHaveTextContent(gone);
    }
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says the catalog is empty", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No scheduled searches defined yet.")).toBeInTheDocument();
  });
});

describe("search catalog contents", () => {
  it("lists the stored, validated searches in a labelled region", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([search()]);
    await renderPage();
    const region = screen.getByRole("region", { name: "Scheduled searches" });
    const table = within(region).getByRole("table");
    for (const header of ["Name", "Category", "MITRE", "Schedule", "Severity", "Ver", "Enabled"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByRole("link", { name: "Repeated authentication failures" })).toHaveAttribute(
      "href",
      "/searches/s-1",
    );
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Credential Access");
    expect(row).toHaveTextContent("T1110");
    // The preset cron is read as a schedule, not as a raw expression.
    expect(row).toHaveTextContent("Every 4 h");
    expect(row).toHaveTextContent("v2");
    expect(row).toHaveTextContent("yes");
  });

  it("tones severity as a finding and never green", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([
      search({ id: "s-1", name: "Critical one", severity: "CRITICAL" }),
      search({ id: "s-2", name: "Low one", severity: "LOW" }),
    ]);
    await renderPage();
    expect(screen.getByText("CRITICAL")).toHaveClass("crit");
    expect(screen.getByText("LOW")).toHaveClass("info");
  });

  it("keeps a custom cron visible and marks a disabled search", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([
      search({ schedule_cron: "17 3 * * 1", severity: "MEDIUM", enabled: false, category: null, mitre_techniques: [] }),
    ]);
    await renderPage();
    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Custom");
    expect(within(row).getByText("17 3 * * 1").tagName).toBe("CODE");
    expect(screen.getByText("MEDIUM")).toHaveClass("warn");
    const disabled = screen.getByText("no");
    expect(disabled).toHaveClass("warn");
    // A missing category and an empty MITRE list are em dashes.
    expect(within(row).getAllByText("—")).toHaveLength(2);
  });
});

describe("search catalog layout", () => {
  it("names the page with the one h1", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([search()]);
    await renderPage();
    expect(screen.getByRole("heading", { name: "Search Catalog", level: 1 })).toBeInTheDocument();
  });

  it("says what failed as an alert and what to do", async () => {
    vi.spyOn(api, "searches").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
  });
});

describe("search catalog authoring entry", () => {
  it("offers New search to a caller who can write", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    vi.spyOn(api, "searches").mockResolvedValue([search()]);
    await renderPage();
    expect(screen.getByRole("link", { name: "New search" })).toHaveAttribute("href", "/searches/new");
  });

  it("hides New search from a reader", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([search()]);
    await renderPage();
    expect(screen.queryByRole("link", { name: "New search" })).toBeNull();
  });

  it("tells a writer how to store the first search", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    vi.spyOn(api, "searches").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No scheduled searches defined yet.")).toBeInTheDocument();
    expect(
      screen.getByText(/Use New search to store a disabled definition/, { selector: "p" }),
    ).toBeInTheDocument();
  });

  it("tells a reader which role can grant search:write when the catalog is empty", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No scheduled searches defined yet.")).toBeInTheDocument();
    expect(screen.getByText("search:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
  });
});
