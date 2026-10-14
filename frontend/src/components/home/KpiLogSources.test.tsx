import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads, type SocOverview } from "@/lib/api";
import { behavior, summary } from "@/lib/home/fixtures";

import { KpiLogSources } from "./KpiLogSources";
import { failed, forbidden, ok } from "./KpiTestStates";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => mocks.states[key] ?? { status: "loading" },
}));

const SUMMARY = homeReads.anomalySummary().key;
const SOURCES = homeReads.sourceBehaviors().key;
const OVERVIEW = homeReads.overview().key;

const overview = (total: number) =>
  ({ log_sources: { total_log_sources: total } }) as unknown as SocOverview;

const sources = [
  behavior({ log_source_id: "a", state: "NORMAL" }),
  behavior({ log_source_id: "b", state: "NORMAL" }),
  behavior({ log_source_id: "c", state: "OPEN" }),
  behavior({ log_source_id: "d", state: "INSUFFICIENT_DATA" }),
];

beforeEach(() => {
  mocks.states = {};
});

describe("KpiLogSources", () => {
  it("shows monitored of total, n of m judged normal, the bar and the drill-down", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 4, silent_sources: 1 }));
    mocks.states[SOURCES] = ok(sources);
    mocks.states[OVERVIEW] = ok(overview(120));
    const { container } = render(<KpiLogSources />);
    expect(screen.getByRole("heading", { name: "Log sources, now" })).toBeInTheDocument();
    expect(screen.getByText("of 120 monitored")).toBeInTheDocument();
    expect(screen.getByText(/^2 of 3 judged normal/)).toBeInTheDocument();
    expect(container.querySelectorAll("rect.health-seg")).toHaveLength(3);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View log sources" })).toHaveAttribute(
      "href",
      "/behavior/sources",
    );
  });

  it("names the silent definition and counts silent", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 4, silent_sources: 2 }));
    mocks.states[SOURCES] = ok(sources);
    mocks.states[OVERVIEW] = ok(overview(10));
    render(<KpiLogSources />);
    expect(screen.getByText(/2 silent/)).toBeInTheDocument();
    // The definition is in the tooltip and in the accessible description.
    expect(screen.getByTitle(/NO_EVENTS anomaly/)).toBeInTheDocument();
    expect(screen.getByRole("article")).toHaveAccessibleDescription(/NO_EVENTS anomaly/);
  });

  it("shows an em dash, not 0 judged normal, when nothing is judged", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 1 }));
    mocks.states[SOURCES] = ok([behavior({ state: "INSUFFICIENT_DATA" })]);
    mocks.states[OVERVIEW] = ok(overview(3));
    render(<KpiLogSources />);
    expect(screen.getByText(/\u2014 judged normal \(no source is judged yet\)/)).toBeInTheDocument();
    expect(screen.getByText("No monitored source has a verdict yet.")).toBeInTheDocument();
    expect(screen.queryByText(/0 of 0/)).toBeNull();
  });

  it("keeps the card when the overview is refused, and says why the total is missing", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 4 }));
    mocks.states[SOURCES] = ok(sources);
    mocks.states[OVERVIEW] = forbidden;
    render(<KpiLogSources />);
    expect(screen.getByText("monitored")).toBeInTheDocument();
    expect(screen.getByText(/permission read:overview/)).toBeInTheDocument();
  });

  it("says the total is unavailable when the overview fails", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 4 }));
    mocks.states[SOURCES] = ok(sources);
    mocks.states[OVERVIEW] = failed(500);
    render(<KpiLogSources />);
    expect(screen.getByText("Total log sources unavailable.")).toBeInTheDocument();
  });

  it("is loading until both required reads are in", () => {
    mocks.states[SUMMARY] = ok(summary());
    render(<KpiLogSources />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading log source health…");
  });

  it("names the permission when refused", () => {
    mocks.states[SUMMARY] = forbidden;
    mocks.states[SOURCES] = ok(sources);
    render(<KpiLogSources />);
    expect(screen.getByText(/Your role cannot read log source health \(permission read:anomalies\)/)).toBeInTheDocument();
  });

  it("says what failed, with the status and no exception text", () => {
    mocks.states[SUMMARY] = failed(500);
    mocks.states[SOURCES] = ok(sources);
    render(<KpiLogSources />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded (HTTP 500)");
  });

  it("keeps the data on a failed refresh and says when it was received", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 4 }), {
      refreshFailed: { httpStatus: 500 },
    });
    mocks.states[SOURCES] = ok(sources);
    mocks.states[OVERVIEW] = ok(overview(9));
    render(<KpiLogSources />);
    const note = screen.getByText(/Refresh failed; showing data received at/);
    expect(within(note).getByText("13:55 UTC")).toBeInTheDocument();
    expect(screen.getByText(/^2 of 3 judged normal/)).toBeInTheDocument();
  });
});
