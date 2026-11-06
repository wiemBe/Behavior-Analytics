import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NoiseError from "./error";
import Loading from "./loading";
import NoisePageView from "./page";
import { ApiError, api, type NoiseSourceRow, type NoiseSourcesPage } from "@/lib/api";
import { NAV } from "@/lib/nav";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";

function row(over: Partial<NoiseSourceRow> = {}): NoiseSourceRow {
  return {
    log_source_id: A,
    name: "fw-edge-01",
    instance_id: "i-1",
    instance_name: "qradar-prod",
    criticality: "HIGH",
    source_class: "FIREWALL",
    monitoring_enabled: true,
    judgement: "JUDGED",
    judged: true,
    reason: null,
    incidents_7d: 4,
    incidents_28d: 11,
    opened_7d: 3,
    opened_28d: 8,
    alerts_7d: 2,
    alerts_28d: 5,
    noise_closed: 3,
    noise_closed_total: 6,
    noise_share: 0.5,
    worst_ratio: 4.2,
    worst_z: 6.1,
    top_detector: "VOLUME_SPIKE",
    evaluated_through: "2026-11-06T11:55:00Z",
    ...over,
  };
}

function page(items: NoiseSourceRow[], over: Partial<NoiseSourcesPage> = {}): NoiseSourcesPage {
  return {
    window_days: 28,
    order: "incidents",
    limit: 50,
    offset: 0,
    total: items.length,
    zero_fired: 38,
    zero_fired_judged: 35,
    zero_fired_insufficient_data: 3,
    last_collection: "2026-11-06T11:55:00Z",
    coverage: { state: "COMPLETE", window_days: 28, covered_days: 28, retention_days: 365, reason: null },
    empty_reason: null,
    items,
    ...over,
  };
}

const MIXED = [
  row(),
  row({
    log_source_id: B,
    name: "dns-core",
    judgement: "INSUFFICIENT_DATA",
    judged: false,
    reason: {
      code: "noise.reason.no_reliable_baseline",
      params: {},
      text: "Not judged: no reliable baseline for this weekday and hour.",
    },
    incidents_7d: 0,
    incidents_28d: 2,
    alerts_7d: 0,
    alerts_28d: 0,
    noise_closed: 2,
    noise_closed_total: 3,
    noise_share: null,
  }),
  row({
    log_source_id: C,
    name: "retired-box",
    monitoring_enabled: false,
    judgement: "NOT_COLLECTED",
    judged: false,
    reason: {
      code: "noise.reason.not_monitored",
      params: {},
      text: "This source is not monitored, so no noise counts are collected.",
    },
    incidents_7d: null,
    incidents_28d: null,
    opened_7d: null,
    opened_28d: null,
    alerts_7d: null,
    alerts_28d: null,
    noise_closed: null,
    noise_closed_total: null,
    noise_share: null,
    worst_ratio: null,
    worst_z: null,
    top_detector: null,
  }),
];

function setup(result: NoiseSourcesPage | Error) {
  return vi
    .spyOn(api, "noiseSources")
    .mockImplementation(async () => (result instanceof Error ? Promise.reject(result) : result));
}

async function renderPage(params: Record<string, string | undefined> = {}) {
  return render(await NoisePageView({ searchParams: Promise.resolve(params) }));
}

function rowOf(name: string): HTMLElement {
  const table = screen.getByRole("table");
  const r = within(table)
    .getAllByRole("row")
    .find((x) => x.textContent?.includes(name));
  if (!r) throw new Error(`no row for ${name}`);
  return r;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Noisy sources: the ranked table", () => {
  it("has one h1 and asks for the 28-day, incidents-ordered first page", async () => {
    const call = setup(page(MIXED));
    const { container } = await renderPage();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Noisy sources");
    expect(call).toHaveBeenCalledWith({ window: 28, order: "incidents", limit: 50, offset: 0 });
  });

  it("shows counts, a share with its n / m, the worst deviation and the judged mark", async () => {
    setup(page(MIXED));
    await renderPage();
    const r = rowOf("fw-edge-01");
    expect(r).toHaveTextContent("11");
    expect(r).toHaveTextContent("50.0");
    expect(r).toHaveTextContent("3 noise / 6 closed");
    expect(r).toHaveTextContent("4.2×");
    expect(r).toHaveTextContent("z 6.1");
    expect(r).toHaveTextContent("Judged");
    expect(within(r).getByRole("link", { name: "fw-edge-01" })).toHaveAttribute(
      "href",
      `/behavior/sources/${A}`,
    );
    expect(within(r).getByRole("link", { name: /Alerts of fw-edge-01/ })).toHaveAttribute(
      "href",
      `/alerts?log_source_id=${A}`,
    );
  });

  it("gives units in the headers and right-aligns the figures", async () => {
    setup(page(MIXED));
    await renderPage();
    expect(screen.getByRole("columnheader", { name: /Incidents\s*7 d/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Alerts\s*28 d/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Noise\s*28 d/ })).toBeInTheDocument();
    expect(rowOf("fw-edge-01").querySelectorAll("td.num").length).toBeGreaterThanOrEqual(5);
  });

  it("shows a short denominator as n noise / m closed, with no percentage", async () => {
    setup(page(MIXED));
    await renderPage();
    const r = rowOf("dns-core");
    expect(r).toHaveTextContent("2 noise / 3 closed");
    expect(r).toHaveTextContent("share needs 5+ closed");
    expect(r.textContent).not.toMatch(/%| %/);
  });

  it("marks a not-judged source and never draws its count as calm", async () => {
    setup(page(MIXED));
    await renderPage();
    const r = rowOf("dns-core");
    expect(r).toHaveTextContent("Not judged");
    expect(r).toHaveTextContent("No reliable baseline for this weekday and hour");
    expect(rowOf("fw-edge-01")).not.toHaveTextContent("Not judged");
  });

  it("prints 'Not judged' once in the cell and strips the reason's own prefix", async () => {
    setup(page(MIXED));
    await renderPage();
    const cell = rowOf("dns-core").querySelector("td.noise-judged") as HTMLElement;
    expect(cell.textContent?.match(/Not judged/g)).toHaveLength(1);
    expect(cell).toHaveTextContent("No reliable baseline for this weekday and hour.");
  });

  it("shows a reason without the prefix unchanged", async () => {
    setup(
      page([
        row({
          judged: false,
          judgement: "INSUFFICIENT_DATA",
          reason: { code: "x", params: {}, text: "Baseline is still warming up." },
        }),
      ]),
    );
    await renderPage();
    const cell = rowOf("fw-edge-01").querySelector("td.noise-judged") as HTMLElement;
    expect(cell).toHaveTextContent("Baseline is still warming up.");
    expect(cell.textContent?.match(/Not judged/g)).toHaveLength(1);
  });

  it("shows the hint when a not-judged row has no reason", async () => {
    setup(page([row({ judged: false, judgement: "INSUFFICIENT_DATA", reason: null })]));
    await renderPage();
    const cell = rowOf("fw-edge-01").querySelector("td.noise-judged") as HTMLElement;
    expect(cell).toHaveTextContent("A low count here is not evidence of calm.");
  });

  it("says 'not monitored' for an unmonitored source and never renders a 0", async () => {
    setup(page(MIXED));
    await renderPage();
    const r = rowOf("retired-box");
    expect(r.querySelector("td[colspan]")).toHaveTextContent("not monitored");
    const cells = within(r).getAllByRole("cell");
    expect(cells.map((c) => c.textContent).join("|")).not.toMatch(/(^|\|)0(\||$)/);
    expect(r).not.toHaveTextContent(/\b0\b/);
  });

  it("shows a null count as not measured, not 0", async () => {
    setup(page([row({ alerts_28d: null, worst_ratio: null, worst_z: null, top_detector: null })]));
    await renderPage();
    const r = rowOf("fw-edge-01");
    expect(r.textContent).toContain("not measured");
  });

  it("notes that noise and worst deviation always cover 28 days, in either window", async () => {
    setup(page(MIXED, { window_days: 7 }));
    await renderPage({ window: "7" });
    const note = screen.getByText(/always cover the last 28 days/);
    expect(note).toHaveTextContent("whichever window is selected");
    expect(screen.getByRole("columnheader", { name: /Noise\s*28 d/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Worst deviation\s*28 d/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Alerts\s*7 d/ })).toBeInTheDocument();
  });

  it("does not link a row to the 17b detail route, which does not exist yet", async () => {
    setup(page(MIXED));
    await renderPage();
    const hrefs = Array.from(document.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs.some((h) => h?.startsWith("/behavior/noise/"))).toBe(false);
  });

  it("states the last evaluation in UTC and avoids 'live'", async () => {
    setup(page(MIXED));
    await renderPage();
    const text = document.body.textContent ?? "";
    expect(text).toContain("2026-11-06 11:55:00 UTC");
    expect(text).not.toMatch(/real-time|\blive\b/i);
  });
});

describe("Noisy sources: the URL", () => {
  it("passes window, order and offset to the API", async () => {
    const call = setup(page(MIXED, { window_days: 7, order: "noise_share", offset: 50, total: 120 }));
    await renderPage({ window: "7", order: "noise_share", offset: "50" });
    expect(call).toHaveBeenCalledWith({ window: 7, order: "noise_share", limit: 50, offset: 50 });
  });

  it("builds the window and order links from the query and marks the current ones", async () => {
    setup(page(MIXED, { window_days: 7 }));
    await renderPage({ window: "7", order: "alerts" });
    const sevenDays = screen.getByRole("link", { name: "7 days" });
    expect(sevenDays).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "28 days" })).toHaveAttribute(
      "href",
      "/behavior/noise?order=alerts",
    );
    expect(screen.getByTitle("Order by Noise")).toHaveAttribute(
      "href",
      "/behavior/noise?window=7&order=noise_share",
    );
    const alertsTh = screen.getByRole("columnheader", { name: /Alerts/ });
    expect(alertsTh).toHaveAttribute("aria-sort", "descending");
  });

  it("carries window and order in the paging links", async () => {
    setup(page(MIXED, { total: 120 }));
    await renderPage({ window: "7", order: "alerts" });
    const next = screen.getByRole("link", { name: /Next/i });
    expect(next.getAttribute("href")).toBe("/behavior/noise?window=7&order=alerts&offset=50");
  });

  it("ignores values it does not know", async () => {
    const call = setup(page(MIXED));
    await renderPage({ window: "99", order: "name", offset: "-4" });
    expect(call).toHaveBeenCalledWith({ window: 28, order: "incidents", limit: 50, offset: 0 });
  });
});

describe("Noisy sources: the zero-incident footer", () => {
  it("is collapsed and carries its count", async () => {
    setup(page(MIXED));
    const { container } = await renderPage();
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(details?.querySelector("summary")).toHaveTextContent("No incidents on 38 sources");
    expect(details).toHaveTextContent("35 sources are judged");
    expect(details).toHaveTextContent("3 sources are not judged");
    expect(details).toHaveTextContent("not evidence of calm");
  });

  it("is absent when every monitored source fired, and says so", async () => {
    setup(page(MIXED, { zero_fired: 0, zero_fired_judged: 0, zero_fired_insufficient_data: 0 }));
    const { container } = await renderPage();
    expect(container.querySelector("details")).toBeNull();
    expect(document.body.textContent).toContain("Every monitored source raised at least one anomaly");
  });
});

describe("Noisy sources: honesty and failure states", () => {
  it("PARTIAL says how many days are covered", async () => {
    setup(
      page(MIXED, {
        coverage: {
          state: "PARTIAL",
          window_days: 28,
          covered_days: 7,
          retention_days: 7,
          reason: {
            code: "noise.reason.partial_retention",
            params: { covered: 7, window: 28 },
            text: "Counts cover 7 of 28 days: incident retention is shorter than the requested window.",
          },
        },
      }),
    );
    await renderPage();
    const banner = screen.getByText("Partial coverage.").closest(".banner") as HTMLElement;
    expect(banner).toHaveTextContent("cover 7 of 28 days");
    expect(banner).toHaveTextContent("lower bounds");
    expect(banner).toHaveTextContent("incident retention is shorter");
  });

  it("shows no PARTIAL banner for complete coverage", async () => {
    setup(page(MIXED));
    await renderPage();
    expect(screen.queryByText("Partial coverage.")).toBeNull();
  });

  it("empty states the reason and the last collection watermark", async () => {
    setup(
      page([], {
        total: 0,
        zero_fired: 48,
        zero_fired_judged: 45,
        zero_fired_insufficient_data: 3,
        empty_reason: {
          code: "noise.reason.empty_window",
          params: { window: 28 },
          text: "No source raised an anomaly in the last 28 days of stored data.",
        },
      }),
    );
    await renderPage();
    expect(screen.getByText("No source raised an anomaly")).toBeInTheDocument();
    expect(document.body.textContent).toContain("in the last 28 days of stored data");
    expect(document.body.textContent).toContain("The last evaluation was 2026-11-06 11:55:00 UTC");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("empty without any collection says it may be a missing collection", async () => {
    setup(page([], { total: 0, last_collection: null, zero_fired: 0, zero_fired_judged: 0, zero_fired_insufficient_data: 0 }));
    await renderPage();
    expect(document.body.textContent).toContain("may be a missing collection");
    expect(document.body.textContent).toContain("No evaluation has been recorded yet");
  });

  it("names what failed and that the counts are unknown, not zero", async () => {
    setup(new ApiError(500, "boom"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("could not be loaded");
    expect(alert).toHaveTextContent("unknown, not zero");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("names read:anomalies when forbidden", async () => {
    setup(new ApiError(403, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:anomalies");
  });

  it("says the session expired on 401", async () => {
    setup(new ApiError(401, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("session has expired");
  });

  it("a page past the end offers the first page", async () => {
    setup(page([], { total: 12, offset: 100 }));
    await renderPage({ offset: "100" });
    expect(screen.getByText("This page is past the end of the list")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the first page" })).toHaveAttribute(
      "href",
      "/behavior/noise",
    );
  });

  it("has a designed loading and error boundary", () => {
    const { unmount } = render(<Loading />);
    expect(document.body.textContent).toMatch(/noisy sources/i);
    unmount();
    render(<NoiseError error={new Error("x")} reset={() => {}} />);
    expect(document.body.textContent).toMatch(/Noisy sources/);
  });
});

describe("Noisy sources: navigation", () => {
  it("has a Behavior entry after Sources", () => {
    const items = NAV.find((g) => g.label === "Behavior")!.items;
    const labels = items.map((i) => i.label);
    expect(items.find((i) => i.href === "/behavior/noise")?.label).toBe("Noisy sources");
    expect(labels.indexOf("Noisy sources")).toBe(labels.indexOf("Sources") + 1);
  });
});
