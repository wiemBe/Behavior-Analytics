import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SearchCatalogPage from "./page";
import { ApiError, api, type ScheduledSearch } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails: a bare "Backend unreachable" notice;
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

beforeEach(() => vi.restoreAllMocks());

describe("search catalog states", () => {
  it("explains a failed read", async () => {
    vi.spyOn(api, "searches").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Search Catalog" })).toBeInTheDocument();
    const notice = screen.getByText("Backend unreachable.");
    expect(notice).not.toHaveTextContent("secret");
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
