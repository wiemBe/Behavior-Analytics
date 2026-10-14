import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { FleetEpsSeries } from "@/lib/api";

import { Sparkline } from "./Sparkline";

function make(values: (number | null)[], total = values.length): FleetEpsSeries {
  return {
    window: "24h",
    group_by: "none",
    window_start: "",
    window_end: "",
    bucket_seconds: 3600,
    step_seconds: 300,
    coverage: { steps_total: total, steps_measured: values.filter((v) => v !== null).length },
    series: [
      {
        key: "all",
        label: "All",
        sources_expected: 1,
        points: values.map((eps) => ({
          step_start: "",
          events: null,
          eps,
          expected_eps: null,
          cells_complete: 0,
          cells_expected: 0,
          sources_expected: 1,
        })),
      },
    ],
  };
}

describe("Sparkline", () => {
  it("breaks the path at a null step", () => {
    const { container } = render(<Sparkline series={make([1, 2, null, 3, 4])} />);
    const d = container.querySelector("path")?.getAttribute("d") ?? "";
    expect(d.match(/M/g)).toHaveLength(2);
  });

  it("draws no marker for any point", () => {
    const { container } = render(<Sparkline series={make([1, null, 3])} />);
    expect(container.querySelectorAll("circle, rect, use")).toHaveLength(0);
  });

  it("has a text equivalent with the measured count and the unit", () => {
    render(<Sparkline series={make([1, null, 3], 3)} />);
    expect(screen.getByText("24 h, 2 of 3 steps measured, peak 3.00 EPS")).toBeInTheDocument();
  });

  it("hides the drawing from assistive technology", () => {
    const { container } = render(<Sparkline series={make([1, 2])} />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("draws no line, not a flat zero, when no step was measured", () => {
    const { container } = render(<Sparkline series={make([null, null], 2)} />);
    expect(container.querySelector("svg, path, line")).toBeNull();
    expect(screen.queryByText(/no step measured/)).toBeNull();
  });

  it("uses no inline style", () => {
    const { container } = render(<Sparkline series={make([1, 2])} />);
    expect(container.querySelector("[style]")).toBeNull();
  });
});
