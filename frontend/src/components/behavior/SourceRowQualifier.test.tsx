import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SourceBehavior, SourceEvaluation } from "@/lib/api";

import { SourceRowQualifier } from "./SourceRowQualifier";

function evaluation(over: Partial<SourceEvaluation> = {}): SourceEvaluation {
  return {
    judged_through: "2026-10-22T17:05:00Z",
    failed_bucket_start: "2026-10-22T17:10:00Z",
    attempts: 2,
    max_attempts: 3,
    error_class: "RuntimeError",
    last_failure_at: "2026-10-22T19:52:00Z",
    given_up_at: null,
    resolved_at: null,
    ...over,
  };
}

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "s-1",
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 50,
    expected_eps: 50,
    expected_low: 45,
    expected_high: 55,
    deviation_ratio: 1,
    state: "OPEN",
    baseline_sample_count: 30,
    baseline_completeness: 1,
    last_bucket_at: "2026-10-22T17:30:00Z",
    last_event_at: "2026-10-22T17:34:00Z",
    open_anomaly_count: 1,
    evaluation: evaluation(),
    skipped_buckets_in_window: 0,
    skipped_buckets: [],
    ...over,
  };
}

describe("SourceRowQualifier", () => {
  it("keeps Not judged since for a retrying fault", () => {
    render(<SourceRowQualifier source={source()} />);
    expect(screen.getByText(/Not judged since/)).toBeInTheDocument();
    expect(screen.getByText("10-22 17:05 UTC")).toBeInTheDocument();
    expect(screen.queryByText(/given up/)).toBeNull();
  });

  it("appends given-up buckets on a retrying row", () => {
    render(<SourceRowQualifier source={source({ skipped_buckets_in_window: 1 })} />);
    expect(screen.getByText(/Not judged since/)).toBeInTheDocument();
    expect(screen.getByText(/1 bucket given up in 24 h/)).toBeInTheDocument();
  });

  it("omits the given-up count when that count is not known", () => {
    render(
      <SourceRowQualifier
        source={source({
          skipped_buckets_in_window: null,
          skipped_buckets: [
            {
              bucket_start: "2026-10-22T16:40:00Z",
              error_class: "RuntimeError",
              attempts: 3,
              given_up_at: "2026-10-22T16:50:00Z",
              last_failure_at: "2026-10-22T16:50:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/Not judged since/)).toBeInTheDocument();
    expect(screen.getByText(/buckets given up in 24 h/)).toBeInTheDocument();
    expect(screen.queryByText(/1 bucket given up/)).toBeNull();
  });

  it("names one given-up bucket after resume, with no since", () => {
    render(
      <SourceRowQualifier
        source={source({
          evaluation: evaluation({
            judged_through: "2026-10-22T17:30:00Z",
            attempts: 3,
            given_up_at: "2026-10-22T19:54:00Z",
          }),
          skipped_buckets_in_window: 1,
          skipped_buckets: [
            {
              bucket_start: "2026-10-22T17:10:00Z",
              error_class: "RuntimeError",
              attempts: 3,
              given_up_at: "2026-10-22T19:54:00Z",
              last_failure_at: "2026-10-22T19:54:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/1 bucket not judged/)).toBeInTheDocument();
    expect(screen.getByText("10-22 17:10 UTC")).toBeInTheDocument();
    expect(screen.queryByText(/Not judged since/)).toBeNull();
    expect(screen.queryByText("10-22 17:30 UTC")).toBeNull();
  });

  it("names the latest of several given-up buckets", () => {
    render(
      <SourceRowQualifier
        source={source({
          evaluation: evaluation({
            judged_through: "2026-10-22T17:30:00Z",
            attempts: 3,
            given_up_at: "2026-10-22T19:54:00Z",
          }),
          skipped_buckets_in_window: 2,
          skipped_buckets: [
            {
              bucket_start: "2026-10-22T17:10:00Z",
              error_class: "RuntimeError",
              attempts: 3,
              given_up_at: "2026-10-22T19:54:00Z",
              last_failure_at: "2026-10-22T19:54:00Z",
            },
            {
              bucket_start: "2026-10-22T16:40:00Z",
              error_class: "RuntimeError",
              attempts: 3,
              given_up_at: "2026-10-22T16:50:00Z",
              last_failure_at: "2026-10-22T16:50:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/2 buckets not judged in 24 h/)).toBeInTheDocument();
    expect(screen.getByText("10-22 17:10 UTC")).toBeInTheDocument();
    expect(screen.queryByText(/Not judged since/)).toBeNull();
  });

  it("omits the count when a given-up count is not known", () => {
    render(
      <SourceRowQualifier
        source={source({
          evaluation: evaluation({
            judged_through: "2026-10-22T17:30:00Z",
            attempts: 3,
            given_up_at: "2026-10-22T19:54:00Z",
          }),
          skipped_buckets_in_window: null,
          skipped_buckets: [
            {
              bucket_start: "2026-10-22T17:10:00Z",
              error_class: "RuntimeError",
              attempts: 3,
              given_up_at: "2026-10-22T19:54:00Z",
              last_failure_at: "2026-10-22T19:54:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/bucket not judged/)).toBeInTheDocument();
    expect(screen.queryByText(/1 bucket not judged/)).toBeNull();
    expect(screen.getByText("10-22 17:10 UTC")).toBeInTheDocument();
  });
});
