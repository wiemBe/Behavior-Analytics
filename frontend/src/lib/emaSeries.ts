// The EMA overlay's shared drawing pieces, for every time-series chart that offers it (display
// only, see `ema.ts`): the lines for the enabled periods, their ECharts series, and the tooltip
// rows. Pure; the colours arrive as an argument, read from the tokens by the caller.

import type { ChartPalette } from "./chartPalette";
import { ema, type EmaPeriod } from "./ema";

/** One enabled average: its period and its value per bucket, aligned with the chart's slots. */
export interface EmaLine {
  period: EmaPeriod;
  values: Array<number | null>;
}

/** The colour of each period, from the tokens: one categorical token per period, never a status hue. */
export type EmaColours = Record<EmaPeriod, string>;

export function emaColours(c: Pick<ChartPalette, "ema3" | "ema5" | "ema10" | "ema20">): EmaColours {
  return { 3: c.ema3, 5: c.ema5, 10: c.ema10, 20: c.ema20 };
}

/** The enabled averages of `observed` (null where the bucket was not fully observed). */
export function emaLinesOf(
  observed: ReadonlyArray<number | null>,
  periods: ReadonlyArray<EmaPeriod>,
): EmaLine[] {
  return periods.map((period) => ({ period, values: ema(observed, period) }));
}

/** Whether the line draws anything at all (a run of `period` consecutive observed buckets). */
export function emaDrawn(line: EmaLine): boolean {
  return line.values.some((v) => v != null);
}

/**
 * The ECharts series of the enabled averages: one thin solid line each, no markers, a gap wherever
 * the observation stops, under the observed line (z 3). `axes` places them on a panel of a
 * multi-grid chart. `scale` converts the values to the axis unit (a share drawn in %).
 */
export function emaSeries(
  lines: ReadonlyArray<EmaLine>,
  slots: ReadonlyArray<number>,
  colours: EmaColours,
  scale = 1,
  axes: { xAxisIndex?: number; yAxisIndex?: number } = {},
) {
  return lines.map((line) => ({
    type: "line" as const,
    name: `EMA ${line.period}`,
    ...axes,
    data: slots.map((t, i) => {
      const v = line.values[i];
      return [t, v == null ? null : v * scale] as [number, number | null];
    }),
    connectNulls: false,
    showSymbol: false,
    silent: true,
    lineStyle: { color: colours[line.period], width: 1 },
    itemStyle: { color: colours[line.period] },
    z: 2,
  }));
}

/** The tooltip rows of bucket `index`: only the averages that have a value there. */
export function emaTooltipRows(
  lines: ReadonlyArray<EmaLine>,
  index: number,
  row: (period: EmaPeriod, value: number) => string,
): string[] {
  return lines.flatMap((line) => {
    const v = line.values[index];
    return v == null ? [] : [row(line.period, v)];
  });
}
