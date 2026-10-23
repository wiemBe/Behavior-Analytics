import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ipsCell, ipsEntityBucket } from "./fixtures";
import { IpsSeriesChart } from "./IpsSeriesChart";
import { buildIpsEntitySeries } from "./ipsSeries";

const { setOption, dispose, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return { setOption, resize, dispose, init: vi.fn(() => ({ setOption, resize, dispose })), use: vi.fn() };
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

interface Series {
  name: string;
  data: Array<[number, number | null]>;
  connectNulls?: boolean;
  showSymbol?: boolean;
  stack?: string;
  markArea?: { data: unknown[] };
}
interface Option {
  useUTC: boolean;
  tooltip: { formatter: (params: unknown) => string };
  xAxis: { name?: string };
  yAxis: { name?: string };
  series: Series[];
}
const option = () => setOption.mock.calls[0][0] as Option;
const series = (name: string) => option().series.find((s) => s.name === name)!;
const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

function chart(
  rows = [ipsEntityBucket()],
  over: Partial<Parameters<typeof IpsSeriesChart>[0]> = {},
) {
  return render(
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
      ariaLabel="Events against the expected range"
      {...over}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IpsSeriesChart", () => {
  it("draws no chart for an empty series", () => {
    const { container } = chart([], { rows: [] });
    expect(init).not.toHaveBeenCalled();
    expect(container.querySelector(".chart")).toBeNull();
  });

  it("plots a measured zero as a value and a null as null, never connecting across it", () => {
    chart([
      ipsEntityBucket({ bucket_start: T(0), events: 0 }),
      ipsEntityBucket({ bucket_start: T(5), events: 9, measurement: null }),
      ipsEntityBucket({ bucket_start: T(10), events: 3 }),
    ]);
    expect(series("Events").data.map((d) => d[1])).toEqual([0, null, 3]);
    expect(series("Events").connectNulls).toBe(false);
  });

  it("shades a never-stored interval and keys it", () => {
    chart([ipsEntityBucket({ bucket_start: T(0) }), ipsEntityBucket({ bucket_start: T(25) })]);
    expect(series("Events").data.map((d) => d[1])).toEqual([120, null, 120]);
    expect(series("Events").markArea?.data.length).toBeGreaterThan(0);
    expect(screen.getByText("Not stored, not measured or not exact")).toBeInTheDocument();
  });

  it("draws the expected range as one stacked band plus one dashed line, not as three lines", () => {
    chart();
    const low = series("Expected low");
    const band = series("Expected range at detection");
    expect(low.stack).toBe("expected-range");
    expect(band.stack).toBe("expected-range");
    expect(band.data[0][1]).toBe(140);
    expect(series("Expected at detection").data[0][1]).toBe(120);
  });

  it("draws no band and no expected line when none was recorded, never a zero one", () => {
    chart(undefined, { expected: null, expectedLow: null, expectedHigh: null });
    expect(series("Expected range at detection").data).toEqual([]);
    expect(series("Expected low").data).toEqual([]);
    expect(series("Expected at detection").data).toEqual([]);
    expect(screen.queryByText("Expected range at detection")).toBeNull();
    expect(screen.queryByText("Expected at detection")).toBeNull();
  });

  it("follows each bucket's seasonal cell, and leaves a bucket with no reliable cell without a band", () => {
    const rows = [
      ipsEntityBucket({ bucket_start: "2026-10-12T09:55:00Z" }),
      ipsEntityBucket({ bucket_start: "2026-10-12T10:00:00Z" }),
    ];
    chart(rows, {
      baseline: {
        metric: "entity_events",
        pooled: false,
        cells: [ipsCell({ hour: 9 }), ipsCell({ hour: 10, is_reliable: false })],
      },
    });
    const band = series("Expected range (seasonal p05–p95)");
    expect(band.data.map((d) => d[1])).toEqual([140, null]);
    expect(series("Expected (seasonal cell)").data.map((d) => d[1])).toEqual([120, null]);
  });

  it("plots a share in percent and names the unit on the axis", () => {
    const rows = [ipsEntityBucket({ bucket_start: T(0), events: 0.25 })];
    chart(rows, { unit: "share", quantity: "Largest source IP's share of events", expected: 0.2, expectedLow: 0.1, expectedHigh: 0.3 });
    expect(series("Largest source IP's share of events").data[0][1]).toBe(25);
    expect(option().yAxis.name).toBe("Largest source IP's share of events (%)");
    expect(screen.getByText(/not 0 %/)).toBeInTheDocument();
  });

  it("spells a share in the tooltip with the shared formatter", () => {
    const rows = [ipsEntityBucket({ bucket_start: T(0), events: 0.25 })];
    chart(rows, { unit: "share", quantity: "Largest source IP's share of events", expected: 0.2, expectedLow: 0.1, expectedHigh: 0.3 });
    const html = option().tooltip.formatter([{ dataIndex: 0 }]);
    expect(html).toContain("25.0\u00A0%");
    expect(html).toContain("20.0\u00A0%");
    expect(html).toContain("10.0\u00A0% – 30.0\u00A0%");
    expect(html).not.toMatch(/\d%/);
  });

  it("names the unit on both axes, plots in UTC, and puts no marker on every point", () => {
    chart();
    expect(option().yAxis.name).toBe("Events (events / 5 min)");
    expect(option().xAxis.name).toBe("Time (UTC)");
    expect(option().useUTC).toBe(true);
    expect(series("Events").showSymbol).toBe(false);
  });

  it("states lower bounds and maintenance in the key", () => {
    chart([ipsEntityBucket({ measurement: "LOWER_BOUND", in_maintenance: true })]);
    expect(screen.getByText(/lower bounds/)).toBeInTheDocument();
    expect(screen.getByText("Maintenance")).toBeInTheDocument();
  });

  it("exposes a named image and a prose summary", () => {
    chart(undefined, { textSummary: "Events across 1 stored buckets." });
    expect(screen.getByRole("img", { name: "Events against the expected range" })).toBeInTheDocument();
    expect(screen.getByText("Events across 1 stored buckets.")).toBeInTheDocument();
  });

  it("disposes the chart on unmount", () => {
    const view = chart();
    view.unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});
