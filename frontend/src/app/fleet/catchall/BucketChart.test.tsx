import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BucketChart } from "./BucketChart";
import { buildSlots } from "./catchall";
import { bucket } from "./fixtures";

const { setOption, init, use, on } = vi.hoisted(() => {
  const setOption = vi.fn();
  const on = vi.fn();
  return {
    setOption,
    on,
    use: vi.fn(),
    init: vi.fn(() => ({
      setOption,
      resize: vi.fn(),
      dispose: vi.fn(),
      getZr: () => ({ on }),
      containPixel: () => true,
      convertFromPixel: () => 1,
    })),
  };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const T = (m: number) => Date.UTC(2026, 9, 13, 10, m);

function option() {
  return setOption.mock.calls.at(-1)?.[0];
}

beforeEach(() => {
  setOption.mockClear();
});

describe("BucketChart", () => {
  it("passes a gap as null, never 0, and shades exactly the gap buckets", () => {
    const s = buildSlots(
      [bucket(0), bucket(15, { measurement: "LOWER_BOUND", events: 900 })],
      T(0),
      T(30),
    );
    render(
      <BucketChart slots={s.slots} bucketSeconds={300} selected={0} onSelect={vi.fn()} ariaLabel="c" textSummary="t" />,
    );
    const [shade, bars] = option().series;
    expect(bars.data[0]).toBe(100);
    expect(bars.data[1]).toBeNull();
    expect(bars.data[2]).toBeNull();
    expect(bars.data.includes(0)).toBe(false);
    expect(bars.data[3]).toMatchObject({ value: 900 });
    // The shaded ranges the chart receives: the two unmeasured buckets, at the
    // full axis height, and nothing else (not the measured ones, not the tail).
    const max = option().yAxis.max;
    expect(shade.data).toEqual([null, max, max, null, null, null]);
    expect(shade.name).toBe("Not measured");
  });

  it("shades a one-bucket gap as a bar of its own, as visible as a long one", () => {
    const s = buildSlots([bucket(0), bucket(10)], T(0), T(15));
    render(<BucketChart slots={s.slots} bucketSeconds={300} selected={null} onSelect={vi.fn()} ariaLabel="c" textSummary="t" />);
    const shade = option().series[0];
    expect(shade.data.filter((v: number | null) => v !== null)).toHaveLength(1);
    expect(shade.barCategoryGap).toBe(0);
    expect(shade.itemStyle.borderWidth).toBeGreaterThan(0);
  });

  it("has one value axis only", () => {
    const s = buildSlots([bucket(0)], T(0), T(5));
    render(<BucketChart slots={s.slots} bucketSeconds={300} selected={null} onSelect={vi.fn()} ariaLabel="c" textSummary="t" />);
    expect(Array.isArray(option().yAxis)).toBe(false);
    expect(option().series.every((x: { type: string }) => x.type === "bar")).toBe(true);
  });

  it("names the unit and UTC on the axes", () => {
    const s = buildSlots([bucket(0)], T(0), T(5));
    render(<BucketChart slots={s.slots} bucketSeconds={300} selected={null} onSelect={vi.fn()} ariaLabel="c" textSummary="t" />);
    expect(option().yAxis.name).toContain("events / 5 min");
    expect(option().xAxis.name).toContain("UTC");
    expect(option().series[1].markLine).toBeUndefined();
  });

  it("draws no bar and no shade after the latest measured bucket", () => {
    const s = buildSlots([bucket(0)], T(0), T(20));
    render(<BucketChart slots={s.slots} bucketSeconds={300} selected={0} onSelect={vi.fn()} ariaLabel="c" textSummary="t" />);
    expect(option().series[1].data).toEqual([100, null, null, null]);
    expect(option().series[0].data).toEqual([null, null, null, null]);
  });

  it("exposes the chart as a named image with a text summary", () => {
    const s = buildSlots([bucket(0)], T(0), T(5));
    render(<BucketChart slots={s.slots} bucketSeconds={300} selected={0} onSelect={vi.fn()} ariaLabel="Events per 5 min bucket" textSummary="2 buckets were not measured" />);
    expect(screen.getByRole("img", { name: "Events per 5 min bucket" })).toBeInTheDocument();
    expect(screen.getByText("2 buckets were not measured")).toBeInTheDocument();
  });
});
