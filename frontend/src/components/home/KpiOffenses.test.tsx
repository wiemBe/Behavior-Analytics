import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { analytics, offenseRow, page } from "@/lib/home/fixtures";

import { KpiOffenses } from "./KpiOffenses";
import { failed, forbidden, ok } from "./KpiTestStates";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => mocks.states[key] ?? { status: "loading" },
}));

const ANALYTICS = homeReads.offenseAnalytics().key;
const PROBE = homeReads.offenseProbe().key;

beforeEach(() => {
  mocks.states = {};
});

const withSnapshot = () => ok(page([offenseRow()], 40));

describe("KpiOffenses", () => {
  it("shows open offenses with the critical split at the product threshold", () => {
    mocks.states[ANALYTICS] = ok(
      analytics({ active: 12, critical: 3, unassigned: 5, exceeding_sla: 2 }, [
        { key: "8", count: 3 },
        { key: "4", count: 8 },
        { key: "unassigned", count: 1 },
      ]),
    );
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(screen.getByRole("heading", { name: "Open offenses, latest snapshot" })).toBeInTheDocument();
    expect(screen.getByText("open offenses")).toBeInTheDocument();
    expect(screen.getByText("3 critical (\u2265 7) \u00b7 8 below \u00b7 1 unknown")).toBeInTheDocument();
    expect(screen.getByText("5 unassigned \u00b7 2 over SLA")).toBeInTheDocument();
    expect(screen.getByTitle(/magnitude of 7 or more/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View open offenses" })).toHaveAttribute(
      "href",
      "/offenses?status=OPEN",
    );
  });

  it("shows a measured zero as 0 when a snapshot exists and none is open", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: 0 }));
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(screen.getByText("0", { selector: ".kpi-value" })).toBeInTheDocument();
  });

  it("says Not collected in words when there is no snapshot, with an em dash", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: 0 }));
    mocks.states[PROBE] = ok(page([], 0));
    render(<KpiOffenses />);
    expect(screen.getByText("—", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("No offense snapshot has been collected yet.")).toBeInTheDocument();
    expect(screen.queryByText(/critical/)).toBeNull();
  });

  it("fails closed when the probe cannot be read and the count is 0", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: 0 }));
    mocks.states[PROBE] = failed(500);
    render(<KpiOffenses />);
    expect(screen.getByText(/Could not confirm that an offense snapshot exists/)).toBeInTheDocument();
    expect(screen.queryByText("0", { selector: ".kpi-value" })).toBeNull();
  });

  it("waits for the probe rather than showing a zero", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: 0 }));
    render(<KpiOffenses />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading offenses…");
  });

  it("is loading while the analytics are in flight", () => {
    render(<KpiOffenses />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("names the permission when refused", () => {
    mocks.states[ANALYTICS] = forbidden;
    render(<KpiOffenses />);
    expect(
      screen.getByText("Your role cannot read offenses (permission read:offenses). Nothing was changed."),
    ).toBeInTheDocument();
  });

  it("says what failed without exception text", () => {
    mocks.states[ANALYTICS] = failed(null);
    render(<KpiOffenses />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The offenses could not be loaded. Nothing was changed.");
  });

  it("keeps the data on a failed refresh", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: 4, critical: 1 }), {
      refreshFailed: { httpStatus: 500 },
    });
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    const note = screen.getByText(/Refresh failed; showing data received at/);
    expect(within(note).getByText("13:55 UTC")).toBeInTheDocument();
    expect(screen.getByText("4", { selector: ".kpi-value" })).toBeInTheDocument();
  });
});
