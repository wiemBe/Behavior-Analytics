import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "./page";
import { ApiError, api, type Alert } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails: a bare "Backend unreachable" notice;
//  - no alerts: the terse "No alerts." notice;
//  - the normal list: the lifecycle explainer, the six columns, the per-row link, the
//    severity and status pills and the occurrence count;
//  - honesty: an alert that has never been seen again shows an em dash for "Last seen".
// The severity and status pills carry a tone class the page uses as meaning
// (sevTone for severity; OPEN -> crit, ACKNOWLEDGED -> warn, else ok), so those are
// asserted. The last block asserts the honesty gap T-096 recorded (the seen times'
// timezone).

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a-1",
    fingerprint: "vol:227:1789",
    title: "Event volume spike on Core Firewall",
    description: null,
    severity: "HIGH",
    status: "OPEN",
    source_type: "ANOMALY",
    opened_at: "2026-10-15T07:00:00Z",
    first_seen_at: "2026-10-15T07:00:00Z",
    last_seen_at: "2026-10-15T08:30:00Z",
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 4,
    evidence_snapshot: {},
    source_anomaly_ids: ["an-1"],
    ...over,
  };
}

async function renderPage() {
  return render(await AlertsPage());
}

beforeEach(() => vi.restoreAllMocks());

describe("alert list states", () => {
  it("explains a failed read", async () => {
    vi.spyOn(api, "alerts").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Alerts" })).toBeInTheDocument();
    const notice = screen.getByText("Backend unreachable.");
    expect(notice).not.toHaveTextContent("secret");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says there are no alerts", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No alerts.")).toBeInTheDocument();
  });
});

describe("alert list contents", () => {
  it("states the lifecycle and lists every column", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([alert()]);
    await renderPage();
    expect(screen.getByText(/Lifecycle OPEN → ACKNOWLEDGED → RESOLVED\./)).toBeInTheDocument();
    const table = screen.getByRole("table");
    for (const header of [
      "Title",
      "Severity",
      "Status",
      "Occurrences",
      "First seen",
      "Last seen",
    ]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByRole("link", { name: "Event volume spike on Core Firewall" })).toHaveAttribute(
      "href",
      "/alerts/a-1",
    );
    const row = within(table).getAllByRole("row")[1];
    expect(within(row).getByText("HIGH")).toHaveClass("crit");
    expect(within(row).getByText("OPEN")).toHaveClass("crit");
    expect(row).toHaveTextContent("4");
  });

  it("tones acknowledged and resolved alerts differently", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([
      alert({ id: "a-1", title: "Acked", status: "ACKNOWLEDGED" }),
      alert({ id: "a-2", title: "Done", status: "RESOLVED" }),
    ]);
    await renderPage();
    expect(screen.getByText("ACKNOWLEDGED")).toHaveClass("warn");
    expect(screen.getByText("RESOLVED")).toHaveClass("ok");
  });

  it("draws a never-repeated alert's last seen as an em dash", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([alert({ last_seen_at: null })]);
    await renderPage();
    const row = screen.getByRole("table").querySelector("tbody tr") as HTMLElement;
    expect(within(row).getByText("—")).toBeInTheDocument();
  });
});

describe("alert list honesty", () => {
  // `toLocaleString()` rendered the seen times in the viewer's zone with no zone name.
  it("renders the first- and last-seen times with their timezone", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([
      alert({ first_seen_at: "2026-10-15T07:00:00Z", last_seen_at: "2026-10-15T08:30:00Z" }),
    ]);
    await renderPage();
    expect(screen.getByText("10-15 07:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("10-15 08:30 UTC")).toBeInTheDocument();
  });

  it("names the page with the one h1, states the zone, and scrolls the table in a region", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([alert()]);
    await renderPage();
    expect(screen.getByRole("heading", { name: "Alerts", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Alerts" })).getByRole("table")).toBeInTheDocument();
  });

  it("right-aligns the occurrence count", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue([alert()]);
    await renderPage();
    expect(screen.getByText("4")).toHaveClass("num");
  });

  it("says what failed as an alert and what to do", async () => {
    vi.spyOn(api, "alerts").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
  });
});
