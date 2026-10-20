import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MetricBucket } from "@/lib/api";

import { VolumeChart } from "./VolumeChart";

const { setOption, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })),
    use: vi.fn(),
  };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

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

interface Option {
  tooltip: { formatter: (p: unknown) => string };
  series: Array<{ name: string; markArea?: { data: Array<[{ name: string }, unknown]> } }>;
}
const option = () => setOption.mock.calls[0][0] as Option;
const areas = () => option().series[0].markArea?.data ?? [];
const tip = (i: number) => option().tooltip.formatter({ dataIndex: i });

function chart(buckets: MetricBucket[]) {
  return render(
    <VolumeChart buckets={buckets} expected={2} expectedLow={null} expectedHigh={null} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.ResizeObserver = class {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  } as unknown as typeof ResizeObserver;
});

describe("VolumeChart: buckets read from QRadar history", () => {
  const mixed = [
    bucket(0, { origin: "HISTORY" }),
    bucket(5, { origin: "HISTORY" }),
    bucket(10, { origin: "LIVE" }),
    bucket(15, { origin: "LIVE" }),
  ];

  it("shades the contiguous history buckets as one labelled span", () => {
    chart(mixed);
    expect(areas()).toHaveLength(1);
    expect(areas()[0][0].name).toBe("read from QRadar history, not judged");
  });

  it("says so in the tooltip of a history point only", () => {
    chart(mixed);
    expect(tip(0)).toContain("Read from QRadar history, not judged");
    expect(tip(2)).not.toContain("history");
  });

  it("keys the span and names it for assistive technology", () => {
    chart(mixed);
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent(
      "Read from QRadar history, not judged",
    );
    expect(document.querySelector(".sr-only")).toHaveTextContent(
      "2 of 4 buckets were read from QRadar history, not judged",
    );
  });

  it("is unchanged for a series with no history bucket", () => {
    chart([bucket(0, { origin: "LIVE" }), bucket(5, { origin: "LIVE" }), bucket(10)]);
    expect(option().series[0].markArea).toBeUndefined();
    expect(tip(0)).not.toContain("history");
    expect(screen.getByRole("list", { name: "Chart key" })).not.toHaveTextContent("history");
    expect(document.querySelector(".sr-only")).toBeNull();
  });
});
