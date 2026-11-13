import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { FULL_COVERAGE, clock, partialWaf, summary } from "./fixtures";
import { ReportsView } from "./ReportsView";

const mocks = vi.hoisted(() => ({ summary: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, reportsApi: { summary: mocks.summary } };
});

// Wednesday 2026-11-11 09:00 UTC.
const NOW = new Date("2026-11-11T09:00:00Z");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  mocks.summary.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

const mount = (props: { period?: string; start?: string } = {}) => render(<ReportsView {...props} />);

const section = (heading: string) => screen.getByRole("heading", { level: 2, name: heading }).closest("section")!;

describe("loading", () => {
  it("draws the chips and a loading line, and no figure or table", () => {
    mocks.summary.mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the report…");
    expect(screen.getByRole("link", { name: "Last 7 days" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText(/Range:/)).toBeNull();
  });
});

describe("the period chips", () => {
  beforeEach(() => {
    mocks.summary.mockReturnValue(new Promise(() => {}));
  });

  it("write the period to the URL, and the default stays bare", () => {
    mount();
    expect(screen.getByRole("link", { name: "Last 7 days" })).toHaveAttribute("href", "/reports");
    expect(screen.getByRole("link", { name: "Last 30 days" })).toHaveAttribute("href", "/reports?period=last_30d");
    expect(screen.getByRole("link", { name: "UTC week" })).toHaveAttribute("href", "/reports?period=calendar_week");
    expect(screen.getByRole("link", { name: "UTC month" })).toHaveAttribute("href", "/reports?period=calendar_month");
  });

  it("mark the period the URL names", () => {
    mount({ period: "last_30d" });
    expect(screen.getByRole("link", { name: "Last 30 days" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("link", { name: "Last 7 days" })).not.toHaveAttribute("aria-current");
    expect(mocks.summary).toHaveBeenCalledWith("last_30d", undefined);
  });

  it("fall back to the default for an unknown period and say so", () => {
    mount({ period: "yearly" });
    expect(screen.getByText("Ignored an unknown period; showing the last 7 days.")).toBeInTheDocument();
    expect(mocks.summary).toHaveBeenCalledWith("last_7d", undefined);
  });
});

describe("the calendar control", () => {
  beforeEach(() => {
    mocks.summary.mockReturnValue(new Promise(() => {}));
  });

  it("asks for the current week from its Monday and turns the next control off", () => {
    mount({ period: "calendar_week" });
    expect(mocks.summary).toHaveBeenCalledWith("calendar_week", "2026-11-09T00:00:00.000Z");
    expect(screen.getByText("Week starting 2026-11-09 UTC")).toBeInTheDocument();
    const next = screen.getByText("Next week");
    expect(next).toHaveAttribute("aria-disabled", "true");
    expect(next.closest("a")).toBeNull();
    expect(screen.getByRole("link", { name: "Previous week" })).toHaveAttribute(
      "href",
      "/reports?period=calendar_week&start=2026-11-02T00%3A00%3A00Z",
    );
  });

  it("steps back and forth, and the next control returns to the current week", () => {
    mount({ period: "calendar_week", start: "2026-11-02T00:00:00Z" });
    expect(mocks.summary).toHaveBeenCalledWith("calendar_week", "2026-11-02T00:00:00.000Z");
    expect(screen.getByRole("link", { name: "Next week" })).toHaveAttribute("href", "/reports?period=calendar_week");
    expect(screen.getByRole("link", { name: "Previous week" })).toHaveAttribute(
      "href",
      "/reports?period=calendar_week&start=2026-10-26T00%3A00%3A00Z",
    );
  });

  it("never goes past the current month, and clamps a start in the future", () => {
    mount({ period: "calendar_month", start: "2026-12-01T00:00:00Z" });
    expect(mocks.summary).toHaveBeenCalledWith("calendar_month", "2026-11-01T00:00:00.000Z");
    expect(screen.getByText("Ignored a start in the future; showing the current period.")).toBeInTheDocument();
    expect(screen.getByText("Next month").closest("a")).toBeNull();
    expect(screen.getByText("Month starting 2026-11-01 UTC")).toBeInTheDocument();
  });

  it("is not offered for a sliding period", () => {
    mount({ period: "last_30d" });
    expect(screen.queryByText("Next week")).toBeNull();
    expect(screen.queryByText("Previous month")).toBeNull();
  });
});

describe("a full report", () => {
  beforeEach(() => {
    mocks.summary.mockResolvedValue(summary());
  });

  it("prints the resolved range in UTC and says it runs to now", async () => {
    mount();
    expect(await screen.findByText("Range: 2026-11-04 09:00 UTC → 2026-11-11 09:00 UTC (to now)")).toBeInTheDocument();
  });

  it("drops the to-now mark for a finished period", async () => {
    mocks.summary.mockResolvedValue(summary({ end: "2026-11-09T00:00:00Z" }));
    mount({ period: "calendar_week", start: "2026-11-02T00:00:00Z" });
    expect(await screen.findByText("Range: 2026-11-04 09:00 UTC → 2026-11-09 00:00 UTC")).toBeInTheDocument();
  });

  it("has the four blocks and no KPI row", async () => {
    mount();
    await screen.findByRole("heading", { level: 2, name: "Alerts opened" });
    for (const name of ["Anomalies opened", "Time to act on anomalies", "Noise by detector", "Related views"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(document.querySelector(".stat, .kpi, .stat-grid")).toBeNull();
  });

  it("drills a non-zero alert cell to the list with the family, severity and range", async () => {
    mount();
    const table = await screen.findByRole("table", { name: /Alerts opened in the period/ });
    const row = within(table).getByRole("row", { name: /Log sources/ });
    const link = within(row).getByRole("link", { name: "12 MEDIUM in Log sources: open the list" });
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/alerts");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      family: "log_source",
      severity: "MEDIUM",
      since: "2026-11-04T09:00:00Z",
      until: "2026-11-11T09:00:00Z",
    });
    expect(within(row).getAllByRole("link")).toHaveLength(2);
  });

  it("draws an empty cell as a muted 0 that is not a link, and sums each row", async () => {
    mount();
    const table = await screen.findByRole("table", { name: /Alerts opened in the period/ });
    const row = within(table).getByRole("row", { name: /Log sources/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual(["0", "2", "12", "0", "0", "14"]);
    expect(within(cells[0]).queryByRole("link")).toBeNull();
  });

  it("labels an identity alert row and drills to that family", async () => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        alerts: {
          ...base.alerts,
          rows: [{ family: "identity", severity: "HIGH", count: 4 }],
        },
      }),
    );
    mount();
    const table = await screen.findByRole("table", { name: /Alerts opened in the period/ });
    const link = within(table).getByRole("link", { name: "4 HIGH in Identity: open the list" });
    expect(new URL(link.getAttribute("href")!, "http://x").searchParams.get("family")).toBe("identity");
  });

  it("lists a source type no family claims without a drill link", async () => {
    mount();
    const table = await screen.findByRole("table", { name: /Alerts opened in the period/ });
    const row = within(table).getByRole("row", { name: /Other source types/ });
    expect(within(row).queryByRole("link")).toBeNull();
    expect(within(row).getAllByRole("cell").at(-1)).toHaveTextContent("3");
  });

  it("drills an anomaly cell to the list by subject kind", async () => {
    mount();
    const table = await screen.findByRole("table", { name: /Anomalies opened in the period/ });
    const waf = within(table).getByRole("link", { name: "9 MEDIUM in WAF: open the list" });
    const url = new URL(waf.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/anomalies");
    expect(url.searchParams.get("subject_kind")).toBe("WAF_ENDPOINT");
    expect(url.searchParams.get("severity")).toBe("MEDIUM");
    expect(url.searchParams.get("since")).toBe("2026-11-04T09:00:00Z");
    const ips = within(table).getByRole("link", { name: "4 HIGH in IPS: open the list" });
    expect(new URL(ips.getAttribute("href")!, "http://x").searchParams.get("subject_kind")).toBe("IPS");
  });

  it("shows the median as hours and minutes with its n", async () => {
    mount();
    const [alertClocks] = await screen.findAllByRole("table", { name: "Median time to act, in hours and minutes" });
    const mtta = within(alertClocks).getByRole("row", { name: /Time to acknowledge/ });
    expect(within(mtta).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "2 h 14 min",
      "18",
      "Measured",
    ]);
  });

  it("never draws a zero for a median that is not measured: an em dash, the n and the wording", async () => {
    mount();
    const [alertClocks] = await screen.findAllByRole("table", { name: "Median time to act, in hours and minutes" });
    const mttr = within(alertClocks).getByRole("row", { name: /Time to resolve/ });
    const cells = within(mttr).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["—", "3", "Insufficient data: a median needs at least 5 closed items."]);
    expect(alertClocks.textContent).not.toMatch(/\b0 (s|min|h)\b/);
  });

  it("shows the noise ratio as a percentage and says so for an insufficient row", async () => {
    mount();
    const table = await screen.findByRole("table", { name: /Anomalies judged by an analyst/ });
    const spike = within(table).getByRole("row", { name: /Volume spike/ });
    expect(within(spike).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Log sources",
      "Volume spike",
      "16",
      "4",
      "25.0\u00a0%",
      "Measured",
    ]);
    const waf = within(table).getByRole("row", { name: /Request-rate spike/ });
    const cells = within(waf).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.at(-2)).toBe("—");
    expect(cells.at(-1)).toBe("Insufficient data");
  });

  it("links to Noisy sources and to Coverage, and fetches nothing more", async () => {
    mount();
    await screen.findByRole("heading", { level: 2, name: "Related views" });
    expect(screen.getByRole("link", { name: "Noisy sources" })).toHaveAttribute("href", "/behavior/noise");
    expect(screen.getByRole("link", { name: "Coverage" })).toHaveAttribute("href", "/coverage");
    expect(mocks.summary).toHaveBeenCalledTimes(1);
  });
});

describe("the CSV exports", () => {
  it("export the period as the list's since and until, and say how many rows at most", async () => {
    mocks.summary.mockResolvedValue(summary());
    mount();
    await screen.findByRole("heading", { level: 2, name: "Alerts opened" });
    const alerts = within(section("Alerts opened")).getByRole("link", { name: "Export alerts CSV" });
    const url = new URL(alerts.getAttribute("href")!);
    expect(url.pathname.endsWith("/alerts.csv")).toBe(true);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      since: "2026-11-04T09:00:00Z",
      until: "2026-11-11T09:00:00Z",
    });
    expect(alerts).toHaveAttribute("download");
    expect(within(section("Alerts opened")).getByText("at most 10,000 rows")).toBeInTheDocument();
    const anomalies = within(section("Anomalies opened")).getByRole("link", { name: "Export anomalies CSV" });
    expect(new URL(anomalies.getAttribute("href")!).pathname.endsWith("/anomalies.csv")).toBe(true);
  });
});

describe("a partial family", () => {
  beforeEach(() => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        anomalies: { ...base.anomalies, state: "PARTIAL", coverage: partialWaf() },
        anomaly_clocks: { ...base.anomaly_clocks, state: "PARTIAL", coverage: partialWaf() },
      }),
    );
  });

  it("shows its counts as lower bounds and keeps the measured families exact", async () => {
    mount();
    const table = await screen.findByRole("table", { name: /Anomalies opened in the period/ });
    const waf = within(table).getByRole("row", { name: /^WAF/ });
    expect(within(waf).getByRole("link", { name: "9 MEDIUM in WAF: open the list" })).toHaveTextContent("≥ 9");
    expect(within(waf).getAllByRole("cell").at(-1)).toHaveTextContent("≥ 9");
    // An unmeasured cell of a partial family is a dash, not a 0.
    expect(within(waf).getAllByRole("cell")[0]).toHaveTextContent("—");
    const logs = within(table).getByRole("row", { name: /^Log sources/ });
    expect(within(logs).getByRole("link", { name: "5 HIGH in Log sources: open the list" })).toHaveTextContent(/^5$/);
  });

  it("names the retention and the instant the counts start from", async () => {
    mount();
    await screen.findByRole("table", { name: /Anomalies opened in the period/ });
    const notes = screen.getAllByText(
      "WAF: anomaly rows are kept for 180 days, so this period is counted only from 2026-11-06 00:00 UTC. Counts marked ≥ are lower bounds.",
    );
    expect(notes).toHaveLength(2);
  });

  it("marks the block as Partial", async () => {
    mount();
    await screen.findByRole("table", { name: /Anomalies opened in the period/ });
    expect(within(section("Anomalies opened")).getByText("Partial")).toBeInTheDocument();
  });

  it("words a family with nothing inside its retention without a figure", async () => {
    const base = summary();
    const cov = FULL_COVERAGE.map((c) => (c.family === "ips" ? { ...c, state: "INSUFFICIENT_DATA" as const } : c));
    mocks.summary.mockResolvedValue(summary({ anomalies: { ...base.anomalies, state: "PARTIAL", coverage: cov } }));
    mount();
    const table = await screen.findByRole("table", { name: /Anomalies opened in the period/ });
    const ips = within(table).getByRole("row", { name: /^IPS/ });
    expect(within(ips).getByText("Insufficient data")).toBeInTheDocument();
    expect(within(ips).queryByRole("link")).toBeNull();
  });

  it("shows a measured partial median beside its basis", async () => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        anomaly_clocks: {
          ...base.anomaly_clocks,
          state: "PARTIAL",
          mtta: clock(1500, 22, { state: "PARTIAL" }),
          coverage: partialWaf(),
        },
      }),
    );
    mount();
    const clocks = await screen.findAllByRole("table", { name: "Median time to act, in hours and minutes" });
    const row = within(clocks[1]).getByRole("row", { name: /first owner or status change/ });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "25 min",
      "22",
      "Partial: older rows are no longer kept.",
    ]);
  });
});

describe("a forbidden block", () => {
  beforeEach(() => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        alerts: { state: "FORBIDDEN", permission: "read:alerts", reason_code: null, rows: null, mtta: null, mttr: null },
        anomalies: base.anomalies,
      }),
    );
  });

  it("names the permission, offers no export, and leaves the other blocks standing", async () => {
    mount();
    const alerts = (await screen.findByRole("heading", { level: 2, name: "Alerts opened" })).closest("section")!;
    expect(alerts).toHaveTextContent("You need the read:alerts permission to see this block.");
    expect(within(alerts).queryByRole("table")).toBeNull();
    expect(within(alerts).queryByRole("link", { name: "Export alerts CSV" })).toBeNull();
    expect(within(alerts).getByText("Forbidden")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Anomalies opened in the period/ })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Anomalies judged by an analyst/ })).toBeInTheDocument();
    expect(screen.queryByText("No alerts or anomalies were opened in this period.")).toBeNull();
  });

  it("falls back to the permission the block needs when the API names none", async () => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        noise_by_detector: { state: "FORBIDDEN", permission: null, reason_code: null, rows: null, coverage: null },
        alerts: base.alerts,
      }),
    );
    mount();
    expect((await screen.findByRole("heading", { level: 2, name: "Noise by detector" })).closest("section")).toHaveTextContent(
      "read:anomalies",
    );
  });
});

describe("a block whose own read failed", () => {
  it("says it could not be read, draws no figure, and the other blocks stay", async () => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        anomaly_clocks: {
          state: "INSUFFICIENT_DATA",
          permission: null,
          reason_code: "reports.reason.block_failed",
          mtta: null,
          mttr: null,
          coverage: null,
        },
        alerts: base.alerts,
      }),
    );
    mount();
    const clocks = (await screen.findByRole("heading", { level: 2, name: "Time to act on anomalies" })).closest("section")!;
    expect(clocks).toHaveTextContent("This block could not be read");
    expect(within(clocks).queryByRole("table")).toBeNull();
    expect(screen.getByRole("table", { name: /Alerts opened in the period/ })).toBeInTheDocument();
  });

  it("names an unknown reason code instead of hiding it", async () => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        noise_by_detector: { ...base.noise_by_detector, state: "INSUFFICIENT_DATA", reason_code: "reports.reason.new", rows: null },
      }),
    );
    mount();
    expect(await screen.findByText(/reports\.reason\.new/)).toBeInTheDocument();
  });
});

describe("an empty period", () => {
  beforeEach(() => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        alerts: { ...base.alerts, rows: [], mtta: clock(null, 0), mttr: clock(null, 0) },
        anomalies: { ...base.anomalies, rows: [] },
        anomaly_clocks: { ...base.anomaly_clocks, mtta: clock(null, 0), mttr: clock(null, 0) },
        noise_by_detector: { ...base.noise_by_detector, rows: [] },
      }),
    );
  });

  it("says what is empty and the range, and still offers both exports", async () => {
    mount();
    expect(await screen.findByText("No alerts or anomalies were opened in this period.")).toBeInTheDocument();
    expect(screen.getByText("Checked 2026-11-04 09:00 UTC → 2026-11-11 09:00 UTC (to now).")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export alerts CSV" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export anomalies CSV" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("is not claimed when one block could not be read", async () => {
    const base = summary();
    mocks.summary.mockResolvedValue(
      summary({
        alerts: { state: "FORBIDDEN", permission: "read:alerts", reason_code: null, rows: null, mtta: null, mttr: null },
        anomalies: { ...base.anomalies, rows: [] },
      }),
    );
    mount();
    expect(await screen.findByText(/You need the/)).toBeInTheDocument();
    expect(screen.queryByText("No alerts or anomalies were opened in this period.")).toBeNull();
  });
});

describe("errors", () => {
  it("says what failed and retries", async () => {
    mocks.summary.mockRejectedValueOnce(new ApiError(500, "boom")).mockResolvedValueOnce(summary());
    mount();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The report could not be loaded.");
    expect(alert).toHaveTextContent("The backend failed while building the report.");
    expect(alert).not.toHaveTextContent("boom");
    expect(screen.getByRole("link", { name: "Last 7 days" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { level: 2, name: "Alerts opened" });
    expect(mocks.summary).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("words a coded refusal with the server sentence in English", async () => {
    mocks.summary.mockRejectedValue(
      new ApiError(422, "start must not be in the future", "start must not be in the future", {
        detail: "start must not be in the future",
        detail_code: "reports.start_in_future",
        detail_params: {},
      }),
    );
    mount({ period: "calendar_week" });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The backend refused the period: start must not be in the future Choose another period.",
    );
  });

  it("names a network failure", async () => {
    mocks.summary.mockRejectedValue(new TypeError("fetch failed"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("The backend did not answer.");
  });

  it("asks to sign in again for a 401", async () => {
    mocks.summary.mockRejectedValue(new ApiError(401, "x"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired.");
  });

  it("keeps the chips and the links while it waits for a new period", async () => {
    mocks.summary.mockRejectedValue(new ApiError(500, "x"));
    mount();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Noisy sources" })).toBeInTheDocument();
  });
});

