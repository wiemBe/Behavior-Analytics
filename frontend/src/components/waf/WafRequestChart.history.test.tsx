import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { wafBucket } from "./fixtures";
import { WafRequestChart } from "./WafRequestChart";

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
const series = (name: string) => option().series.find((s) => s.name === name)!;
const tip = (i: number) => option().tooltip.formatter({ dataIndex: i });
const T = (m: number) => `2026-10-12T09:${String(m).padStart(2, "0")}:00Z`;

function chart(buckets: ReturnType<typeof wafBucket>[], extra: { compact?: boolean; historyLabel?: boolean } = {}) {
  return render(
    <WafRequestChart buckets={buckets} expected={41.5} expectedLow={22} expectedHigh={68} {...extra} />,
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

describe("WafRequestChart: buckets read from QRadar history", () => {
  it("shades the history span on both panels, labels it, and keys it", () => {
    chart([
      wafBucket({ bucket_start: T(0), origin: "HISTORY" }),
      wafBucket({ bucket_start: T(5), origin: "HISTORY" }),
      wafBucket({ bucket_start: T(10), origin: "LIVE" }),
    ]);
    for (const name of ["Requests", "Endpoint coverage"]) {
      const data = series(name).markArea!.data;
      expect(data).toHaveLength(1);
      expect(data[0][0].name).toBe("read from QRadar history, not judged");
    }
    expect(tip(0)).toContain("Read from QRadar history, not judged");
    expect(tip(2)).not.toContain("history");
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent(
      "Read from QRadar history, not judged",
    );
    expect(document.body).toHaveTextContent("2 of 3 buckets were read from QRadar history");
  });

  it("renders a null origin as unknown: not shaded, never live", () => {
    chart([wafBucket({ bucket_start: T(0), origin: null }), wafBucket({ bucket_start: T(5), origin: "LIVE" })]);
    expect(series("Requests").markArea).toBeUndefined();
    expect(tip(0)).toContain("origin unknown");
    expect(tip(1)).not.toContain("origin unknown");
    expect(document.body).toHaveTextContent("1 bucket has an origin unknown");
  });

  it("is unchanged for a series with no history bucket", () => {
    chart([wafBucket({ bucket_start: T(0), origin: "LIVE" }), wafBucket({ bucket_start: T(5) })]);
    expect(series("Requests").markArea).toBeUndefined();
    expect(tip(0)).not.toContain("history");
    expect(screen.getByRole("list", { name: "Chart key" })).not.toHaveTextContent("history");
  });

  it("keeps the words inside the full-size chart and leaves them out of the tile", () => {
    const rows = [wafBucket({ bucket_start: T(0), origin: "HISTORY" }), wafBucket({ bucket_start: T(5) })];
    const label = () =>
      (series("Requests").markArea!.data[0][0] as { label?: { show: boolean } }).label;
    chart(rows);
    expect(label()).toMatchObject({ show: true });
    vi.clearAllMocks();
    chart(rows, { compact: true });
    expect(label()).toBeUndefined();
    expect(screen.getAllByRole("list", { name: "Chart key" }).at(-1)).toHaveTextContent("history");
    vi.clearAllMocks();
    chart(rows, { compact: true, historyLabel: true });
    expect(label()).toMatchObject({ show: true });
  });
});
