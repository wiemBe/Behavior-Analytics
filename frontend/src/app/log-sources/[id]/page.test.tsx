import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LogSourceDetailPage from "./page";
import { ApiError, api, type LogSourceDetail } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - the read fails: 401 session expired, 403 names read:log_sources, 404 is not
//    in the inventory, and any other failure says the backend could not be reached;
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
  it("says the backend could not be reached on a non-auth failure", async () => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage("s-9");
    expect(screen.getByRole("heading", { name: "Log Source Detail" })).toBeInTheDocument();
    const notice = screen.getByRole("alert");
    expect(notice).toHaveTextContent("The backend could not be reached. Reload the page to try again.");
    expect(notice).not.toHaveTextContent("secret");
  });

  it("shows the header line with type, criticality and owner", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    await renderPage();
    expect(screen.getByRole("heading", { name: "Core Firewall", level: 1 })).toBeInTheDocument();
    expect(
      screen.getByText("Check Point Firewall · criticality HIGH · owner SOC"),
    ).toBeInTheDocument();
  });
});

describe("log source detail contents", () => {
  beforeEach(() => vi.spyOn(api, "logSource").mockResolvedValue(detail()));

  it("has no health section: no score is computed, so none is promised", async () => {
    await renderPage();
    expect(screen.queryByText(/health/i)).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("Configuration", { selector: ".k" })).toBeInTheDocument();
  });

  it("shows the configuration in the order it is read", async () => {
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Description: Perimeter firewall");
    expect(config).toHaveTextContent("Business hours only: no");
    expect(config).toHaveTextContent("Expected interval: 300 s");
    expect(config).toHaveTextContent("QRadar status: SUCCESS");
  });

  it("links to the charts instead of promising them", async () => {
    await renderPage();
    expect(screen.queryByText(/arrive in Phase 2/)).toBeNull();
    expect(screen.getByRole("link", { name: "source behavior" })).toHaveAttribute(
      "href",
      "/behavior/sources/s-1",
    );
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
    expect(config).toHaveTextContent(/Expected interval: —Last event/);
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

  it("carries no inline style (the page moved them to a stylesheet)", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    const { container } = await renderPage();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it.each([
    [401, "Your session has expired. Sign in again to continue."],
    [403, "Ask an administrator for the read:log_sources grant."],
    [404, "Log source s-9 is not in the inventory."],
    [500, "The backend could not be reached. Reload the page to try again."],
  ])("splits a %s read into its own sentence", async (status, sentence) => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(status, "secret"));
    await renderPage("s-9");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    expect(alert).not.toHaveTextContent("secret");
    if (status === 404) expect(alert).not.toHaveTextContent("Reload the page");
  });

  it("gives a missing expected interval no unit", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ expected_interval_seconds: null }));
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config.textContent).toContain("Expected interval: —");
    expect(config.textContent).not.toMatch(/—\s*s/);
  });

  it.each([
    ["SUCCESS", "ok"],
    ["WARNING", "warn"],
    ["ERROR", "crit"],
  ])("tones the QRadar status %s as %s", async (status, tone) => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ qradar_status: status }));
    await renderPage();
    expect(screen.getByText(status, { selector: ".pill" })).toHaveClass(tone);
  });

  it.each(["DISABLED", "NA", null])("keeps the status %s neutral", async (status) => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ qradar_status: status }));
    await renderPage();
    const pill = screen.getByText(status ?? "unknown", { selector: ".pill" });
    for (const t of ["ok", "warn", "crit"]) expect(pill).not.toHaveClass(t);
  });
});
