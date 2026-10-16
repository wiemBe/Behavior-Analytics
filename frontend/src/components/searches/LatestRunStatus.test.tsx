import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LatestRunStatus } from "./LatestRunStatus";
import type { SearchExecution } from "@/lib/api";

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1",
    search_id: "s-1",
    query_version: 2,
    status: "COMPLETED",
    trigger: "MANUAL",
    triggered_by: null,
    ariel_search_id: null,
    ariel_status: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    result_count: 0,
    truncated: false,
    error_type: null,
    error_message: null,
    retry_count: 0,
    threshold_breached: false,
    ...over,
  };
}

describe("LatestRunStatus", () => {
  it("shows a measured empty result as zero", () => {
    render(<LatestRunStatus execution={execution()} consecutiveFailures={0} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("returned 0 rows");
    expect(status).not.toHaveClass("ok");
  });

  it("does not draw a missing count as zero", () => {
    render(
      <LatestRunStatus
        execution={execution({ result_count: null })}
        consecutiveFailures={null}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("row count was not recorded");
    expect(status).not.toHaveTextContent("0 rows");
  });

  it("shows a truncated run as a lower bound with no verdict", () => {
    render(
      <LatestRunStatus
        execution={execution({ truncated: true, result_count: 500, threshold_breached: false })}
        consecutiveFailures={0}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveClass("warn");
    expect(status).toHaveTextContent("at least 500");
    expect(status).toHaveTextContent("no verdict");
    expect(status).not.toHaveTextContent("returned 500");
  });

  it("names a failed run's error type and the consecutive-failure count", () => {
    render(
      <LatestRunStatus
        execution={execution({ status: "FAILED", result_count: null, error_type: "TIMEOUT" })}
        consecutiveFailures={2}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveClass("crit");
    expect(status).toHaveTextContent("The latest run failed.");
    expect(status).toHaveTextContent("Error type TIMEOUT");
    expect(status).toHaveTextContent("The search exceeded its timeout.");
    expect(status).toHaveTextContent("Consecutive failures recorded: 2");
    expect(status).toHaveTextContent("after 3");
  });

  it("says when a truncated run already meets the threshold", () => {
    render(
      <LatestRunStatus
        execution={execution({ truncated: true, result_count: null, threshold_breached: true })}
        consecutiveFailures={0}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("not shown as zero");
    expect(status).toHaveTextContent("already meets the threshold");
  });
});
