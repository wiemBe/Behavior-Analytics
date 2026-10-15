// The alert evidence table: every figure names the unit its detector measures it in, a missing
// figure is a dash with no unit after it, and the layout carries no inline style.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Evidence } from "./Evidence";

const VOLUME = {
  anomaly_type: "VOLUME_SPIKE",
  observed_value: 182.4,
  expected_value: 63.8,
  baseline_low: 49,
  baseline_high: 82.6,
  deviation_score: 5.4,
  threshold: 3.5,
  sample_count: 672,
  confidence: 0.94,
  reason: "average EPS 182.4 is above the baseline median 63.8",
};

function row(label: string): HTMLElement {
  return screen.getByText(label).closest("tr") as HTMLElement;
}

describe("Evidence units", () => {
  it("states EPS, z, samples and the 0-to-1 confidence for a volume detector", () => {
    render(<Evidence data={VOLUME} />);
    expect(row("Observed")).toHaveTextContent("182.4EPS");
    expect(row("Expected")).toHaveTextContent("63.8EPS");
    expect(row("Baseline low")).toHaveTextContent("49EPS");
    expect(row("Baseline high")).toHaveTextContent("82.6EPS");
    expect(row("Deviation")).toHaveTextContent("5.4z");
    expect(row("Threshold")).toHaveTextContent("3.5z");
    expect(row("Samples")).toHaveTextContent("672baseline samples");
    expect(row("Confidence")).toHaveTextContent("0.94(0 to 1)");
  });

  it("shows a parse-ratio share as a percentage", () => {
    render(
      <Evidence
        data={{
          anomaly_type: "PARSING_DEGRADATION",
          observed_value: 0.612,
          expected_value: 0.9,
          deviation_score: 0.288,
          threshold: 0.9,
          sample_count: 48210,
        }}
      />,
    );
    expect(row("Observed")).toHaveTextContent("61.2%");
    expect(row("Expected")).toHaveTextContent("90%");
    expect(row("Deviation")).toHaveTextContent("28.8pp");
    expect(row("Samples")).toHaveTextContent("48,210events");
  });

  it("uses seconds for a delay and singular units for a count of one", () => {
    render(
      <Evidence
        data={{
          anomaly_type: "TIMESTAMP_DELAY",
          observed_value: 1840,
          threshold: 900,
          deviation_score: 2.04,
          sample_count: 1,
        }}
      />,
    );
    expect(row("Observed")).toHaveTextContent("1,840s");
    expect(row("Deviation")).toHaveTextContent("2.04× limit");
    expect(row("Samples")).toHaveTextContent("1interval");
    expect(row("Samples")).not.toHaveTextContent("intervals");
  });

  it("puts no unit after a figure whose detector this table does not know", () => {
    render(<Evidence data={{ observed_value: 412, expected_value: 100, operator: ">" }} />);
    expect(row("Observed").textContent).toBe("Observed412");
    expect(row("Expected").textContent).toBe("Expected100");
  });

  it("draws a missing figure as a dash with no unit after it", () => {
    render(<Evidence data={{ ...VOLUME, expected_value: null, baseline_low: undefined }} />);
    expect(row("Expected").textContent).toBe("Expected—");
    expect(row("Baseline low").textContent).toBe("Baseline low—");
    expect(row("Observed")).toHaveTextContent("182.4EPS");
  });

  it("does not read an absent deviation as zero", () => {
    render(<Evidence data={{ ...VOLUME, anomaly_type: "NO_EVENTS", deviation_score: null }} />);
    expect(row("Deviation").textContent).toBe("Deviation—");
  });
});

describe("Evidence layout", () => {
  it("right-aligns each figure with tabular figures and sets no inline style", () => {
    const { container } = render(<Evidence data={VOLUME} />);
    const figures = container.querySelectorAll("td.evidence-value");
    expect(figures).toHaveLength(8);
    figures.forEach((td) => expect(td.className).toContain("num"));
    expect(container.querySelectorAll("[style]")).toHaveLength(0);
  });

  it("says when no evidence was captured", () => {
    render(<Evidence data={{}} />);
    expect(screen.getByText("no evidence captured")).toBeInTheDocument();
  });

  it("shows the detector's reason above the figures", () => {
    render(<Evidence data={VOLUME} />);
    expect(screen.getByText(VOLUME.reason)).toBeInTheDocument();
  });
});
