// The series the source page can draw, and the action mix of the range.
//
// The three non-total series are the ones the IPS_SOURCE detectors judge (`ipsDetectors.ts`), so
// they alone have seasonal cells; the metric names mirror `app/ips/inputs.py`. Total events has no
// baseline: the page says so and draws no expected range for it.

import type { IpsBaselineCell, IpsSourceMetricBucket } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { grouped } from "@/lib/figureText";

import { ENGLISH_IPS } from "./ipsI18n";
import type { IpsUnit } from "./ipsDetectors";
import {
  buildIpsSourceSeries,
  buildIpsTotalsSeries,
  ipsSeriesCounts,
  type IpsPoint,
  type SourceSeriesKind,
} from "./ipsSeries";

export const SOURCE_SERIES_KEYS = ["events", "concentration", "fanout", "block_ratio"] as const;
export type SourceSeriesKey = (typeof SOURCE_SERIES_KEYS)[number];

export interface SourceSeriesSpec {
  unit: IpsUnit;
  /** The baseline metric of the seasonal cells, or null when there is none (total events). */
  metric: string | null;
  /** A share's expectation is the pooled sum ratio the detector divides. */
  pooled: boolean;
}

export const SOURCE_SERIES_SPECS: Record<SourceSeriesKey, SourceSeriesSpec> = {
  events: { unit: "events", metric: null, pooled: false },
  concentration: { unit: "share", metric: "top1_source_share", pooled: true },
  fanout: { unit: "ports", metric: "max_distinct_ports", pooled: false },
  block_ratio: { unit: "share", metric: "block_ratio", pooled: true },
};

export function parseSeries(value: string | undefined): SourceSeriesKey {
  return SOURCE_SERIES_KEYS.find((k) => k === value) ?? "events";
}

export function seriesPoints(key: SourceSeriesKey, buckets: IpsSourceMetricBucket[]): IpsPoint[] {
  return key === "events"
    ? buildIpsTotalsSeries(buckets)
    : buildIpsSourceSeries(buckets, key satisfies SourceSeriesKind);
}

/** The chart as prose, for readers who cannot see a canvas. */
export function sourceChartSummary(
  points: IpsPoint[],
  quantity: string,
  subject: string,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  const c = ipsSeriesCounts(points);
  const n = (v: number) => grouped(v, i18n.locale);
  const t = i18n.t;
  const parts = [t("ips.chartSummary.lead", { quantity, subject, count: n(c.stored) })];
  if (c.gaps > 0) parts.push(t("ips.chartSummary.gaps", { count: n(c.gaps) }));
  if (c.notMeasured > 0) parts.push(t("ips.chartSummary.notMeasured", { count: n(c.notMeasured) }));
  if (c.lowerBound > 0) parts.push(t("ips.chartSummary.lowerBounds", { count: n(c.lowerBound) }));
  if (c.maintenance > 0) parts.push(t("ips.chartSummary.maintenance", { count: n(c.maintenance) }));
  return parts.join(" ");
}

export interface ActionMix {
  blocked: number;
  allowed: number;
  unmapped: number;
  /** Buckets that carried a complete split, and the stored buckets of the range. */
  measured: number;
  stored: number;
  /** True when some counted bucket measured the action search only as a lower bound. */
  lowerBound: boolean;
}

/**
 * The range's action sums, or null when no bucket carried a split. A bucket counts only when all
 * three parts are present and its ACTION search was EXACT or LOWER_BOUND; a bucket with any part
 * missing is left out, never read as a zero.
 */
export function actionMix(buckets: IpsSourceMetricBucket[]): ActionMix | null {
  let blocked = 0;
  let allowed = 0;
  let unmapped = 0;
  let measured = 0;
  let lowerBound = false;
  for (const b of buckets) {
    if (b.blocked_events == null || b.allowed_events == null || b.unmapped_events == null) continue;
    const m = b.dimensions.find((d) => d.dimension === "ACTION")?.measurement;
    if (m !== "EXACT" && m !== "LOWER_BOUND") continue;
    blocked += b.blocked_events;
    allowed += b.allowed_events;
    unmapped += b.unmapped_events;
    measured += 1;
    if (m === "LOWER_BOUND") lowerBound = true;
  }
  return measured === 0 ? null : { blocked, allowed, unmapped, measured, stored: buckets.length, lowerBound };
}

/** Whether the seasonal cells hold a reliable one for the series' metric. */
export function hasReliableCell(cells: IpsBaselineCell[], metric: string): boolean {
  return cells.some((c) => c.metric_name === metric && c.is_reliable);
}
