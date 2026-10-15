import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VolumeChart, mergeIntervals } from "./VolumeChart";
import type { MetricBucket } from "@/lib/api";

// ECharts is stubbed as in VolumeChart.test.tsx: the contract under test is the
// mark-area data the chart is handed.
const { setOption } = vi.hoisted(() => ({ setOption: vi.fn() }));

vi.mock("echarts/core", () => ({
  init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })),
  use: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.ResizeObserver = class {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  } as unknown as typeof ResizeObserver;
});

function bucket(start: string): MetricBucket {
  return {
    bucket_start: start,
    bucket_seconds: 300,
    event_count: 600,
    average_eps: 2,
    peak_eps: null,
    completeness: "COMPLETE",
    last_event_at: null,
  };
}

type Area = [{ xAxis: number; name?: string }, { xAxis: number }];

function areas(): Area[] {
  const option = setOption.mock.calls[0][0] as {
    series: Array<{ name: string; markArea?: { data: Area[] } }>;
  };
  // The two buckets are hours apart, so the chart also shades the uncollected gap.
  return option.series
    .find((s) => s.name === "Observed EPS")!
    .markArea!.data.filter((a) => a[0].name === "Anomalous interval");
}

const t = (hhmm: string) => Date.parse(`2026-07-20T${hhmm}:00Z`);

describe("mergeIntervals", () => {
  it("unites overlapping and touching spans, and keeps disjoint ones apart", () => {
    expect(
      mergeIntervals([
        { start: t("12:00"), end: t("13:00") },
        { start: t("10:00"), end: t("11:00") },
        { start: t("10:30"), end: t("11:30") },
        { start: t("11:30"), end: t("11:45") },
      ]),
    ).toEqual([
      { start: t("10:00"), end: t("11:45") },
      { start: t("12:00"), end: t("13:00") },
    ]);
  });

  it("lets a span that contains another absorb it", () => {
    expect(
      mergeIntervals([
        { start: t("10:00"), end: t("14:00") },
        { start: t("11:00"), end: t("12:00") },
      ]),
    ).toEqual([{ start: t("10:00"), end: t("14:00") }]);
  });
});

describe("overlapping anomalies on the volume chart", () => {
  const props = { expected: 2, expectedLow: null, expectedHigh: null } as const;

  it("paints two overlapping incidents as one band, so their alpha cannot stack", () => {
    render(
      <VolumeChart
        {...props}
        buckets={[bucket("2026-07-20T09:00:00Z"), bucket("2026-07-20T13:00:00Z")]}
        anomalies={[
          { start: "2026-07-20T10:00:00Z", end: "2026-07-20T12:00:00Z" },
          { start: "2026-07-20T11:00:00Z", end: "2026-07-20T12:30:00Z" },
        ]}
      />,
    );
    const drawn = areas();
    expect(drawn).toHaveLength(1);
    expect(drawn[0][0].xAxis).toBe(t("10:00"));
    expect(drawn[0][1].xAxis).toBe(t("12:30"));
  });

  it("merges a still-running incident into the one it overlaps", () => {
    render(
      <VolumeChart
        {...props}
        buckets={[bucket("2026-07-20T09:00:00Z"), bucket("2026-07-20T13:00:00Z")]}
        anomalies={[
          { start: "2026-07-20T10:00:00Z", end: "2026-07-20T12:00:00Z" },
          { start: "2026-07-20T11:00:00Z", end: null },
        ]}
      />,
    );
    const drawn = areas();
    expect(drawn).toHaveLength(1);
    expect(drawn[0][1].xAxis).toBe(t("13:00"));
  });

  it("keeps two separate incidents as two bands", () => {
    render(
      <VolumeChart
        {...props}
        buckets={[bucket("2026-07-20T09:00:00Z"), bucket("2026-07-20T13:00:00Z")]}
        anomalies={[
          { start: "2026-07-20T09:30:00Z", end: "2026-07-20T10:00:00Z" },
          { start: "2026-07-20T11:00:00Z", end: "2026-07-20T12:00:00Z" },
        ]}
      />,
    );
    expect(areas()).toHaveLength(2);
  });
});
