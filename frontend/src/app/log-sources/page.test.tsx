import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LogSourcesPage from "./page";
import { ApiError, api, type LogSourceSummary } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails: a "Backend unreachable" notice naming the sync endpoint;
//  - no sources (a successful empty read): the "No log sources yet" notice;
//  - the normal inventory: the source count, the table, the maintenance marker and
//    the status / health / anomaly pills;
//  - honesty: a missing type is an em dash, a missing QRadar status reads "unknown",
//    a missing health score is an em dash, a zero anomaly count is a plain "0" and a
//    source that has never sent an event says "never".
// The status, health and maintenance pills carry a tone class the page uses as
// meaning (SUCCESS -> ok, otherwise crit; health >= 70 ok, >= 40 warn, else crit),
// so those are asserted. Two honesty gaps the current page does not meet are recorded
// as `it.todo` at the end.

function source(over: Partial<LogSourceSummary> = {}): LogSourceSummary {
  return {
    id: "s-1",
    qradar_id: 227,
    name: "Core Firewall",
    type_name: "Check Point Firewall",
    criticality: "HIGH",
    owner: "SOC",
    enabled: true,
    monitoring_enabled: true,
    maintenance_mode: false,
    qradar_status: "SUCCESS",
    last_event_time: "2026-10-15T08:59:00Z",
    health_score: 85,
    open_anomaly_count: 0,
    ...over,
  };
}

async function renderPage() {
  return render(await LogSourcesPage());
}

beforeEach(() => vi.restoreAllMocks());

describe("log source list states", () => {
  it("explains a failed read and names the sync endpoint", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Log Sources" })).toBeInTheDocument();
    const notice = screen.getByText(/Backend unreachable/);
    expect(notice).toHaveTextContent("POST /api/v1/log-sources/sync");
    expect(notice).not.toHaveTextContent("secret");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says what to do when no source has been collected", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([]);
    await renderPage();
    expect(
      screen.getByText(
        /No log sources yet\. Trigger an inventory sync to pull them from the configured provider\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/^0 sources\./)).toBeInTheDocument();
  });
});

describe("log source list contents", () => {
  beforeEach(() => vi.spyOn(api, "logSources").mockResolvedValue([source()]));

  it("keeps the SOC-owned fields visible and links each source", async () => {
    await renderPage();
    expect(screen.getByText(/^1 sources\./)).toBeInTheDocument();
    const table = screen.getByRole("table");
    for (const header of [
      "Name",
      "Type",
      "Criticality",
      "Status",
      "Health",
      "Anomalies",
      "Last Event",
    ]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByRole("link", { name: "Core Firewall" })).toHaveAttribute(
      "href",
      "/log-sources/s-1",
    );
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Check Point Firewall");
    expect(row).toHaveTextContent("HIGH");
    expect(row).toHaveTextContent("SUCCESS");
    expect(row).toHaveTextContent("85");
  });

  it("marks a source in maintenance and tones its status and health", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([
      source({ maintenance_mode: true, health_score: 45 }),
    ]);
    await renderPage();
    expect(screen.getByText("maint")).toHaveClass("warn");
    expect(screen.getByText("SUCCESS")).toHaveClass("ok");
    expect(screen.getByText("45")).toHaveClass("warn");
  });

  it("shows a positive anomaly count as an alarm and a zero as a plain zero", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([
      source({ id: "s-1", name: "Core Firewall", open_anomaly_count: 3 }),
      source({ id: "s-2", name: "DNS Resolver", open_anomaly_count: 0 }),
    ]);
    await renderPage();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("3");
    expect(within(rows[0]).getByText("3")).toHaveClass("warn");
    expect(rows[1]).toHaveTextContent("0");
    expect(within(rows[1]).getByText("0").className).not.toContain("pill");
  });

  it("draws missing values as em dashes and unknown, never zero", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([
      source({ type_name: null, qradar_status: null, health_score: null, last_event_time: null }),
    ]);
    await renderPage();
    const row = screen.getByRole("table").querySelector("tbody tr") as HTMLElement;
    // The type and the health score are both unmeasured.
    expect(within(row).getAllByText("—")).toHaveLength(2);
    expect(within(row).getByText("unknown")).toBeInTheDocument();
    expect(row).toHaveTextContent("never");
  });
});

describe("log source list honesty defects", () => {
  // The subtitle counts the array the page happened to hold, so a failed read still
  // reads "0 sources". A count the page could not measure must not be drawn as zero.
  it.todo("do not render '0 sources' in the subtitle when the read failed");
  // A null QRadar status is drawn in the alarm ("crit") tone although it is unknown.
  it.todo("tone an unknown QRadar status neutrally, not as critical");
  // The last-event time is locale-formatted with no timezone.
  it.todo("render the last-event time with its timezone like the rest of the app");
});
