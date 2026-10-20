import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LogSourceDetail, MetricBucket, SourceBehavior } from "@/lib/api";

import { SourceAdvanced } from "./SourceAdvanced";

const at = (m: number) => new Date(Date.UTC(2026, 9, 12, 9, m)).toISOString();
const bucket = (m: number, over: Partial<MetricBucket> = {}): MetricBucket => ({
  bucket_start: at(m),
  bucket_seconds: 300,
  event_count: 600,
  average_eps: 2,
  peak_eps: null,
  completeness: "COMPLETE",
  last_event_at: null,
  ...over,
});

function table(buckets: MetricBucket[]) {
  render(
    <SourceAdvanced
      behavior={{} as SourceBehavior}
      meta={null as LogSourceDetail | null}
      buckets={buckets}
      cells={[]}
      cellsFailed={false}
    />,
  );
}

describe("the metric bucket table: origin", () => {
  it("marks a history row in text and says nothing on a live row", () => {
    table([bucket(0, { origin: "HISTORY" }), bucket(5, { origin: "LIVE" })]);
    expect(screen.getAllByText("read from QRadar history, not judged")).toHaveLength(1);
    expect(screen.queryByText("origin unknown")).toBeNull();
  });
});
