import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { summary } from "@/lib/home/fixtures";

import { KpiAnomalies } from "./KpiAnomalies";
import { failed, forbidden, ok } from "./KpiTestStates";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown>, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => {
    mocks.keys.push(key);
    return mocks.states[key] ?? { status: "loading" };
  },
}));

const SUMMARY = homeReads.anomalySummary().key;

beforeEach(() => {
  mocks.states = {};
  mocks.keys = [];
});

describe("KpiAnomalies", () => {
  it("shows the count, the types on one line, the unjudged companion and the drill-down", () => {
    mocks.states[SUMMARY] = ok(
      summary({
        open_anomalies: 3,
        spikes: 1,
        drops: 1,
        silent_sources: 1,
        candidates: 2,
        recovering: 3,
        insufficient_data_sources: 4,
      }),
    );
    render(<KpiAnomalies />);
    expect(screen.getByRole("heading", { name: "Active anomalies, now" })).toBeInTheDocument();
    expect(screen.getByText("active anomalies")).toBeInTheDocument();
    expect(
      screen.getByText("1 spikes · 1 drops · 1 silent · 2 candidates · 3 recovering"),
    ).toBeInTheDocument();
    expect(screen.getByText(/4 sources not judged; they are not in these counts/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View active anomalies" })).toHaveAttribute(
      "href",
      "/anomalies?active_only=true",
    );
    expect(screen.getByRole("article")).toHaveAccessibleDescription(/candidate, open or recovering/);
  });

  it("does not read the capped active list and draws no severity list", () => {
    mocks.states[SUMMARY] = ok(summary({ open_anomalies: 345 }));
    render(<KpiAnomalies />);
    expect(mocks.keys.every((k) => k === SUMMARY)).toBe(true);
    expect(screen.queryByText(/severity/i)).toBeNull();
    expect(screen.queryByText("Truncated")).toBeNull();
  });

  it("shows a measured zero as 0 and still names unjudged sources", () => {
    mocks.states[SUMMARY] = ok(summary({ open_anomalies: 0, insufficient_data_sources: 1 }));
    render(<KpiAnomalies />);
    expect(screen.getByText("0", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText(/1 source not judged; it is not in these counts/)).toBeInTheDocument();
  });

  it("is a polite status while loading", () => {
    render(<KpiAnomalies />);
    const s = screen.getByRole("status");
    expect(s).toHaveTextContent("Loading anomalies…");
    expect(s).toHaveAttribute("aria-live", "polite");
  });

  it("names the permission when refused", () => {
    mocks.states[SUMMARY] = forbidden;
    render(<KpiAnomalies />);
    expect(
      screen.getByText("Your role cannot read anomalies (permission read:anomalies). Nothing was changed."),
    ).toBeInTheDocument();
  });

  it("says what failed without exception text", () => {
    mocks.states[SUMMARY] = failed(503);
    render(<KpiAnomalies />);
    expect(screen.getByRole("alert")).toHaveTextContent("The anomalies could not be loaded (HTTP 503)");
  });

  it("keeps the data on a failed refresh", () => {
    mocks.states[SUMMARY] = ok(summary({ open_anomalies: 2 }), { refreshFailed: { httpStatus: 500 } });
    render(<KpiAnomalies />);
    const note = screen.getByText(/Refresh failed; showing data received at/);
    expect(within(note).getByText("13:55 UTC")).toBeInTheDocument();
    expect(screen.getByText("2", { selector: ".kpi-value" })).toBeInTheDocument();
  });

  it("says nothing about when the data arrived while all is well", () => {
    mocks.states[SUMMARY] = ok(summary({ open_anomalies: 2 }));
    render(<KpiAnomalies />);
    expect(screen.queryByText(/Received|13:55/)).toBeNull();
  });
});
