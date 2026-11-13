// SOC-13f: the band of an analyst training exclusion on the WAF, IPS and property-watch charts.
// Neutral, outlined solid, labelled inside the chart, named in the key, merged where it overlaps,
// and absent (not an empty key entry) when there is none.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { setOption, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })),
    use: vi.fn(),
  };
});
vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

import { buildIpsEntitySeries } from "@/components/ips/ipsSeries";
import { ipsEntityBucket } from "@/components/ips/fixtures";
import { IpsSeriesChart } from "@/components/ips/IpsSeriesChart";
import { iso, point, series } from "@/components/propertywatch/fixtures";
import { PropertyWatchSeriesChart } from "@/components/propertywatch/PropertyWatchSeriesChart";
import { wafBucket } from "@/components/waf/fixtures";
import { WafRequestChart } from "@/components/waf/WafRequestChart";

interface Area {
  name?: string;
  xAxis: number;
  itemStyle?: { borderType?: string };
  label?: { show: boolean; formatter?: string };
}
interface Option {
  series: Array<{ name: string; markArea?: { data: Array<[Area, { xAxis: number }]> } }>;
}
const option = () => setOption.mock.calls[0][0] as Option;
const excludedAreas = () =>
  option()
    .series.flatMap((s) => s.markArea?.data ?? [])
    .filter(([from]) => from.name === "Excluded from baseline training");

const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;
const span = (a: number, b: number) => ({ start: T(a), end: T(b) });
const rows = [0, 5, 10, 15, 20].map((m) => ipsEntityBucket({ bucket_start: T(m) }));
const wafRows = [0, 5, 10, 15, 20].map((m) => wafBucket({ bucket_start: T(m) }));

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.ResizeObserver = class {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  } as unknown as typeof ResizeObserver;
});

const WAF = (exclusions?: Array<{ start: string; end: string }>) =>
  render(
    <WafRequestChart buckets={wafRows} expected={41} expectedLow={20} expectedHigh={60} exclusions={exclusions} />,
  );
const IPS = (exclusions?: Array<{ start: string; end: string }>) =>
  render(
    <IpsSeriesChart
      points={buildIpsEntitySeries(rows)}
      rows={rows}
      unit="events"
      quantity="Events"
      bucketSeconds={300}
      baseline={null}
      expected={120}
      expectedLow={70}
      expectedHigh={210}
      ariaLabel="Events"
      exclusions={exclusions}
    />,
  );
const PW = (exclusions?: Array<{ start: string; end: string }>) =>
  render(
    <PropertyWatchSeriesChart
      series={[series("a", "alice", [point(0), point(1), point(3)])]}
      windowStart={iso(0)}
      windowEnd={iso(4)}
      bucketSeconds={300}
      exclusions={exclusions}
    />,
  );

describe.each([
  ["WAF request chart", WAF, "Chart key"],
  ["IPS series chart", IPS, "Chart key"],
  ["property-watch chart", PW, null],
] as const)("%s", (_name, draw, _key) => {
  it("shades an excluded interval as one labelled, solid-outlined area", () => {
    draw([span(5, 15)]);
    const areas = excludedAreas();
    expect(areas).toHaveLength(1);
    const [from, to] = areas[0];
    expect(from.xAxis).toBe(Date.parse(T(5)));
    expect(to.xAxis).toBe(Date.parse(T(15)));
    expect(from.itemStyle?.borderType).toBe("solid");
    expect(from.label).toMatchObject({ show: true, formatter: "Excluded from baseline training" });
  });

  it("merges overlapping intervals so one stretch is shaded once", () => {
    draw([span(0, 10), span(5, 15)]);
    expect(excludedAreas()).toHaveLength(1);
    expect(excludedAreas()[0][1].xAxis).toBe(Date.parse(T(15)));
  });

  it("names the band in the key, only when one was drawn", () => {
    const { unmount } = draw([span(5, 15)]);
    expect(screen.getByRole("list")).toHaveTextContent("Excluded from baseline training");
    unmount();
    setOption.mockClear();
    draw();
    expect(excludedAreas()).toHaveLength(0);
    expect(screen.getByRole("list")).not.toHaveTextContent("Excluded from baseline training");
  });
});
