import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "./page";
import AlertsLoading from "./loading";
import { ApiError, api, type Alert, type Page } from "@/lib/api";
import { setTestLocale } from "../../../vitest.setup";

// States this route renders, from `page.tsx`:
//  - the read fails: the 403 / 401 / failure sentence from `loadErrorMessage`;
//  - no alerts: copy specific to the selected population;
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

function page(
  items: Alert[],
  over: Partial<Page<Alert>> = {},
): Page<Alert> {
  return { items, total: items.length, limit: 100, offset: 0, ...over };
}

async function renderPage(params: Record<string, string | undefined> = {}) {
  return render(await AlertsPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("en");
});

describe("alert list states", () => {
  it("names the list while the route is loading", () => {
    render(<AlertsLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading alerts…");
  });

  it("keeps the heading when the read fails", async () => {
    vi.spyOn(api, "alerts").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Alerts" })).toBeInTheDocument();
  });

  it.each([
    [403, new ApiError(403, "secret"), "You do not have permission to view alerts. Ask an administrator for the read:alerts grant."],
    [401, new ApiError(401, "secret"), "Your session has expired. Sign in again to continue."],
    [500, new ApiError(500, "secret"), "Alerts could not be loaded. The backend may be unreachable; reload the page to try again."],
    ["network", new Error("fetch failed"), "Alerts could not be loaded. The backend may be unreachable; reload the page to try again."],
  ])("names what failed for a %s read failure, with no developer instruction", async (_case, error, sentence) => {
    vi.spyOn(api, "alerts").mockRejectedValue(error);
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    for (const gone of ["Backend unreachable", "Start the stack", "POST", "secret", "!"]) {
      expect(alert).not.toHaveTextContent(gone);
    }
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says there are no alerts", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([]));
    await renderPage();
    expect(screen.getByText("No alerts have been raised.")).toBeInTheDocument();
  });

  it("says when the OPEN population is empty", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([]));
    await renderPage({ status: "OPEN" });
    expect(screen.getByText("No open alerts.")).toBeInTheDocument();
  });
});

describe("alert list contents", () => {
  it("renders coded search and custom-rule titles in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "alerts").mockResolvedValue(page([
      alert({
        id: "search",
        title: "Daily failures crossed its threshold",
        title_code: "alert.threshold.crossed",
        title_params: { name: "Günlük hatalar" },
      }),
      alert({
        id: "rule",
        title: "High traffic crossed its threshold",
        title_code: "alert.threshold.crossed",
        title_params: { name: "Yüksek trafik" },
      }),
    ]));
    await renderPage();
    expect(screen.getByRole("link", { name: "Günlük hatalar eşiğini aştı" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Yüksek trafik eşiğini aştı" })).toBeInTheDocument();
  });

  it("renders each engine's coded title in Turkish, with the detector labelled from its enum", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "alerts").mockResolvedValue(page([
      alert({
        id: "phase-a",
        title: "VOLUME_DROP on Core Firewall",
        title_code: "alert.anomaly.title",
        title_params: { anomaly_type: "VOLUME_DROP", log_source: "Core Firewall" },
      }),
      alert({
        id: "waf",
        title: "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}",
        title_code: "alert.waf.title",
        title_params: {
          detector: "WAF_REQUEST_RATE_SPIKE",
          log_source: "edge-waf",
          method: "POST",
          endpoint: "/login/{id}",
        },
      }),
      alert({
        id: "ips",
        title: "IPS_PORT_RATE_SPIKE on ips-412: destination port 3389/tcp",
        title_code: "alert.ips.title.destination_port",
        title_params: { detector: "IPS_PORT_RATE_SPIKE", log_source: "ips-412", entity: "3389/tcp" },
      }),
      alert({
        id: "pw",
        title: "PW_VALUE_SPIKE on Admin logins: alice | 10.0.0.1",
        title_code: "alert.pw.title",
        title_params: { detector: "PW_VALUE_SPIKE", watch: "Admin logins", value: "alice | 10.0.0.1" },
      }),
    ]));
    await renderPage();
    expect(screen.getByRole("link", { name: "Core Firewall üzerinde hacim düşüşü" })).toBeInTheDocument();
    expect(screen.getByRole("link", {
      name: "edge-waf üzerinde istek hızı artışı: POST /login/{id}",
    })).toBeInTheDocument();
    expect(screen.getByRole("link", {
      name: "ips-412 üzerinde hedef port hızı artışı: hedef port 3389/tcp",
    })).toBeInTheDocument();
    expect(screen.getByRole("link", {
      name: "Admin logins üzerinde değer ani artışı: alice | 10.0.0.1",
    })).toBeInTheDocument();
  });

  it("keeps each engine's English title exactly as stored", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert({
      title: "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}",
      title_code: "alert.waf.title",
      title_params: {
        detector: "WAF_REQUEST_RATE_SPIKE",
        log_source: "edge-waf",
        method: "POST",
        endpoint: "/login/{id}",
      },
    })]));
    await renderPage();
    expect(screen.getByRole("link", {
      name: "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}",
    })).toBeInTheDocument();
  });

  it("keeps coded English titles byte for byte", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert({
      title: "Stored English exactly",
      title_code: "alert.threshold.crossed",
      title_params: { name: "Ignored in English" },
    })]));
    await renderPage();
    expect(screen.getByRole("link", { name: "Stored English exactly" })).toBeInTheDocument();
  });

  it("states the lifecycle and lists every column", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert()]));
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

  it("groups a large occurrence count", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert({ occurrence_count: 1200 })]));
    await renderPage();
    const row = screen.getByRole("table").querySelector("tbody tr") as HTMLElement;
    expect(row).toHaveTextContent("1,200");
  });

  it("tones acknowledged and resolved alerts differently", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([
      alert({ id: "a-1", title: "Acked", status: "ACKNOWLEDGED" }),
      alert({ id: "a-2", title: "Done", status: "RESOLVED" }),
    ]));
    await renderPage();
    expect(screen.getByText("ACKNOWLEDGED")).toHaveClass("warn");
    expect(screen.getByText("RESOLVED")).toHaveClass("ok");
  });

  it("draws a never-repeated alert's last seen as an em dash", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert({ last_seen_at: null })]));
    await renderPage();
    const row = screen.getByRole("table").querySelector("tbody tr") as HTMLElement;
    expect(within(row).getByText("—")).toBeInTheDocument();
  });
});

describe("alert list honesty", () => {
  // `toLocaleString()` rendered the seen times in the viewer's zone with no zone name.
  it("renders the first- and last-seen times with their timezone", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([
      alert({ first_seen_at: "2026-10-15T07:00:00Z", last_seen_at: "2026-10-15T08:30:00Z" }),
    ]));
    await renderPage();
    expect(screen.getByText("10-15 07:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("10-15 08:30 UTC")).toBeInTheDocument();
  });

  it("names the page with the one h1, states the zone, and scrolls the table in a region", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert()]));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Alerts", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Alerts" })).getByRole("table")).toBeInTheDocument();
  });

  it("right-aligns the occurrence count", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([alert()]));
    await renderPage();
    expect(screen.getByText("4")).toHaveClass("num");
  });

  it("says what failed as an alert and what to do", async () => {
    vi.spyOn(api, "alerts").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
  });
});

describe("alert list paging and filters", () => {
  it("loads the selected status and offset with the bounded page size", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(
      page([alert()], { total: 201, offset: 100 }),
    );
    await renderPage({ status: "OPEN", offset: "100" });

    expect(api.alerts).toHaveBeenCalledWith({
      status: "OPEN",
      limit: 100,
      offset: 100,
    });
    expect(screen.getByRole("combobox", { name: "Alert status" })).toHaveValue("OPEN");
  });

  it("states the cap and keeps the status on pagination links", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(
      page([alert()], { total: 201, limit: 100 }),
    );
    await renderPage({ status: "OPEN" });

    expect(screen.getByText("Showing 1 of 201 alerts.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/alerts?status=OPEN&offset=100",
    );
  });

  it("defaults to all statuses and drops an invalid status", async () => {
    vi.spyOn(api, "alerts").mockResolvedValue(page([]));
    await renderPage({ status: "NOT_A_STATUS" });

    expect(api.alerts).toHaveBeenCalledWith({
      status: undefined,
      limit: 100,
      offset: 0,
    });
    expect(screen.getByRole("combobox", { name: "Alert status" })).toHaveValue("");
  });
});
