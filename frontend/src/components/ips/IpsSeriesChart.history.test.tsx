import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ipsEntityBucket } from "./fixtures";
import { IpsSeriesChart } from "./IpsSeriesChart";
import { buildIpsEntitySeries } from "./ipsSeries";

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

interface Option {
  tooltip: { formatter: (p: unknown) => string };
  series: Array<{ name: string; markArea?: { data: Array<[{ name: string }, unknown]> } }>;
}
const option = () => setOption.mock.calls[0][0] as Option;
const events = () => option().series.find((s) => s.name === "Events")!;
const tip = (i: number) => option().tooltip.formatter({ dataIndex: i });
const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

function chart(rows: ReturnType<typeof ipsEntityBucket>[]) {
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
    />,
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

describe("IpsSeriesChart: buckets read from QRadar history", () => {
  it("shades, labels and keys the history span, and says so in the tooltip", () => {
    chart([
      ipsEntityBucket({ bucket_start: T(0), origin: "HISTORY" }),
      ipsEntityBucket({ bucket_start: T(5), origin: "HISTORY" }),
      ipsEntityBucket({ bucket_start: T(10), origin: "LIVE" }),
    ]);
    const data = events().markArea!.data;
    expect(data).toHaveLength(1);
    expect(data[0][0].name).toBe("read from QRadar history, not judged");
    expect(tip(1)).toContain("Read from QRadar history, not judged");
    expect(tip(2)).not.toContain("history");
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent(
      "Read from QRadar history, not judged",
    );
    expect(document.body).toHaveTextContent("2 of 3 buckets were read from QRadar history");
  });

  it("renders a null origin as unknown, not shaded and not live", () => {
    chart([ipsEntityBucket({ bucket_start: T(0), origin: null })]);
    expect(events().markArea).toBeUndefined();
    expect(tip(0)).toContain("origin unknown");
    expect(document.body).toHaveTextContent("1 bucket has an origin unknown");
  });

  it("is unchanged for a series with no history bucket", () => {
    chart([ipsEntityBucket({ bucket_start: T(0), origin: "LIVE" }), ipsEntityBucket({ bucket_start: T(5) })]);
    expect(events().markArea).toBeUndefined();
    expect(tip(0)).not.toContain("history");
    expect(screen.getByRole("list", { name: "Chart key" })).not.toHaveTextContent("history");
  });
});
