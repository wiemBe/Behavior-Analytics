import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AnomalyDetail, DetectionDetail, RobustScoreStatus } from "@/lib/api";

import { TechnicalDetails } from "./TechnicalDetails";

function detection(
  robust_score_status: RobustScoreStatus | null,
): DetectionDetail {
  return {
    reason: "Observed volume exceeded the expected range.",
    expected_low: 10,
    expected_high: 20,
    threshold: 3.5,
    baseline_sample_count: 28,
    baseline_completeness: 0.95,
    baseline_version: 2,
    observed_eps: 31,
    expected_eps: 15,
    observed_events: 1_860,
    expected_events: 900,
    absolute_delta_events: 960,
    bucket_seconds: 60,
    ratio: 2.07,
    ratio_basis: null,
    robust_score_status,
    robust_z: 21.4,
    fallback_bound: 22,
  };
}

function anomaly(robust_score_status: RobustScoreStatus | null): AnomalyDetail {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "Lab source",
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 31,
    expected_value: 15,
    deviation_ratio: 2.07,
    robust_z: 21.4,
    absolute_delta: 960,
    consecutive_buckets: 3,
    confidence: 0.9,
    detected_at: "2026-10-12T09:05:00Z",
    opened_at: "2026-10-12T09:15:00Z",
    anomaly_start: "2026-10-12T09:00:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: null,
    baseline_version: 2,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: detection(robust_score_status),
  };
}

function robustZRow(): HTMLElement {
  return screen.getByText("Robust z observed").closest("div")!;
}

describe("TechnicalDetails robust score", () => {
  it.each([
    ["OK", "21.40"],
    [null, "21.40"],
  ] as const)("shows the robust z figure when status is %s", (status, figure) => {
    render(<TechnicalDetails anomaly={anomaly(status)} packaged={null} />);
    expect(robustZRow()).toHaveTextContent(figure);
  });

  it.each([
    ["DEGENERATE", "not used: MAD was zero"],
    ["UNAVAILABLE", "not computed"],
  ] as const)("withholds the stored robust z when status is %s", (status, reason) => {
    render(<TechnicalDetails anomaly={anomaly(status)} packaged={null} />);
    expect(robustZRow()).toHaveTextContent(reason);
    expect(robustZRow()).not.toHaveTextContent("21.40");
  });
});
