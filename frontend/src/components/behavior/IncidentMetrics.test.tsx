import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IncidentMetrics } from "./IncidentMetrics";
import type { AnomalyDetail } from "@/lib/api";

function anomaly(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "LAB Firewall",
    anomaly_type: "VOLUME_DROP",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 4.2,
    expected_value: 28.6,
    deviation_ratio: 0.1469,
    robust_z: null,
    absolute_delta: -1464,
    consecutive_buckets: 6,
    confidence: 0.42,
    detected_at: "2026-10-12T09:20:00Z",
    opened_at: null,
    anomaly_start: "2026-10-12T09:15:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    baseline_version: 1,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: null,
    ...over,
  };
}

const view = (a: AnomalyDetail, limited = false) =>
  render(<IncidentMetrics anomaly={a} confidenceLimited={limited} />);

function measure(name: string): HTMLElement {
  const dt = screen.getByText(name, { selector: "dt" });
  return dt.closest(".measure-item") as HTMLElement;
}

describe("IncidentMetrics", () => {
  it("compares observed with expected, both in EPS", () => {
    view(anomaly());
    expect(within(measure("Observed")).getByText("4.20")).toBeInTheDocument();
    expect(within(measure("Observed")).getByText("EPS")).toBeInTheDocument();
    expect(within(measure("Expected")).getByText("28.60")).toBeInTheDocument();
    expect(within(measure("Expected")).getByText("EPS")).toBeInTheDocument();
  });

  it("gives the deviation as a ratio and as a signed absolute delta in events", () => {
    view(anomaly());
    const dev = within(measure("Deviation"));
    expect(dev.getByText("0.15x")).toBeInTheDocument();
    expect(dev.getByText("-1,464")).toBeInTheDocument();
    expect(measure("Deviation")).toHaveTextContent("events");
  });

  it("renders a measured zero as zero, not as a missing value", () => {
    // A NO_EVENTS incident: silence in a complete bucket is the finding.
    view(anomaly({ observed_value: 0, deviation_ratio: 0, absolute_delta: -444 }));
    expect(within(measure("Observed")).getByText("0.00")).toBeInTheDocument();
    expect(within(measure("Deviation")).getByText("0.00x")).toBeInTheDocument();
  });

  it("renders an unmeasured value as an em dash, never as zero", () => {
    view(
      anomaly({
        observed_value: null,
        expected_value: null,
        deviation_ratio: null,
        absolute_delta: null,
        confidence: null,
      }),
    );
    expect(within(measure("Observed")).getByText("—")).toBeInTheDocument();
    expect(within(measure("Expected")).getByText("—")).toBeInTheDocument();
    expect(within(measure("Deviation")).getAllByText("—").length).toBeGreaterThan(0);
    expect(measure("Observed")).not.toHaveTextContent("0.00");
    expect(screen.getByText("Detector confidence").parentElement).toHaveTextContent("—");
  });

  it("calls a still-running incident running, with no duration of 0s", () => {
    view(anomaly({ anomaly_end: null, duration_seconds: null }));
    const dur = measure("Duration");
    expect(dur).toHaveTextContent("Still running");
    expect(dur).toHaveTextContent("—");
    expect(dur).not.toHaveTextContent("0s");
  });

  it("shows the length of a closed incident", () => {
    view(anomaly({ anomaly_end: "2026-10-12T09:19:00Z", duration_seconds: 240 }));
    expect(measure("Duration")).toHaveTextContent("4m");
    expect(measure("Duration")).toHaveTextContent("Interval length");
  });

  it("labels confidence as the detector's own score, outside the four measures", () => {
    const { container } = view(anomaly());
    expect(container.querySelectorAll(".measure-grid > .measure-item")).toHaveLength(4);
    expect(screen.getByText("Detector confidence")).toBeInTheDocument();
    expect(container.querySelector(".measure-foot")).toHaveTextContent("0.42");
  });

  it("states a confidence limitation where confidence is read", () => {
    const { container } = view(anomaly(), true);
    expect(container.querySelector(".measure-foot")).toHaveTextContent(
      /Confidence limited because baseline variability is zero/,
    );
  });

  it("adds no limitation when the score is sound", () => {
    const { container } = view(anomaly(), false);
    expect(container.querySelector(".measure-foot")).not.toHaveTextContent(/limited/);
  });

  it("is a named region", () => {
    view(anomaly());
    expect(screen.getByRole("region", { name: "Incident measurements" })).toBeInTheDocument();
  });
});
