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
    const lines = screen.getAllByText((_, node) => node?.classList.contains("kpi-line") ?? false);
    expect(
      Array.from(lines[0].querySelectorAll(".kpi-line-part"), (part) => part.textContent),
    ).toEqual(["3\u00a0critical (\u2265 7) \u00b7", "8\u00a0below"]);
    expect(lines[0].textContent?.replaceAll("\u00a0", " ")).toBe(
      "3 critical (\u2265 7) \u00b7 8 below",
    );
    // The offense with no magnitude is its own qualifier line, not a third breakdown part.
    expect(lines[1].textContent).toBe("+\u00a01\u00a0with unknown magnitude");
    expect(
      Array.from(lines[2].querySelectorAll(".kpi-line-part"), (part) => part.textContent),
    ).toEqual(["5\u00a0unassigned \u00b7", "2\u00a0over SLA"]);
    expect(lines[2].textContent?.replaceAll("\u00a0", " ")).toBe("5 unassigned \u00b7 2 over SLA");
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

  it("says Not collected from a null count even when the probe lists rows", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: null }));
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(screen.getByText("—", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("No offense snapshot has been collected yet.")).toBeInTheDocument();
    expect(screen.queryByText("0", { selector: ".kpi-value" })).toBeNull();
  });

  it("does not wait for the probe when the count is already unknown", () => {
    mocks.states[ANALYTICS] = ok(analytics({ active: null }));
    render(<KpiOffenses />);
    expect(screen.getByText("—", { selector: ".kpi-value" })).toBeInTheDocument();
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("No offense snapshot has been collected yet.")).toBeInTheDocument();
    expect(screen.queryByText(/Loading offenses/)).toBeNull();
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
    expect(alert.querySelector(".widget-error-title")).toHaveTextContent(
      "The offenses could not be read.",
    );
    expect(alert.querySelector(".widget-error-action")).toHaveTextContent(
      "The backend did not answer. Nothing was changed. Use Refresh",
    );
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

describe("KpiOffenses unknown counts", () => {
  const lineTexts = () =>
    screen
      .getAllByText((_, node) => node?.classList.contains("kpi-line") ?? false)
      .map((n) => n.textContent);

  it("says how many open offenses have no magnitude and no age, in words", () => {
    mocks.states[ANALYTICS] = ok(
      analytics({
        active: 12, critical: 3, unassigned: 5, exceeding_sla: 2,
        critical_unknown: 4, age_unknown: 2,
      }),
    );
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(lineTexts()).toEqual([
      "3\u00a0critical (\u2265 7) \u00b7 5\u00a0below",
      "+\u00a04\u00a0with unknown magnitude",
      "5\u00a0unassigned \u00b7 2\u00a0over SLA",
      "+\u00a02\u00a0with unknown age",
    ]);
    expect(screen.getByRole("link", { name: "View open offenses" })).toHaveAttribute(
      "href",
      "/offenses?status=OPEN",
    );
  });

  it("adds nothing when no open offense is unknown", () => {
    mocks.states[ANALYTICS] = ok(
      analytics({
        active: 12, critical: 3, unassigned: 5, exceeding_sla: 2,
        critical_unknown: 0, age_unknown: 0,
      }),
    );
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(lineTexts()).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/with unknown/);
  });

  it("shows the age qualifier alone, and the magnitude one alone", () => {
    mocks.states[ANALYTICS] = ok(
      analytics({ active: 6, critical: 1, critical_unknown: 0, age_unknown: 3 }),
    );
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(document.body.textContent).toContain("+\u00a03\u00a0with unknown age");
    expect(document.body.textContent).not.toContain("unknown magnitude");
  });

  it("shows neither qualifier when nothing was collected", () => {
    mocks.states[ANALYTICS] = ok(
      analytics({ active: null, critical: null, critical_unknown: null, age_unknown: null }),
    );
    mocks.states[PROBE] = withSnapshot();
    render(<KpiOffenses />);
    expect(document.body.textContent).not.toMatch(/with unknown/);
  });
});
