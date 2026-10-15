import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LogSourceDetailPage from "./page";
import { ApiError, api, type LogSourceDetail } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails (it catches everything, so a 500 and a 404 look the same): a
//    "Could not load log source <id>" notice;
//  - the normal detail: the header line, the five-part health breakdown, the
//    configuration card and the Phase-2 chart placeholder;
//  - a null health breakdown: the breakdown cards are simply absent;
//  - honesty: a missing type reads "unknown type", a missing owner / description /
//    expected interval is an em dash, a source that has never sent an event says
//    "never", and a missing QRadar status reads "unknown".
// The last block asserts the honesty gaps T-096 recorded (the last-event timezone).

function detail(over: Partial<LogSourceDetail> = {}): LogSourceDetail {
  return {
    id: "s-1",
    qradar_id: 227,
    enabled: true,
    monitoring_enabled: true,
    maintenance_mode: false,
    timezone_name: "UTC",
    name: "Core Firewall",
    type_name: "Check Point Firewall",
    description: "Perimeter firewall",
    criticality: "HIGH",
    owner: "SOC",
    owner_email: "soc@example.com",
    qradar_status: "SUCCESS",
    health_score: 85,
    health_breakdown: { score: 85, freshness: 90, volume: 80, parsing: 95, collection: 70 },
    business_hours_only: false,
    expected_interval_seconds: 300,
    last_event_time: "2026-10-15T08:59:00Z",
    ...over,
  };
}

async function renderPage(id = "s-1") {
  return render(await LogSourceDetailPage({ params: Promise.resolve({ id }) }));
}

beforeEach(() => vi.restoreAllMocks());

describe("log source detail states", () => {
  it("names the source it could not load", async () => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage("s-9");
    expect(screen.getByRole("heading", { name: "Log Source Detail" })).toBeInTheDocument();
    const notice = screen.getByText(/Could not load log source/);
    expect(notice).toHaveTextContent("s-9");
    expect(notice).not.toHaveTextContent("secret");
  });

  it("shows the header line with type, criticality and owner", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    await renderPage();
    expect(screen.getByRole("heading", { name: "Core Firewall", level: 2 })).toBeInTheDocument();
    expect(
      screen.getByText("Check Point Firewall · criticality HIGH · owner SOC"),
    ).toBeInTheDocument();
  });
});

describe("log source detail contents", () => {
  beforeEach(() => vi.spyOn(api, "logSource").mockResolvedValue(detail()));

  it("breaks the health score into its weighted parts", async () => {
    await renderPage();
    const card = (label: string) =>
      screen.getByText(label, { selector: ".k" }).closest(".card") as HTMLElement;
    expect(within(card("Overall Health")).getByText("85")).toBeInTheDocument();
    expect(within(card("Freshness (40%)")).getByText("90")).toBeInTheDocument();
    expect(within(card("Volume (25%)")).getByText("80")).toBeInTheDocument();
    expect(within(card("Parsing (20%)")).getByText("95")).toBeInTheDocument();
    expect(within(card("Collection (15%)")).getByText("70")).toBeInTheDocument();
  });

  it("shows the configuration in the order it is read", async () => {
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Description: Perimeter firewall");
    expect(config).toHaveTextContent("Business hours only: no");
    expect(config).toHaveTextContent("Expected interval: 300 s");
    expect(config).toHaveTextContent("QRadar status: SUCCESS");
  });

  it("states what the detail page does not yet show", async () => {
    await renderPage();
    expect(screen.getByText(/EPS trend, anomaly timeline and baseline overlay charts/)).toBeInTheDocument();
  });

  it("omits the health breakdown when the source has none", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ health_breakdown: null }));
    await renderPage();
    expect(screen.queryByText("Overall Health")).toBeNull();
    expect(screen.getByText("Configuration", { selector: ".k" })).toBeInTheDocument();
  });

  it("draws missing values as unknown, never, or an em dash", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(
      detail({
        type_name: null,
        owner: null,
        description: null,
        expected_interval_seconds: null,
        last_event_time: null,
        qradar_status: null,
        business_hours_only: true,
      }),
    );
    await renderPage();
    expect(screen.getByText(/unknown type · criticality HIGH · owner —/)).toBeInTheDocument();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Description: —");
    expect(config).toHaveTextContent("Business hours only: yes");
    expect(config).toHaveTextContent("Expected interval: — s");
    expect(config).toHaveTextContent("Last event: never");
    expect(config).toHaveTextContent("QRadar status: unknown");
  });
});

describe("log source detail honesty", () => {
  // `toLocaleString()` rendered the viewer's zone with no zone name.
  it("renders the last-event time with its timezone", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(
      detail({ last_event_time: "2026-10-15T08:59:00Z" }),
    );
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Last event: 2026-10-15 08:59:00 UTC");
  });

  it("says why there is no health breakdown instead of showing nothing", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ health_breakdown: null }));
    await renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("No health score yet");
  });

  it("carries no inline style (the page moved them to a stylesheet)", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    const { container } = await renderPage();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("says what failed and what to do as an alert", async () => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(404, "secret"));
    await renderPage("s-9");
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
    expect(screen.getByRole("heading", { name: "Log Source Detail", level: 1 })).toBeInTheDocument();
  });
});
