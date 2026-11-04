import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { activeCounts, summary } from "@/lib/home/fixtures";

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
        candidates: 1,
        recovering: 1,
        insufficient_data_sources: 4,
      }),
    );
    render(<KpiAnomalies />);
    expect(screen.getByRole("heading", { name: "Active anomalies, now" })).toBeInTheDocument();
    expect(screen.getByText("active anomalies")).toBeInTheDocument();
    const detector = screen.getByText(/Log sources:/).closest("p")!;
    expect(
      Array.from(detector.querySelectorAll(".kpi-line-part"), (part) => part.textContent),
    ).toEqual(["1\u00a0spike \u00b7", "1\u00a0drop \u00b7", "1\u00a0silent"]);
    const lifecycle = screen.getByText(/Lifecycle/).closest("p")!;
    expect(
      Array.from(lifecycle.querySelectorAll(".kpi-line-part"), (part) => part.textContent),
    ).toEqual(["1\u00a0open \u00b7", "1\u00a0candidate \u00b7", "1\u00a0recovering"]);
    expect(screen.getByText(/4 log sources not judged; they are not in these counts/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View active anomalies" })).toHaveAttribute(
      "href",
      "/anomalies?active_only=true",
    );
    expect(screen.getByRole("article")).toHaveAccessibleDescription(/candidate, open or recovering/);
  });

  it("counts every family: the total, the family parts and the lifecycle parts agree", () => {
    mocks.states[SUMMARY] = ok(
      summary({
        open_anomalies: 3,
        spikes: 2,
        candidates: 1,
        active_by_family: {
          log_source: activeCounts(2, 1, 0),
          waf: activeCounts(0, 0, 2),
          ips: activeCounts(1),
          property_watch: activeCounts(3),
        },
      }),
    );
    render(<KpiAnomalies />);
    expect(screen.getByText("9", { selector: ".kpi-value" })).toBeInTheDocument();
    const parts = (label: RegExp) =>
      Array.from(
        screen.getByText(label).closest("p")!.querySelectorAll(".kpi-line-part"),
        (part) => part.textContent,
      );
    expect(parts(/Log sources:/)).toEqual([
      "2\u00a0spikes \u00b7",
      "0\u00a0drops \u00b7",
      "0\u00a0silent \u00b7",
      "1\u00a0other",
    ]);
    expect(parts(/Also:/)).toEqual([
      "2\u00a0WAF \u00b7",
      "1\u00a0IPS \u00b7",
      "3\u00a0property watches",
    ]);
    expect(parts(/Lifecycle:/)).toEqual([
      "6\u00a0open \u00b7",
      "1\u00a0candidate \u00b7",
      "2\u00a0recovering",
    ]);
    expect(screen.queryByText(/not read/)).toBeNull();
  });

  it.each([
    [0, "0\u00a0spikes", "0\u00a0drops", "0\u00a0WAF", "0\u00a0property watches", "0\u00a0candidates"],
    [1, "1\u00a0spike", "1\u00a0drop", "1\u00a0WAF", "1\u00a0property watch", "1\u00a0candidate"],
    [2, "2\u00a0spikes", "2\u00a0drops", "2\u00a0WAF", "2\u00a0property watches", "2\u00a0candidates"],
  ])("uses the right plural form for %i", (n, spikes, drops, waf, watches, candidates) => {
    mocks.states[SUMMARY] = ok(
      summary({
        monitored_sources: 1,
        spikes: n,
        drops: n,
        insufficient_data_sources: n,
        active_by_family: {
          log_source: activeCounts(n * 3 + n, n, 0),
          waf: activeCounts(n),
          ips: activeCounts(n),
          property_watch: activeCounts(n),
        },
      }),
    );
    render(<KpiAnomalies />);
    const text = document.body.textContent ?? "";
    for (const part of [spikes, drops, waf, watches, candidates]) expect(text).toContain(part);
    expect(text).toContain(`${n}\u00a0IPS`);
    const noJudged = n === 1 ? "1 log source not judged" : `${n} log sources not judged`;
    expect(text).toContain(noJudged);
  });

  it("uses the singular for one log source when the detector split is unavailable", () => {
    mocks.states[SUMMARY] = ok(
      summary({ spikes: 5, active_by_family: { log_source: activeCounts(1) } }),
    );
    render(<KpiAnomalies />);
    expect(document.body.textContent).toContain("1\u00a0log source");
  });

  it("names a family it could not read, never draws it as 0, and marks the total a floor", () => {
    mocks.states[SUMMARY] = ok(
      summary({
        monitored_sources: 1,
        active_by_family: {
          log_source: activeCounts(2),
          ips: activeCounts(1),
          property_watch: activeCounts(0),
        },
      }),
    );
    render(<KpiAnomalies />);
    expect(screen.getByText("\u2265 3", { selector: ".kpi-value" })).toBeInTheDocument();
    const also = screen.getByText(/Also:/).closest("p")!;
    expect(also).toHaveTextContent("WAF not read");
    expect(also).toHaveTextContent("1 IPS");
    expect(also).toHaveTextContent("0 property watches");
    expect(screen.getByText(/At least this many/)).toBeInTheDocument();
  });

  it("draws no total when no family was read", () => {
    mocks.states[SUMMARY] = ok(summary({ monitored_sources: 1, active_by_family: null }));
    render(<KpiAnomalies />);
    expect(screen.getByText("\u2014", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText("not read", { selector: ".kpi-reason" })).toBeInTheDocument();
    expect(screen.getByText(/Log source count not read/)).toBeInTheDocument();
  });

  it("is not empty when only a non-log-source family has an active anomaly", () => {
    mocks.states[SUMMARY] = ok(
      summary({
        active_by_family: {
          log_source: activeCounts(0),
          waf: activeCounts(1),
          ips: activeCounts(0),
          property_watch: activeCounts(0),
        },
      }),
    );
    render(<KpiAnomalies />);
    expect(screen.getByText("1", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.queryByText(/nothing to judge yet/)).toBeNull();
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
    expect(screen.getByText(/1 log source not judged; it is not in these counts/)).toBeInTheDocument();
  });

  it("does not present a fresh install with no monitored source as a measured zero", () => {
    mocks.states[SUMMARY] = ok(summary());
    render(<KpiAnomalies />);
    expect(screen.getByText("—", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText(/nothing to judge yet/)).toBeInTheDocument();
    expect(screen.queryByText("0", { selector: ".kpi-value" })).toBeNull();
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
    const alert = screen.getByRole("alert");
    expect(alert.querySelector(".widget-error-title")).toHaveTextContent(
      "The anomalies could not be read.",
    );
    expect(alert.querySelector(".widget-error-action")).toHaveTextContent(
      "HTTP 503. Nothing was changed. Use Refresh",
    );
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
