import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { anomaly, page, summary } from "@/lib/home/fixtures";

import { failed, forbidden, loading, ok } from "./testkit";
import { RecentAnomalies } from "./RecentAnomalies";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const LIST = homeReads.activeAnomalies(5).key;
const SUM = homeReads.anomalySummary().key;

function setup(list: unknown, sum: unknown = ok(summary({ insufficient_data_sources: 3 }))) {
  res.byKey = { [LIST]: list, [SUM]: sum };
}

beforeEach(() => {
  res.byKey = {};
});

describe("RecentAnomalies", () => {
  it("badges the total as 'n active', never 'new'", () => {
    setup(ok(page([anomaly()], 18)));
    render(<RecentAnomalies />);
    expect(screen.getByText("18 active")).toBeInTheDocument();
    expect(screen.queryByText(/new/i)).toBeNull();
    expect(screen.getByText("Showing the latest 1 of 18.")).toBeInTheDocument();
  });

  it("shows the time in UTC with its zone, a datetime attribute and its age", () => {
    setup(ok(page([anomaly({ detected_at: "2026-10-14T10:30:00Z" })])));
    const { container } = render(<RecentAnomalies />);
    const time = container.querySelector("td time");
    expect(time).toHaveAttribute("datetime", "2026-10-14T10:30:00Z");
    expect(time).toHaveTextContent("10-14 10:30 UTC");
  });

  it("links the row to the anomaly and the source to its page", () => {
    setup(ok(page([anomaly({ id: "z9", log_source_id: "s7", log_source_name: "FW 7" })])));
    render(<RecentAnomalies />);
    expect(screen.getByRole("link", { name: "Investigate FW 7" })).toHaveAttribute("href", "/anomalies/z9");
    expect(screen.getByRole("link", { name: "FW 7" })).toHaveAttribute("href", "/behavior/sources/s7");
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/anomalies");
  });

  it("writes a null deviation as an em dash, never 0", () => {
    setup(ok(page([anomaly({ deviation_ratio: null })])));
    render(<RecentAnomalies />);
    const row = screen.getByRole("link", { name: /Investigate/ }).closest("tr")!;
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).queryByText(/^0/)).toBeNull();
  });

  it("says in words what the severity and state are", () => {
    setup(ok(page([anomaly({ severity: "CRITICAL", state: "RECOVERING" })])));
    render(<RecentAnomalies />);
    expect(screen.getAllByText("CRITICAL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Recovering").length).toBeGreaterThan(0);
  });

  it("names how many sources are not judged when there is nothing active", () => {
    setup(ok(page([])));
    render(<RecentAnomalies />);
    expect(screen.getByText(/No active anomalies\. Sources with no baseline are not being judged: 3 sources/)).toBeInTheDocument();
    expect(screen.getByText("0 active")).toBeInTheDocument();
  });

  it("does not invent the not-judged count when the summary failed", () => {
    setup(ok(page([])), failed());
    render(<RecentAnomalies />);
    expect(screen.getByText(/could not be read/)).toBeInTheDocument();
  });

  it("is loading text in a polite live region", () => {
    setup(loading);
    render(<RecentAnomalies />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading the active anomalies/);
  });

  it("names the permission when refused", () => {
    setup(forbidden);
    render(<RecentAnomalies />);
    expect(screen.getByText(/permission read:anomalies/)).toBeInTheDocument();
  });

  it("says what failed, and not the exception", () => {
    setup(failed(null));
    render(<RecentAnomalies />);
    expect(screen.getByRole("alert")).toHaveTextContent(/The backend did not answer/);
  });

  it("keeps the old rows and says so when a refresh failed", () => {
    setup(ok(page([anomaly({ log_source_name: "Kept FW" })]), { refreshFailed: { httpStatus: 502 } }));
    render(<RecentAnomalies />);
    expect(screen.getByRole("link", { name: "Kept FW" })).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed \(HTTP 502\)/)).toBeInTheDocument();
  });
});
