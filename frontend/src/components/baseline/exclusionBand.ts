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
