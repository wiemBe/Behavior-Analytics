import { describe, expect, it } from "vitest";

import { CHART_TOKENS } from "@/lib/chartPalette";
import { HOME_TOKENS, type HomePalette } from "@/lib/home/chartPalette";
import type { MetricBucket } from "@/lib/api";

import { buildSourceEpsOption, sourceEpsModel } from "./SourceEpsChart";

const palette = Object.fromEntries(
  [...Object.keys(CHART_TOKENS), ...Object.keys(HOME_TOKENS)].map((k) => [k, `c-${k}`]),
) as HomePalette;

const bucket = (m: number, over: Partial<MetricBucket> = {}): MetricBucket => ({
  bucket_start: new Date(Date.UTC(2026, 9, 12, 9, m)).toISOString(),
  bucket_seconds: 300,
  event_count: 600,
  average_eps: 2,
  peak_eps: null,
  completeness: "COMPLETE",
  last_event_at: null,
  ...over,
});

interface Option {
  tooltip: { formatter: (p: unknown) => string };
  series: Array<{ name: string; markArea?: { data: Array<[{ name: string }, unknown]> } }>;
}
const build = (buckets: MetricBucket[]) =>
  buildSourceEpsOption(sourceEpsModel(buckets, null), palette) as unknown as Option;

describe("the source EPS widget: buckets read from QRadar history", () => {
  it("shades the contiguous history span, labelled, and says so in the tooltip", () => {
    const o = build([
      bucket(0, { origin: "HISTORY" }),
      bucket(5, { origin: "HISTORY" }),
      bucket(10, { origin: "LIVE" }),
    ]);
    const data = o.series[0].markArea!.data;
    expect(data).toHaveLength(1);
    expect(data[0][0].name).toBe("read from QRadar history, not judged");
    expect(o.tooltip.formatter({ dataIndex: 0 })).toContain("Read from QRadar history, not judged");
    expect(o.tooltip.formatter({ dataIndex: 2 })).not.toContain("history");
  });

  it("keeps a null origin unknown: not shaded, said so in the tooltip", () => {
    const o = build([bucket(0, { origin: null as never })]);
    expect(o.series[0].markArea).toBeUndefined();
    expect(o.tooltip.formatter({ dataIndex: 0 })).toContain("origin unknown");
  });

  it("is unchanged for a series with no history bucket", () => {
    const o = build([bucket(0, { origin: "LIVE" }), bucket(5)]);
    expect(o.series[0].markArea).toBeUndefined();
    expect(o.tooltip.formatter({ dataIndex: 0 })).not.toContain("history");
  });

  it("labels the span inside the chart only when asked (the expanded widget), not in the tile", () => {
    const rows = [bucket(0, { origin: "HISTORY" }), bucket(5)];
    const label = (labelled: boolean) =>
      (
        buildSourceEpsOption(sourceEpsModel(rows, null, labelled), palette) as unknown as {
          series: Array<{ markArea: { data: Array<[{ label?: unknown }]> } }>;
        }
      ).series[0].markArea.data[0][0].label;
    expect(label(false)).toBeUndefined();
    expect(label(true)).toMatchObject({ show: true });
  });
});
