import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads, type SocOverview } from "@/lib/api";
import { behavior, fleet, summary } from "@/lib/home/fixtures";

import { failed, forbidden, loading, ok } from "./testkit";
import { LogSourceHealth } from "./LogSourceHealth";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const BEH = homeReads.sourceBehaviors().key;
const SUM = homeReads.anomalySummary().key;
const FLEET = homeReads.fleetSummary("24h").key;
const OV = homeReads.overview().key;

const overview = { log_sources: { total_log_sources: 11, in_maintenance: 2 } } as SocOverview;
const sources = [
  behavior({ log_source_id: "a", state: "NORMAL" }),
  behavior({ log_source_id: "b", state: "OPEN" }),
  behavior({ log_source_id: "c", state: "INSUFFICIENT_DATA" }),
];

function setup(over: Record<string, unknown> = {}) {
  res.byKey = {
    [BEH]: ok(sources),
    [SUM]: ok(summary({ silent_sources: 1, spikes: 4, monitored_sources: 3 })),
    [FLEET]: ok(fleet([], { new_sources: 3, conflict_sources: 0, unclassified_sources: 5 })),
    [OV]: ok(overview),
    ...over,
  };
}

beforeEach(() => {
  res.byKey = {};
});

const row = (name: string) => screen.getByText(name).closest("tr")!;

describe("LogSourceHealth", () => {
  it("shows the distribution and 'n of m judged normal', not a healthy percentage", () => {
    setup();
    const { container } = render(<LogSourceHealth />);
    expect(container.querySelector("svg.health-bar")).not.toBeNull();
    expect(screen.getByText("1 of 2 judged normal")).toBeInTheDocument();
    expect(screen.getByText(/1 not judged/)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("lists the real categories with their counts and never Degraded", () => {
    setup();
    render(<LogSourceHealth />);
    expect(within(row("Silent")).getByText("1")).toBeInTheDocument();
    expect(within(row("Exceeding expected volume")).getByText("4")).toBeInTheDocument();
    expect(within(row("Newly discovered")).getByText("3")).toBeInTheDocument();
    expect(within(row("In maintenance")).getByText("2")).toBeInTheDocument();
    expect(screen.queryByText(/degraded/i)).toBeNull();
  });

  it("keeps a measured zero as 0", () => {
    setup();
    render(<LogSourceHealth />);
    expect(within(row("Conflicting class")).getByText("0")).toBeInTheDocument();
  });

  it("links a category to the page that owns it, and not 'In maintenance'", () => {
    setup();
    render(<LogSourceHealth />);
    expect(screen.getByRole("link", { name: "Newly discovered" })).toHaveAttribute("href", "/fleet/sources?review_status=NEW");
    expect(screen.getByRole("link", { name: "Silent" })).toHaveAttribute(
      "href",
      "/anomalies?anomaly_type=NO_EVENTS&active_only=true",
    );
    expect(screen.queryByRole("link", { name: "In maintenance" })).toBeNull();
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/behavior/sources");
  });

  it("leaves a category whose read failed at a dash with the reason, not at zero", () => {
    setup({ [FLEET]: failed() });
    render(<LogSourceHealth />);
    const r = row("Newly discovered");
    expect(within(r).getByText("—")).toBeInTheDocument();
    expect(within(r).getByText(/Fleet summary unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Newly discovered" })).toBeNull();
    expect(within(row("Silent")).getByText("1")).toBeInTheDocument();
  });

  it("says the judged figure is unavailable when the summary cannot be read", () => {
    setup({ [SUM]: forbidden });
    render(<LogSourceHealth />);
    expect(screen.getByText(/judged normal is unavailable/)).toBeInTheDocument();
    expect(within(row("Silent")).getByText("—")).toBeInTheDocument();
  });

  it("states INSUFFICIENT_DATA when no source has a verdict yet", () => {
    setup({ [BEH]: ok([behavior({ state: "INSUFFICIENT_DATA" })]) });
    render(<LogSourceHealth />);
    expect(screen.getByText("Insufficient data", { selector: ".kind" })).toBeInTheDocument();
    expect(screen.queryByText(/judged normal/)).toBeNull();
  });

  it("names what is empty and why when no source is monitored", () => {
    setup({ [BEH]: ok([]) });
    render(<LogSourceHealth />);
    expect(screen.getByText(/No monitored log sources\. Enable monitoring/)).toBeInTheDocument();
  });

  it("shows the inventory failure and no zero health rows when no source exists", () => {
    setup({
      [BEH]: ok([]),
      [OV]: ok({
        log_sources: { total_log_sources: 0, in_maintenance: 0, inventory_state: "FAILED" },
      } as SocOverview),
    });
    render(<LogSourceHealth />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The source states could not be read.");
    expect(alert).toHaveTextContent("The inventory sync failed, so these are not counts of zero.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/behavior/sources");
  });

  it("says the inventory was not collected and draws no zero health rows", () => {
    setup({
      [BEH]: ok([]),
      [OV]: ok({
        log_sources: { total_log_sources: 0, in_maintenance: 0, inventory_state: "NOT_COLLECTED" },
      } as SocOverview),
    });
    render(<LogSourceHealth />);
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(screen.getByText(/there are no health counts/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/behavior/sources");
  });

  it("keeps a clean empty inventory as the empty state, with measured zero rows", () => {
    setup({
      [BEH]: ok([]),
      [SUM]: ok(summary({ silent_sources: 0, spikes: 0, monitored_sources: 0 })),
      [FLEET]: ok(fleet([], { new_sources: 0, conflict_sources: 0, unclassified_sources: 0 })),
      [OV]: ok({
        log_sources: { total_log_sources: 0, in_maintenance: 0, inventory_state: "COMPLETE" },
      } as SocOverview),
    });
    render(<LogSourceHealth />);
    expect(screen.getByText(/No monitored log sources\. Enable monitoring/)).toBeInTheDocument();
    expect(within(row("Silent")).getByText("0")).toBeInTheDocument();
    expect(within(row("In maintenance")).getByText("0")).toBeInTheDocument();
  });

  it("keeps measured sources when the latest inventory sync failed", () => {
    setup({
      [OV]: ok({
        log_sources: { total_log_sources: 11, in_maintenance: 2, inventory_state: "FAILED" },
      } as SocOverview),
    });
    render(<LogSourceHealth />);
    expect(screen.getByText("1 of 2 judged normal")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("is loading text in a polite live region", () => {
    setup({ [BEH]: loading });
    render(<LogSourceHealth />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading the source states/);
  });

  it("names the permission when the states are refused", () => {
    setup({ [BEH]: forbidden });
    render(<LogSourceHealth />);
    expect(screen.getByText(/permission read:anomalies/)).toBeInTheDocument();
  });

  it("says what failed, and not the exception", () => {
    setup({ [BEH]: failed(500) });
    render(<LogSourceHealth />);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be read/);
  });

  it("keeps the old data and says so when a refresh failed", () => {
    setup({ [BEH]: ok(sources, { refreshFailed: { httpStatus: 500 } }) });
    render(<LogSourceHealth />);
    expect(screen.getByText("1 of 2 judged normal")).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed/)).toBeInTheDocument();
  });
});
