import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FleetClassTable } from "./FleetClassTable";
import type { FleetClassSummary } from "@/lib/api";

function cls(over: Partial<FleetClassSummary> = {}): FleetClassSummary {
  return {
    source_class: "WAF",
    sources: { enabled: 4, disabled: 1 },
    buckets: {
      expected: 1152,
      complete: 1152,
      complete_from_history: 0,
      partial: 0,
      missing: 0,
    },
    events: 3456000,
    mean_eps: 40,
    expected_eps: 38.5,
    baseline_sources: 4,
    baseline_share: 1,
    monitored_sources: 4,
    open_incidents: { VOLUME_SPIKE: 1 },
    top_sources: [{ id: "s1", name: "waf-dmz-01", events: 1800000 }],
    ...over,
  };
}

function row(name: string) {
  return screen.getByRole("row", { name: new RegExp(`^${name}`) });
}

describe("FleetClassTable", () => {
  it("shows figures with units and the sources split", () => {
    render(<FleetClassTable classes={[cls()]} windowName="24 hours" />);
    const r = row("WAF");
    expect(within(r).getByText("40.00")).toBeInTheDocument();
    expect(within(r).getByText("38.50")).toBeInTheDocument();
    expect(within(r).getByText("4 enabled, 1 disabled")).toBeInTheDocument();
    expect(within(r).getAllByText("EPS").length).toBe(2);
    expect(screen.getByRole("link", { name: "waf-dmz-01" })).toHaveAttribute(
      "href",
      "/behavior/sources/s1",
    );
  });

  it("says not measured, never 0, for a class with no complete bucket", () => {
    render(
      <FleetClassTable
        classes={[
          cls({
            source_class: "WINDOWS",
            events: null,
            mean_eps: null,
            buckets: {
              expected: 100,
              complete: 0,
              complete_from_history: 0,
              partial: 20,
              missing: 80,
            },
            top_sources: [],
          }),
        ]}
        windowName="24 hours"
      />,
    );
    const r = row("Windows");
    expect(within(r).getAllByText("Not measured")).toHaveLength(2);
    // The reason is stated once, under Events, not repeated under Mean EPS.
    expect(within(r).getAllByText("No complete bucket in the window.")).toHaveLength(1);
    expect(within(r).queryByText(/^0(\.00)?$/)).toBeNull();
    expect(within(r).getByText("Incomplete")).toBeInTheDocument();
  });

  it("keeps a measured zero as 0", () => {
    render(<FleetClassTable classes={[cls({ events: 0, mean_eps: 0 })]} windowName="24 hours" />);
    const r = row("WAF");
    expect(within(r).getByText("0")).toBeInTheDocument();
    expect(within(r).getByText("0.00")).toBeInTheDocument();
  });

  it("shows an em dash and its reason when no source has a baseline", () => {
    render(
      <FleetClassTable
        classes={[cls({ expected_eps: null, baseline_sources: 0, baseline_share: 0 })]}
        windowName="24 hours"
      />,
    );
    const r = row("WAF");
    expect(within(r).getByLabelText("No expectation")).toHaveTextContent("—");
    expect(within(r).getByText(/no source has a baseline/i)).toBeInTheDocument();
  });

  it("states the basis of a partial expectation", () => {
    render(
      <FleetClassTable
        classes={[cls({ baseline_sources: 3, baseline_share: 0.5 })]}
        windowName="24 hours"
      />,
    );
    expect(screen.getByText(/3 of 6 sources with a baseline/)).toBeInTheDocument();
    expect(screen.getByText(/covers part of the class only/i)).toBeInTheDocument();
  });

  it("shows the incomplete window next to the figures it qualifies", () => {
    render(
      <FleetClassTable
        classes={[
          cls({
            buckets: {
              expected: 100,
              complete: 80,
              complete_from_history: 0,
              partial: 12,
              missing: 8,
            },
          }),
        ]}
        windowName="24 hours"
      />,
    );
    const r = row("WAF");
    expect(within(r).getByText("Incomplete")).toBeInTheDocument();
    expect(within(r).getByText("80 of 100 buckets complete")).toBeInTheDocument();
    expect(within(r).getByText("12 partial, 8 missing")).toBeInTheDocument();
    expect(within(r).getByText("complete buckets only")).toBeInTheDocument();
  });

  it("adds the history share only when complete buckets came from history", () => {
    const live = render(<FleetClassTable classes={[cls()]} windowName="24 hours" />);
    expect(screen.queryByText(/complete buckets were read from QRadar history/)).toBeNull();
    live.unmount();

    render(
      <FleetClassTable
        classes={[
          cls({
            buckets: {
              expected: 1152,
              complete: 1152,
              complete_from_history: 864,
              partial: 0,
              missing: 0,
            },
          }),
        ]}
        windowName="24 hours"
      />,
    );
    expect(
      screen.getByText(
        "864 of 1,152 complete buckets were read from QRadar history, not judged.",
      ),
    ).toBeInTheDocument();
  });

  it("does not call an empty class complete", () => {
    render(
      <FleetClassTable
        classes={[
          cls({
            source_class: "PROXY",
            sources: { enabled: 0, disabled: 0 },
            buckets: {
              expected: 0,
              complete: 0,
              complete_from_history: 0,
              partial: 0,
              missing: 0,
            },
            events: null,
            mean_eps: null,
            expected_eps: null,
            baseline_sources: 0,
            baseline_share: null,
            open_incidents: {},
            top_sources: [],
          }),
        ]}
        windowName="24 hours"
      />,
    );
    const r = row("Proxy");
    expect(within(r).getAllByText("No monitored sources").length).toBeGreaterThan(0);
    expect(within(r).queryByText("Incomplete")).toBeNull();
  });
});
