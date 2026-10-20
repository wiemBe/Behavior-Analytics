import { describe, expect, it } from "vitest";

import { CHART_TOKENS } from "@/lib/chartPalette";
import { HOME_TOKENS, type HomePalette } from "@/lib/home/chartPalette";
import { epsChartModel } from "@/lib/home/epsChart";
import { epsPoint, epsSeries } from "@/lib/home/fixtures";
import type { EpsSeriesPoint } from "@/lib/api";

import { buildEpsOption, epsTooltip } from "./chartOptions";
import { epsHistory, epsHistoryLine, epsHistorySummary } from "./epsHistory";

const palette = Object.fromEntries(
  [...Object.keys(CHART_TOKENS), ...Object.keys(HOME_TOKENS)].map((k) => [k, `c-${k}`]),
) as HomePalette;

const step = (i: number, fromHistory: number | undefined, over: Partial<EpsSeriesPoint> = {}) => ({
  ...epsPoint(i, 5),
  ...(fromHistory === undefined ? {} : { cells_from_history: fromHistory }),
  ...over,
});

// 4 complete cells per step (the fixture). Steps 0-1 wholly history, 2 partly, 3 live.
const series = epsSeries([step(0, 4), step(1, 4), step(2, 1), step(3, 0)]);
const model = epsChartModel(series);
const history = epsHistory(series);

interface Opt {
  tooltip: { formatter: (p: unknown) => string };
  series: Array<{ markArea?: { data: Array<unknown[]> } }>;
}

describe("the fleet EPS series: cells read from QRadar history", () => {
  it("says n of m cells in the tooltip of a step with history cells", () => {
    expect(epsTooltip(model, 2, history)).toContain("1 of 4 cells read from QRadar history");
    expect(epsTooltip(model, 0, history)).toContain("4 of 4 cells read from QRadar history");
  });

  it("adds nothing for a step with zero history cells, or with the field absent", () => {
    expect(epsTooltip(model, 3, history)).not.toContain("history");
    const bare = epsSeries([step(0, undefined)]);
    expect(epsHistoryLine(epsHistory(bare), model.steps[0].t)).toBe("");
    expect(epsTooltip(epsChartModel(bare), 0, epsHistory(bare))).not.toContain("history");
  });

  it("shades only the wholly-history steps, as one span", () => {
    expect(history.spans).toHaveLength(1);
    expect(history.spans[0]).toMatchObject({ start: model.steps[0].t, end: model.steps[2].t });
    const o = buildEpsOption(model, palette, false, history) as unknown as Opt;
    expect(o.series[0].markArea!.data).toHaveLength(1);
  });

  it("draws no span for a series without history, and the option is as before", () => {
    const live = epsSeries([step(0, 0), step(1, 0)]);
    const m = epsChartModel(live);
    const withHistory = buildEpsOption(m, palette, false, epsHistory(live)) as unknown as Opt;
    const without = buildEpsOption(m, palette, false) as unknown as Opt;
    expect(withHistory.series).toEqual(without.series);
  });

  it("states it for assistive technology, and not at all without history cells", () => {
    expect(epsHistorySummary(history)).toBe(
      "3 steps hold cells read from QRadar history, not judged. 2 of them are wholly read from QRadar history, not judged and shaded.",
    );
    expect(epsHistorySummary(epsHistory(epsSeries([step(0, 0)])))).toBe("");
  });

  it("puts the words inside the chart only where there is room: not in the tile, yes expanded", () => {
    const first = (h: ReturnType<typeof epsHistory>) =>
      ((buildEpsOption(model, palette, false, h) as unknown as Opt).series[0].markArea!.data[0] as Array<{
        label?: { show: boolean; formatter: string };
      }>)[0];
    expect(first(epsHistory(series, false)).label).toBeUndefined();
    expect(first(epsHistory(series, true)).label).toMatchObject({
      show: true,
      formatter: "read from QRadar history, not judged",
    });
  });
});
