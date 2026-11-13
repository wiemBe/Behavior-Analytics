// The shaded band of an analyst training exclusion on a volume chart (SOC-13d).
//
// It is neutral and token-backed (the same neutral fill the history span uses), told apart from the
// history span by a solid outline, and it carries its own words inside the chart. The words are not
// the only carrier: the chart key and the exclusion text beside the chart say the same, with the
// UTC range, because shading alone cannot be read by everyone.

import type { HistoryShade } from "@/components/behavior/historyOrigin";

export interface ExclusionSpan {
  start: string;
  end: string;
}

/**
 * Union of overlapping or touching `[start, end]` intervals, in epoch ms.
 *
 * Each mark area is painted with the same translucent fill, so two incidents
 * over one stretch of time would stack their alpha and read as a stronger
 * signal there. Merged, every instant is covered by exactly one band.
 */
export function mergeIntervals(
  spans: Array<{ start: number; end: number }>,
): Array<{ start: number; end: number }> {
  const sorted = spans
    .filter((s) => Number.isFinite(s.start) && Number.isFinite(s.end))
    .map((s) => ({ start: Math.min(s.start, s.end), end: Math.max(s.start, s.end) }))
    .sort((a, b) => a.start - b.start);
  const out: Array<{ start: number; end: number }> = [];
  for (const s of sorted) {
    const last = out[out.length - 1];
    if (last && s.start <= last.end) last.end = Math.max(last.end, s.end);
    else out.push({ ...s });
  }
  return out;
}

/** The excluded spans of a chart in epoch ms, overlaps merged so one stretch is shaded once. */
export function mergeExclusions(spans: readonly ExclusionSpan[]): MergedSpan[] {
  return mergeIntervals(spans.map((s) => ({ start: Date.parse(s.start), end: Date.parse(s.end) })));
}

/** The default for a chart with no exclusions: one stable array, so a render does not rebuild the option. */
export const NO_SPANS: ExclusionSpan[] = [];

/** A span in epoch ms, after overlapping ones are merged. */
export interface MergedSpan {
  start: number;
  end: number;
}

export type ExclusionMarkArea = [
  {
    xAxis: number;
    name: string;
    itemStyle: { color: string; borderColor: string; borderWidth: number; borderType: "solid" };
    label: {
      show: boolean;
      position?: "insideTopLeft";
      formatter?: string;
      color?: string;
      fontFamily?: string;
      fontSize?: number;
    };
  },
  { xAxis: number },
];

/** One stretch of time is shaded once (the caller merges overlaps); the widest span is labelled. */
export function exclusionMarkAreas(
  merged: MergedSpan[],
  shade: HistoryShade,
  text: string,
): ExclusionMarkArea[] {
  const widest = merged.reduce(
    (best, s, i) => (s.end - s.start > merged[best].end - merged[best].start ? i : best),
    0,
  );
  return merged.map((m, i): ExclusionMarkArea => [
    {
      xAxis: m.start,
      name: text,
      itemStyle: { color: shade.color, borderColor: shade.border, borderWidth: 1, borderType: "solid" },
      label:
        i === widest
          ? {
              show: true,
              position: "insideTopLeft",
              formatter: text,
              color: shade.text,
              fontFamily: shade.font,
              fontSize: 12,
            }
          : { show: false },
    },
    { xAxis: m.end },
  ]);
}
