// One convention for every time axis in the widgets and on the WAF page (T-098): a day boundary
// reads "15 Oct", any other tick `HH:MM`. The shared formatter is `TIME_AXIS_FORMATTER`; each
// chart must hand it to its time axes, and ECharts must pick its day level at midnight.

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { sourceEpsModel, buildSourceEpsOption } from "@/components/catalog/SourceEpsChart";
import { eps } from "@/components/catalog/subjectFixtures";
import { wafBucket } from "@/components/waf/fixtures";
import { WafRequestChart } from "@/components/waf/WafRequestChart";
import { CHART_TOKENS } from "@/lib/chartPalette";
import { HOME_TOKENS, type HomePalette } from "@/lib/home/chartPalette";
import { epsChartModel } from "@/lib/home/epsChart";
import { epsPoint, epsSeries } from "@/lib/home/fixtures";

import { buildEpsOption, TIME_AXIS_FORMATTER } from "./chartOptions";

const { setOption } = vi.hoisted(() => ({ setOption: vi.fn() }));
vi.mock("echarts/core", async (importActual) => ({
  ...(await importActual<typeof import("echarts/core")>()),
  init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on: vi.fn() })),
  use: vi.fn(),
}));

const palette = Object.fromEntries(
  [...Object.keys(CHART_TOKENS), ...Object.keys(HOME_TOKENS)].map((k) => [k, `c-${k}`]),
) as HomePalette;

interface TimeAxis {
  type: string;
  axisLabel?: { show?: boolean; formatter?: unknown };
}
const timeAxes = (option: unknown): TimeAxis[] => {
  const x = (option as { xAxis: TimeAxis | TimeAxis[] }).xAxis;
  return (Array.isArray(x) ? x : [x]).filter((a) => a.type === "time");
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("the shared time-axis formatter", () => {
  it("labels midnight with the day and month, every other hour HH:MM", async () => {
    const { time } = await vi.importActual<typeof import("echarts/core")>("echarts/core");
    const fmt = (iso: string, level: keyof typeof TIME_AXIS_FORMATTER) =>
      time.format(Date.parse(iso), TIME_AXIS_FORMATTER[level], true);
    expect(fmt("2026-10-15T00:00:00Z", "day")).toBe("15 Oct");
    expect(fmt("2026-10-15T04:00:00Z", "hour")).toBe("04:00");
    expect(fmt("2026-10-15T16:30:00Z", "minute")).toBe("16:30");
  });
});

describe("every time axis uses it", () => {
  it("Fleet EPS", () => {
    const model = epsChartModel(epsSeries([epsPoint(0, 5, 4), epsPoint(1, 6, 4)]));
    const axes = timeAxes(buildEpsOption(model, palette, false));
    expect(axes).toHaveLength(1);
    expect(axes[0].axisLabel?.formatter).toBe(TIME_AXIS_FORMATTER);
  });

  it("the log-source EPS chart (chart.eps_observed_expected)", () => {
    const model = sourceEpsModel([eps(0), eps(5)], null);
    const axes = timeAxes(buildSourceEpsOption(model, palette));
    expect(axes).toHaveLength(1);
    expect(axes[0].axisLabel?.formatter).toBe(TIME_AXIS_FORMATTER);
  });

  it.each([false, true])("the WAF request chart (compact %s)", (compact) => {
    render(<WafRequestChart
        buckets={[wafBucket()]}
        expected={null}
        expectedLow={null}
        expectedHigh={null}
        compact={compact}
      />);
    const axes = timeAxes(setOption.mock.calls[0][0]);
    expect(axes).toHaveLength(compact ? 1 : 2);
    // The upper panel of the full chart hides its labels; the one that shows them must format.
    const shown = axes.filter((a) => a.axisLabel?.show !== false);
    expect(shown).toHaveLength(1);
    expect(shown[0].axisLabel?.formatter).toBe(TIME_AXIS_FORMATTER);
  });
});
