import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads, type FleetEpsSeries } from "@/lib/api";
import { fleet, fleetClass, noData } from "@/lib/home/fixtures";

import { KpiTotalEps } from "./KpiTotalEps";
import { failed, forbidden, ok } from "./KpiTestStates";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => mocks.states[key] ?? { status: "loading" },
}));

const SUMMARY = homeReads.fleetSummary("24h").key;
const SERIES = homeReads.epsSeries("24h", "none").key;

function series(values: (number | null)[], steps = values.length): FleetEpsSeries {
  return {
    window: "24h",
    group_by: "none",
    window_start: "2026-10-13T14:00:00Z",
    window_end: "2026-10-14T14:00:00Z",
    bucket_seconds: 3600,
    step_seconds: 300,
    coverage: { steps_total: steps, steps_measured: values.filter((v) => v !== null).length },
    series: [
      {
        key: "all",
        label: "All",
        sources_expected: 2,
        points: values.map((eps, i) => ({
          step_start: `2026-10-14T0${i}:00:00Z`,
          events: null,
          eps,
          expected_eps: null,
          cells_complete: 1,
          cells_expected: 1,
          sources_expected: 2,
        })),
      },
    ],
  };
}

beforeEach(() => {
  mocks.states = {};
});

describe("KpiTotalEps", () => {
  it("shows the mean in EPS with its window, the largest class and the drill-down", () => {
    mocks.states[SUMMARY] = ok(
      fleet([
        fleetClass("FIREWALL", { mean_eps: 800 }),
        fleetClass("WAF", { mean_eps: 200 }),
      ]),
    );
    mocks.states[SERIES] = ok(series([1, 2, 3]));
    render(<KpiTotalEps />);
    expect(screen.getByRole("heading", { name: "Total EPS, 24 h" })).toBeInTheDocument();
    expect(screen.getByText("1,000.00")).toBeInTheDocument();
    expect(screen.getByText("EPS")).toBeInTheDocument();
    // The definition is in the tooltip and the accessible description, not the body.
    expect(screen.getByTitle("Mean over 24 h, complete intervals only.")).toBeInTheDocument();
    expect(screen.getByRole("article")).toHaveAccessibleDescription(/Mean EPS over 24 h/);
    expect(screen.getByText("largest: Firewall 80 %")).toBeInTheDocument();
    expect(screen.getByTitle("Firewall, 800.00 EPS of the total")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View fleet by class" })).toHaveAttribute(
      "href",
      "/fleet?window=24h",
    );
    // No always-on "Received" line: the top bar says when the page refreshed.
    expect(screen.queryByText(/Received/)).toBeNull();
  });

  it.each(["1h", "7d"] as const)("keeps the fleet window %s on the drill-down", (window) => {
    render(<KpiTotalEps range={window} />);
    expect(screen.getByRole("link", { name: "View fleet by class" })).toHaveAttribute(
      "href",
      `/fleet?window=${window}`,
    );
  });

  it("shows an em dash and the reason, never 0, when no class has a mean", () => {
    mocks.states[SUMMARY] = ok(fleet([noData("FIREWALL")]));
    mocks.states[SERIES] = ok(series([null, null]));
    render(<KpiTotalEps />);
    expect(screen.getByText("—", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText("No class has a complete bucket in this window.")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).toBeNull();
  });

  it("says Partial with the interval count when intervals are missing", () => {
    mocks.states[SUMMARY] = ok(
      fleet([
        fleetClass("FIREWALL", {
          mean_eps: 5,
          buckets: {
            expected: 48,
            complete: 40,
            complete_from_history: 0,
            partial: 2,
            missing: 6,
          },
        }),
      ]),
    );
    render(<KpiTotalEps />);
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText("40 of 48 intervals")).toBeInTheDocument();
  });

  it("names classes left out of the sum", () => {
    mocks.states[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: 5 }), noData("WAF")]));
    render(<KpiTotalEps />);
    expect(screen.getByText("1 class not in sum")).toBeInTheDocument();
  });

  it("writes the baseline coverage and compares on those classes only", () => {
    mocks.states[SUMMARY] = ok(
      fleet([
        fleetClass("FIREWALL", {
          mean_eps: 90,
          expected_eps: 100,
          baseline_sources: 1,
          monitored_sources: 2,
        }),
      ]),
    );
    render(<KpiTotalEps />);
    expect(
      screen.getByTitle("Compared on baselined classes only: observed 90.00 EPS."),
    ).toHaveTextContent("expected 100.00 EPS · \u221210.0 %");
    expect(screen.getByText(/baselines 1 of 2 sources/)).toBeInTheDocument();
    // The "compared on those classes only" caveat is in the tooltip.
    expect(
      screen.getByTitle("Compared on baselined classes only: observed 90.00 EPS."),
    ).toBeInTheDocument();
  });

  it("shows no expected figure and no percentage without a baseline", () => {
    mocks.states[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: 5 })]));
    render(<KpiTotalEps />);
    expect(screen.getByText(/expected \u2014 \(no reliable baseline\)/)).toBeInTheDocument();
    expect(screen.queryByText(/\d\.\d %/)).toBeNull();
  });

  it("draws the trend with a text equivalent and a gap for a null step", () => {
    mocks.states[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: 5 })]));
    mocks.states[SERIES] = ok(series([1, null, 3, 4], 4));
    const { container } = render(<KpiTotalEps />);
    expect(screen.getByText(/24 h, 3 of 4 steps measured, peak 4.00 EPS/)).toBeInTheDocument();
    expect(screen.getByTitle(/24 h, 3 of 4 steps measured/)).toBeInTheDocument();
    const d = container.querySelector(".kpi-spark-line")?.getAttribute("d") ?? "";
    expect(d.match(/M/g)).toHaveLength(2);
  });

  it("keeps the card when the trend fails, and says so", () => {
    mocks.states[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: 5 })]));
    mocks.states[SERIES] = failed(500);
    render(<KpiTotalEps />);
    expect(screen.getByText("Trend unavailable (HTTP 500).")).toBeInTheDocument();
    expect(screen.getByText("5.00")).toBeInTheDocument();
  });

  it("is a polite status while loading", () => {
    render(<KpiTotalEps />);
    const s = screen.getByRole("status");
    expect(s).toHaveTextContent("Loading fleet EPS…");
    expect(s).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("link", { name: "View fleet by class" })).toBeInTheDocument();
  });

  it("names the permission when the role cannot read it", () => {
    mocks.states[SUMMARY] = forbidden;
    render(<KpiTotalEps />);
    expect(
      screen.getByText("Your role cannot read fleet EPS (permission read:fleet). Nothing was changed."),
    ).toBeInTheDocument();
  });

  it("says what failed and what to do, without exception text", () => {
    mocks.states[SUMMARY] = failed(502);
    render(<KpiTotalEps />);
    const alert = screen.getByRole("alert");
    expect(alert.querySelector(".widget-error-title")).toHaveTextContent(
      "The fleet EPS could not be read.",
    );
    expect(alert.querySelector(".widget-error-action")).toHaveTextContent(
      "HTTP 502. Nothing was changed. Use Refresh in the top bar",
    );
  });

  it("keeps the old data and says the refresh failed", () => {
    mocks.states[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: 5 })]), {
      refreshFailed: { httpStatus: 503 },
    });
    render(<KpiTotalEps />);
    expect(screen.getByText("5.00")).toBeInTheDocument();
    const note = screen.getByText(/Refresh failed; showing data received at/);
    expect(within(note).getByText("13:55 UTC")).toBeInTheDocument();
  });
});
