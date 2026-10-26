// The catch-all bucket chart in Turkish: axis names with their unit, the tooltip of each kind of
// bucket, and the numbers in the Turkish form. English is pinned by BucketChart.test.tsx.

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../../vitest.setup";

import { BucketChart } from "./BucketChart";
import { buildSlots } from "./catchall";
import { bucket } from "./fixtures";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({
      setOption,
      resize: vi.fn(),
      dispose: vi.fn(),
      getZr: () => ({ on: vi.fn() }),
      containPixel: () => true,
      convertFromPixel: () => 1,
    })),
  };
});
vi.mock("echarts/core", () => ({ init, use: vi.fn() }));
vi.mock("echarts/charts", () => ({ BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const T = (m: number) => Date.UTC(2026, 9, 13, 10, m);
const option = () => setOption.mock.calls.at(-1)?.[0];
const NB = " ";

beforeEach(() => {
  setOption.mockClear();
  setTestLocale("tr");
});

function draw() {
  const s = buildSlots(
    [bucket(0, { events: 1_204_000, distinct_senders: 1204 }), bucket(15, { measurement: "LOWER_BOUND", events: 900, distinct_senders_lower_bound: true })],
    T(0),
    T(30),
  );
  render(<BucketChart slots={s.slots} bucketSeconds={300} selected={null} onSelect={vi.fn()} ariaLabel="c" textSummary="t" />);
  return (i: number) => option().tooltip.formatter({ dataIndex: i }) as string;
}

describe("BucketChart in Turkish", () => {
  it("names the unit and UTC on both axes", () => {
    draw();
    expect(option().yAxis.name).toBe(`Hacim (olay / 5${NB}dk)`);
    expect(option().xAxis.name).toBe("Zaman diliminin başlangıcı (UTC)");
  });

  it("words an exact bucket with the Turkish number form", () => {
    const tip = draw();
    expect(tip(0)).toContain(`Olaylar: 1.204.000 olay / 5${NB}dk`);
    expect(tip(0)).toContain("Farklı gönderenler: 1.204");
    expect(tip(0)).not.toContain("Kırpılmış");
  });

  it("flags a lower bound as at least, with the truncation note", () => {
    const tip = draw();
    expect(tip(3)).toContain(`Olaylar: en az 900 olay / 5${NB}dk`);
    expect(tip(3)).toContain("Farklı gönderenler: en az 4");
    expect(tip(3)).toContain("Kırpılmış arama: daha fazla olay olabilir");
  });

  it("says a gap was not measured and the tail was not collected yet, never a figure", () => {
    const tip = draw();
    expect(tip(1)).toContain("Ölçülmedi (bu zaman dilimi için kayıt saklanmadı)");
    expect(tip(1)).not.toMatch(/Olaylar:/);
    expect(tip(5)).toContain("Henüz toplanmadı (ölçülen son zaman diliminden sonra)");
  });

  it("keeps the English tooltips when the session is English", () => {
    setTestLocale("en");
    const tip = draw();
    expect(tip(3)).toContain("Events: at least 900 events / 5 min");
    expect(tip(1)).toContain("Not measured (no row was stored for this bucket)");
    expect(option().yAxis.name).toBe("Volume (events / 5 min)");
  });
});
