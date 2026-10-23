import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { BaselineCell, LogSourceDetail, SourceBehavior } from "@/lib/api";
import type { BaselineQuality, CollectionHealth } from "@/lib/sourceDetail";

import { SourceAdvanced } from "./SourceAdvanced";
import { SourceQuality } from "./SourceQuality";

const cell = (over: Partial<BaselineCell> = {}): BaselineCell => ({
  metric_name: "average_eps",
  weekday: 3,
  hour: 9,
  median: 2,
  mad: 0.5,
  p05: 1.7,
  p95: 2.3,
  sample_count: 12,
  is_reliable: true,
  completeness: 0.9,
  baseline_version: 3,
  computed_at: "2026-07-20T09:00:00Z",
  ...over,
});

function advanced(behavior: Partial<SourceBehavior>, cells: BaselineCell[]) {
  render(
    <SourceAdvanced
      behavior={behavior as SourceBehavior}
      meta={null as LogSourceDetail | null}
      buckets={[]}
      cells={cells}
      cellsFailed={false}
    />,
  );
}

describe("source completeness is spelled by the shared percent formatter", () => {
  it("shows the baseline and per-cell completeness with one decimal and a spaced sign", () => {
    advanced({ baseline_completeness: 0.875 } as Partial<SourceBehavior>, [cell({ completeness: 0.9 })]);
    expect(screen.getByText("87.5 %")).toBeInTheDocument();
    expect(screen.getByText("90.0 %")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d%/);
  });

  it("draws a missing completeness as a dash, never 0.0 %", () => {
    advanced({} as Partial<SourceBehavior>, []);
    expect(screen.queryByText("0.0 %")).toBeNull();
    expect(screen.queryByText("NaN%")).toBeNull();
  });

  it("shows the cell completeness in the baseline quality panel", () => {
    const baseline = {
      status: "OK",
      label: "Reliable",
      tone: "ok",
      explanation: "x",
      cell: cell({ completeness: 0.9 }),
    } as unknown as BaselineQuality;
    const collection = {
      status: "CURRENT",
      label: "Current",
      tone: "ok",
      explanation: "y",
      expectedBuckets: 12,
      observedBuckets: 12,
      incompleteBuckets: 0,
      lagSeconds: 30,
    } as unknown as CollectionHealth;
    render(
      <SourceQuality
        baseline={baseline}
        collection={collection}
        expectedLow={1.7}
        expectedHigh={2.3}
        lastBucketAt="2026-07-20T10:00:00Z"
        qradarStatus={null}
      />,
    );
    expect(screen.getByText("90.0 %")).toBeInTheDocument();
  });
});
