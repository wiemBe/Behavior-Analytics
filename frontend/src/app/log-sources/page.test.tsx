import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LogSourcesPage from "./page";
import { ApiError, api, type LogSourceSummary } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails: the 403 / 401 / failure sentence from `loadErrorMessage`;
//  - no sources (a successful empty read): the "No log sources yet" notice;
//  - the normal inventory: the source count, the table, the maintenance marker and
//    the status / anomaly pills;
//  - honesty: a missing type is an em dash, a missing QRadar status reads "unknown",
//    a zero anomaly count is a plain "0" and a
//    source that has never sent an event says "never".
// The status and maintenance pills carry a tone class the page uses as
// meaning (SUCCESS -> ok, otherwise crit),
// so those are asserted. The honesty gaps T-096 recorded are asserted in the last block.

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
    open_anomaly_count: 0,
    ...over,
  };
}

async function renderPage() {
  return render(await LogSourcesPage());
}

beforeEach(() => vi.restoreAllMocks());

describe("log source list states", () => {
  it("keeps the heading when the read fails", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Log Sources" })).toBeInTheDocument();
  });

  it.each([
    [403, new ApiError(403, "secret"), "You do not have permission to view log sources. Ask an administrator for the read:log_sources grant."],
    [401, new ApiError(401, "secret"), "Your session has expired. Sign in again to continue."],
    [500, new ApiError(500, "secret"), "Log sources could not be loaded. The backend may be unreachable; reload the page to try again."],
    ["network", new Error("fetch failed"), "Log sources could not be loaded. The backend may be unreachable; reload the page to try again."],
  ])("names what failed for a %s read failure, with no developer instruction", async (_case, error, sentence) => {
    vi.spyOn(api, "logSources").mockRejectedValue(error);
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    for (const gone of ["Backend unreachable", "Start the stack", "POST", "secret", "!"]) {
      expect(alert).not.toHaveTextContent(gone);
    }
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
  });

  it("has no Health column: no score is computed, so none is drawn", async () => {
    await renderPage();
    expect(screen.queryByRole("columnheader", { name: "Health" })).toBeNull();
    expect(within(screen.getByRole("table")).getAllByRole("columnheader")).toHaveLength(6);
  });

  it("marks a source in maintenance and tones its status", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([
      source({ maintenance_mode: true }),
    ]);
    await renderPage();
    expect(screen.getByText("maint")).toHaveClass("warn");
    expect(screen.getByText("SUCCESS")).toHaveClass("ok");
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
      source({ type_name: null, qradar_status: null, last_event_time: null }),
    ]);
    await renderPage();
    const row = screen.getByRole("table").querySelector("tbody tr") as HTMLElement;
    // The type is unmeasured.
    expect(within(row).getAllByText("—")).toHaveLength(1);
    expect(within(row).getByText("unknown")).toBeInTheDocument();
    expect(row).toHaveTextContent("never");
  });
});

describe("log source list honesty", () => {
  // The subtitle used to count the array the page happened to hold, so a failed read
  // read "0 sources". A count the page could not measure must not be drawn as zero.
  it("does not render '0 sources' in the subtitle when the read failed", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.queryByText(/\d+ sources/)).toBeNull();
    expect(screen.getByText(/QRadar-owned fields refresh on sync/)).toBeInTheDocument();
  });

  // A null QRadar status used to be drawn in the alarm ("crit") tone although it is unknown.
  it("tones an unknown QRadar status neutrally, not as critical", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([source({ qradar_status: null })]);
    await renderPage();
    const pill = screen.getByText("unknown");
    expect(pill).toHaveClass("pill");
    expect(pill).not.toHaveClass("crit");
    expect(pill).not.toHaveClass("ok");
    expect(pill).not.toHaveClass("warn");
  });

  it("still tones a reported non-SUCCESS status as a problem", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([source({ qradar_status: "ERROR" })]);
    await renderPage();
    expect(screen.getByText("ERROR")).toHaveClass("crit");
  });

  // The last-event time used to be locale-formatted with no timezone.
  it("renders the last-event time with its timezone like the rest of the app", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([
      source({ last_event_time: "2026-10-15T08:59:00Z" }),
    ]);
    await renderPage();
    expect(screen.getByText("10-15 08:59 UTC")).toBeInTheDocument();
  });
});

describe("log source list layout", () => {
  it("names the page with the one h1 and keeps the table inside a scroll region", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([source()]);
    await renderPage();
    expect(screen.getByRole("heading", { name: "Log Sources", level: 1 })).toBeInTheDocument();
    const region = screen.getByRole("region", { name: "Log sources" });
    expect(within(region).getByRole("table")).toBeInTheDocument();
  });

  it("right-aligns the anomaly figure", async () => {
    vi.spyOn(api, "logSources").mockResolvedValue([source()]);
    await renderPage();
    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    const cells = within(row).getAllByRole("cell");
    expect(cells[4]).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: "Anomalies" })).toHaveClass("num");
  });

  it("says what failed as an alert", async () => {
    vi.spyOn(api, "logSources").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("Log sources could not be loaded.");
  });
});

describe("QRadar status tones", () => {
  it.each([
    ["SUCCESS", "ok"],
    ["WARNING", "warn"],
    ["ERROR", "crit"],
  ])("tones %s as %s", async (status, tone) => {
    vi.spyOn(api, "logSources").mockResolvedValue([source({ qradar_status: status })]);
    await renderPage();
    expect(screen.getByText(status, { selector: ".pill" })).toHaveClass(tone);
  });

  it.each(["DISABLED", "NA"])("keeps %s neutral and labels it as QRadar reported it", async (status) => {
    vi.spyOn(api, "logSources").mockResolvedValue([source({ qradar_status: status })]);
    await renderPage();
    const pill = screen.getByText(status, { selector: ".pill" });
    for (const t of ["ok", "warn", "crit"]) expect(pill).not.toHaveClass(t);
  });
});
