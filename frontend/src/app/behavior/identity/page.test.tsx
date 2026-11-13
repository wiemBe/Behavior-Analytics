import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OTHER_ID, SOURCE_ID, point, profile, source, summary } from "@/components/identity/fixtures";
import { ApiError, api, identityApi, type IdentitySource } from "@/lib/api";

import IdentityError from "./error";
import Loading from "./loading";
import IdentityOverviewPage from "./page";

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  sources: IdentitySource[] = [source()],
  params: Record<string, string | undefined> = {},
) {
  const list = vi.spyOn(identityApi, "sources").mockResolvedValue(sources);
  const detail = vi
    .spyOn(identityApi, "source")
    .mockResolvedValue({ profile: profile(), series: [point({ event_count: 12 })] });
  const anomalies = vi
    .spyOn(api, "anomalies")
    .mockResolvedValue({ items: [summary()], total: 1, limit: 200, offset: 0 });
  const view = render(await IdentityOverviewPage({ searchParams: Promise.resolve(params) }));
  return { ...view, list, detail, anomalies };
}

describe("Identity overview", () => {
  it("has one h1 and leads with how many sources are collected", async () => {
    const { container } = await renderPage([
      source(),
      source({ profile: profile({ log_source_id: OTHER_ID, log_source_name: "dc-02", enabled: false }) }),
    ]);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Identity");
    const lead = container.querySelector(".identity-lead") as HTMLElement;
    expect(lead).toHaveTextContent("1 of 2 sources are collected");
    expect(lead).toHaveTextContent("1 need attention");
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });

  it("reads only active identity incidents and the window's counts", async () => {
    const { anomalies, detail } = await renderPage();
    expect(anomalies).toHaveBeenCalledWith({ subject_kind: "IDENTITY_SOURCE", active_only: true, limit: 200 });
    const [id, window] = detail.mock.calls[0];
    expect(id).toBe(SOURCE_ID);
    expect(Date.parse(window!.end!) - Date.parse(window!.start!)).toBe(24 * 3_600_000);
  });

  it("shows the open incidents by severity and links them", async () => {
    await renderPage();
    const row = within(screen.getAllByRole("link", { name: "dc-01.example.test" })[0].closest("tr")!);
    expect(row.getByRole("link", { name: "1 open incident" })).toHaveAttribute(
      "href",
      expect.stringContaining("subject_kind=IDENTITY_SOURCE"),
    );
    expect(row.getByText("HIGH")).toBeInTheDocument();
  });

  it("counts right-aligned per catalogue event and marks a sum over missing buckets as at least", async () => {
    await renderPage();
    const table = screen.getByRole("table", { name: /Catalogue event counts/ });
    const head = within(table).getAllByRole("columnheader");
    expect(head.map((h) => h.textContent?.slice(0, 4))).toEqual(
      expect.arrayContaining(["4625", "4740", "1102"]),
    );
    const cell = within(table).getByText(/≥ 12/);
    expect(cell.closest("td")).toHaveClass("num");
    expect(within(table).getByText(/of 288 buckets not measured/)).toBeInTheDocument();
  });

  it("draws a dash, not zero, for an event nothing was measured for", async () => {
    await renderPage();
    const table = screen.getByRole("table", { name: /Catalogue event counts/ });
    const dashes = within(table).getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(7);
    expect(table.textContent).not.toMatch(/(^|\s)0(\s|$)/);
  });

  it("shows an event the profile does not collect as off", async () => {
    await renderPage([source({ profile: profile({ enabled_event_ids: [4625] }) })]);
    expect(screen.getAllByText("off")).toHaveLength(7);
  });

  it("states the honesty state of a source that was never collected", async () => {
    await renderPage([source({ last_bucket: null })]);
    expect(screen.getAllByText("Not collected yet").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not collected").length).toBeGreaterThan(0);
  });

  it("states truncated and partial newest buckets", async () => {
    await renderPage([
      source({ last_bucket: { ...source().last_bucket!, measurement: "TRUNCATED" } }),
      source({
        profile: profile({ log_source_id: OTHER_ID, log_source_name: "dc-02" }),
        last_bucket: { ...source().last_bucket!, admission_truncated: true },
      }),
    ]);
    expect(screen.getByText("Truncated")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
  });

  it("says a profile that is not enabled is not collected", async () => {
    await renderPage([source({ profile: profile({ enabled: false }) })]);
    expect(screen.getAllByText("Not enabled").length).toBeGreaterThan(0);
  });

  it("flags a failing collector first", async () => {
    await renderPage([
      source({ collection: { ...source().collection, consecutive_failures: 4 } }),
    ]);
    expect(screen.getByText("Failing")).toBeInTheDocument();
    expect(screen.getByText(/4 failed runs in a row/)).toBeInTheDocument();
  });

  it("takes the range from the URL and ignores an unknown one", async () => {
    const { detail } = await renderPage([source()], { range: "7d" });
    const [, w] = detail.mock.calls[0];
    expect(Date.parse(w!.end!) - Date.parse(w!.start!)).toBe(7 * 86_400_000);
    expect(screen.getByRole("link", { name: "7 days" })).toHaveAttribute("aria-current", "page");
  });

  it("falls back to 24 hours for a range it does not know", async () => {
    await renderPage([source()], { range: "forever" });
    expect(screen.getByRole("link", { name: "24 hours" })).toHaveAttribute("aria-current", "page");
  });
});

describe("Identity overview: what could not be read", () => {
  it("says the incidents could not be read, with a dash and not zero", async () => {
    vi.spyOn(identityApi, "sources").mockResolvedValue([source()]);
    vi.spyOn(identityApi, "source").mockResolvedValue({ profile: profile(), series: [] });
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(500, "boom"));
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText(/open incidents could not be read/)).toBeInTheDocument();
    expect(screen.queryByText("None")).toBeNull();
  });

  it("says the counts of a source could not be read instead of drawing zeros", async () => {
    vi.spyOn(identityApi, "sources").mockResolvedValue([source()]);
    vi.spyOn(identityApi, "source").mockRejectedValue(new ApiError(500, "boom"));
    vi.spyOn(api, "anomalies").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("The counts of this source could not be read.")).toBeInTheDocument();
  });

  it("reads the counts of the first 25 sources only and says so", async () => {
    const many = Array.from({ length: 27 }, (_, i) =>
      source({ profile: profile({ log_source_id: `s${i}`, log_source_name: `dc-${String(i).padStart(2, "0")}` }) }),
    );
    const { detail } = await renderPage(many);
    expect(detail).toHaveBeenCalledTimes(25);
    expect(screen.getByText(/first 25 of 27 sources/)).toBeInTheDocument();
    expect(screen.getAllByText(/Not read here/)).toHaveLength(2);
  });

  it("caps the incident list and says the per-source counts are at least what is shown", async () => {
    vi.spyOn(identityApi, "sources").mockResolvedValue([source()]);
    vi.spyOn(identityApi, "source").mockResolvedValue({ profile: profile(), series: [] });
    vi.spyOn(api, "anomalies").mockResolvedValue({ items: [summary()], total: 450, limit: 200, offset: 0 });
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText(/Only 1 of 450 open incidents were read/)).toBeInTheDocument();
  });
});

describe("Identity overview: states", () => {
  it("is empty with what is empty and what to do, not a clean bill of health", async () => {
    vi.spyOn(identityApi, "sources").mockResolvedValue([]);
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("status")).toHaveTextContent("No identity source is enabled.");
    expect(screen.getByRole("status")).toHaveTextContent("admin:identity");
    expect(screen.getByRole("status")).toHaveTextContent("not a clean result");
    expect(screen.getByRole("link", { name: "Find the log source" })).toHaveAttribute("href", "/log-sources");
  });

  it("names the permission when the read is forbidden", async () => {
    vi.spyOn(identityApi, "sources").mockRejectedValue(
      new ApiError(403, "forbidden", "missing required permission: read:identity"),
    );
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("You cannot read identity sources");
    expect(screen.getByRole("alert")).toHaveTextContent("read:identity");
  });

  it("says what failed on a server error, without the exception", async () => {
    vi.spyOn(identityApi, "sources").mockRejectedValue(new ApiError(500, "boom ECONNREFUSED 10.0.0.9"));
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("Identity sources could not be loaded");
    expect(screen.getByRole("alert")).not.toHaveTextContent("ECONNREFUSED");
  });

  it("says the session expired on a 401", async () => {
    vi.spyOn(identityApi, "sources").mockRejectedValue(new ApiError(401, "no"));
    render(await IdentityOverviewPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("Your session has expired");
  });

  it("has a loading boundary that keeps the heading and a render error that offers retry", () => {
    const { unmount } = render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Identity");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the identity sources");
    unmount();
    const reset = vi.fn();
    render(<IdentityError error={Object.assign(new Error("secret stack"), { digest: "ref-1" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be displayed");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
