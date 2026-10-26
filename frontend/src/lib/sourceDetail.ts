// Derived semantics for the source behavior page.
//
// Three judgements the API does not make for us — how far back to look, how
// trustworthy the baseline is, and whether collection is keeping up — made
// here as pure functions so they are testable and so the page contains no
// reasoning of its own.
//
// The constraint that governs all three is the one that governs Phase A: a
// value that was never measured must never be presented as a measurement, and
// a collection problem must never be presented as source behavior.

import type { AnomalySummary, BaselineCell, MetricBucket, SourceBehavior } from "@/lib/api";
import { formatMetric } from "@/lib/behavior";
import { ENGLISH_BEHAVIOR, plainCount, type BehaviorI18n } from "@/lib/behaviorI18n";
import { formatShare } from "@/lib/format";

// --- time range ------------------------------------------------------------

/**
 * Selectable windows.
 *
 * All four are safe against the metrics endpoint, which caps `limit` at 5000:
 * 24 hours of 60-second buckets is 1440 rows, well inside the cap. Nothing
 * here issues an unbounded request — `since` is always computed and sent.
 */
export const RANGE_HOURS = [1, 6, 12, 24] as const;
export type RangeHours = (typeof RANGE_HOURS)[number];

export const DEFAULT_RANGE: RangeHours = 6;

/** The row cap sent with a metric request. Bounded, never omitted. */
export const METRIC_LIMIT = 2000;

export function parseRange(value: string | undefined): RangeHours {
  const n = Number.parseInt(value ?? "", 10);
  return (RANGE_HOURS as readonly number[]).includes(n)
    ? (n as RangeHours)
    : DEFAULT_RANGE;
}

export function rangeLabel(hours: RangeHours, i18n: BehaviorI18n = ENGLISH_BEHAVIOR): string {
  return i18n.t("behavior.range.label", { count: hours });
}

/**
 * The source page's nearest range for a dashboard window.
 *
 * 1 h and 24 h exist as ranges. 7 d does not: a week of 60-second buckets is
 * 10080 rows, past the metrics cap of 5000, so the link is the longest range
 * (24 h) and `from=7d` asks the page to say so.
 */
export function sourceDrillHref(sourceId: string, window: "1h" | "24h" | "7d"): string {
  if (window === "1h") return `/behavior/sources/${sourceId}?range=1`;
  if (window === "24h") return `/behavior/sources/${sourceId}?range=24`;
  return `/behavior/sources/${sourceId}?range=24&from=7d`;
}

export const SOURCE_7D_NOTE = ENGLISH_BEHAVIOR.t("behavior.source.note7d");

/**
 * A seasonal cell as an analyst reads it: `Mon 12:00 UTC`.
 *
 * The backend keys cells by ISO weekday (1 = Monday) and the UTC hour. A bare
 * "Weekday 7, hour 12" asks the reader to know both conventions.
 */
export function seasonalCellLabel(
  weekday: number,
  hour: number,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): string {
  const key = `behavior.weekday.${weekday}`;
  const day = Number.isInteger(weekday) && i18n.t.has?.(key) ? i18n.t(key) : null;
  if (!day || !Number.isInteger(hour) || hour < 0 || hour > 23) {
    return i18n.t("behavior.weekday.unknown", { weekday, hour });
  }
  return `${day} ${String(hour).padStart(2, "0")}:00 UTC`;
}

// --- baseline quality ------------------------------------------------------

export type BaselineStatus =
  | "RELIABLE"
  | "STILL_LEARNING"
  | "DEGENERATE"
  | "INCOMPLETE";

export interface BaselineQuality {
  status: BaselineStatus;
  label: string;
  tone: "ok" | "warn" | "";
  /** One sentence an analyst can act on. */
  explanation: string;
  /** The seasonal cell this source is currently judged against, if identifiable. */
  cell: BaselineCell | null;
}

/**
 * The baseline cell the current expectation came from.
 *
 * Identified by matching the expected value against each cell's median rather
 * than by computing the current weekday and hour in the browser. The backend
 * owns the seasonal-cell convention, and re-deriving it here from a local
 * clock would silently disagree with it across timezones and week boundaries.
 */
export function currentCell(
  cells: BaselineCell[],
  expectedEps: number | null | undefined,
): BaselineCell | null {
  if (expectedEps == null) return null;
  const match = cells.find((c) => Math.abs(c.median - expectedEps) < 1e-6);
  return match ?? null;
}

export function baselineQuality(
  source: SourceBehavior,
  cells: BaselineCell[],
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): BaselineQuality {
  const cell = currentCell(cells, source.expected_eps);
  const samples = source.baseline_sample_count;
  const label = (status: BaselineStatus) => i18n.t(`behavior.baseline.label.${status}`);
  const shown = (n: number) => ({ count: n, shown: plainCount(n, i18n.locale) });

  // No expectation at all: the detector has declined to judge this source, and
  // nothing about the cells below changes that.
  if (source.state === "INSUFFICIENT_DATA" || source.expected_eps == null) {
    return {
      status: "STILL_LEARNING",
      label: label("STILL_LEARNING"),
      tone: "",
      explanation:
        samples > 0
          ? i18n.t("behavior.baseline.explanation.learningSome", shown(samples))
          : i18n.t("behavior.baseline.explanation.learningNone"),
      cell,
    };
  }

  // MAD of zero is a property of a steady low-volume source, not a fault, and
  // is stated as such: the verdict still rests on the deterministic bound.
  if (cell && cell.mad === 0) {
    return {
      status: "DEGENERATE",
      label: label("DEGENERATE"),
      tone: "warn",
      explanation: i18n.t("behavior.baseline.explanation.degenerate", {
        samples: plainCount(cell.sample_count, i18n.locale),
      }),
      cell,
    };
  }

  if (cell && !cell.is_reliable) {
    return {
      status: "INCOMPLETE",
      label: label("INCOMPLETE"),
      tone: "warn",
      explanation: i18n.t("behavior.baseline.explanation.incomplete", {
        ...shown(cell.sample_count),
        completeness: formatShare(cell.completeness, i18n.locale),
      }),
      cell,
    };
  }

  return {
    status: "RELIABLE",
    label: label("RELIABLE"),
    tone: "ok",
    explanation: cell
      ? i18n.t("behavior.baseline.explanation.reliableCell", shown(cell.sample_count))
      : i18n.t("behavior.baseline.explanation.reliableSeasonal", shown(samples)),
    cell,
  };
}

// --- collection health -----------------------------------------------------

export type CollectionStatus = "CURRENT" | "DELAYED" | "PARTIAL" | "NONE";

export interface CollectionHealth {
  status: CollectionStatus;
  label: string;
  tone: "ok" | "warn" | "crit" | "";
  explanation: string;
  /** Buckets present in the window but not fully observed. */
  incomplete: number;
  /** Buckets that exist for the window. */
  total: number;
  /** Buckets the window should contain at this bucket width. */
  expected: number;
  /** Intervals with no stored bucket at all. */
  absent: number;
  /** Age of the newest collected bucket, in seconds. Null when none exists. */
  lagSeconds: number | null;
}

/** Beyond this many bucket widths behind, collection is called delayed. */
const DELAY_FACTOR = 3;

/**
 * How healthy collection is, from the buckets themselves.
 *
 * Deliberately says nothing about whether the *source* is quiet. A collector
 * that has stopped and a source that has stopped produce the same empty chart,
 * and conflating them is the failure this whole section exists to prevent.
 *
 * There is no FAILED status: the API exposes no collection-failure counter,
 * and inventing one from an absence of buckets would report a fault we have
 * not observed.
 */
export function collectionHealth(
  buckets: MetricBucket[],
  lastBucketAt: string | null,
  /** Seconds the chart covers, so absent intervals can be counted. */
  windowSeconds: number,
  now: number = Date.now(),
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): CollectionHealth {
  const total = buckets.length;
  const incomplete = buckets.filter((b) => b.completeness !== "COMPLETE").length;

  // An interval with no stored bucket is as uncollected as one stored with
  // PARTIAL completeness, and the chart shades both. Counting only the rows
  // that exist would report "115 of 115 fully observed" over a window the
  // chart draws as mostly gaps — two true statements that mislead together.
  const width = buckets[buckets.length - 1]?.bucket_seconds ?? 60;
  const expected = width > 0 ? Math.round(windowSeconds / width) : total;
  const absent = Math.max(0, expected - total);

  const lastAt = lastBucketAt ? Date.parse(lastBucketAt) : null;
  const lagSeconds =
    lastAt != null && !Number.isNaN(lastAt) ? Math.max(0, (now - lastAt) / 1000) : null;

  if (total === 0 && lagSeconds == null) {
    return {
      status: "NONE",
      label: i18n.t("behavior.collection.label.NONE"),
      tone: "crit",
      explanation: i18n.t("behavior.collection.explanation.none"),
      incomplete,
      total,
      expected,
      absent,
      lagSeconds,
    };
  }

  if (lagSeconds != null && lagSeconds > width * DELAY_FACTOR) {
    return {
      status: "DELAYED",
      label: i18n.t("behavior.collection.label.DELAYED"),
      tone: "warn",
      explanation: i18n.t("behavior.collection.explanation.delayed", {
        minutes: plainCount(Math.round(lagSeconds / 60), i18n.locale),
        factor: DELAY_FACTOR,
      }),
      incomplete,
      total,
      expected,
      absent,
      lagSeconds,
    };
  }

  const uncollected = incomplete + absent;
  if (uncollected > 0) {
    const bits: string[] = [];
    const count = (n: number) => ({ count: n, shown: plainCount(n, i18n.locale) });
    if (absent > 0) bits.push(i18n.t("behavior.collection.explanation.absent", count(absent)));
    if (incomplete > 0) {
      bits.push(i18n.t("behavior.collection.explanation.incomplete", count(incomplete)));
    }
    return {
      status: "PARTIAL",
      label: i18n.t("behavior.collection.label.PARTIAL"),
      tone: "warn",
      explanation: i18n.t("behavior.collection.explanation.partial", {
        expected: plainCount(expected, i18n.locale),
        bits: bits.join(i18n.t("behavior.collection.explanation.and")),
      }),
      incomplete,
      total,
      expected,
      absent,
      lagSeconds,
    };
  }

  return {
    status: "CURRENT",
    label: i18n.t("behavior.collection.label.CURRENT"),
    tone: "ok",
    explanation: i18n.t("behavior.collection.explanation.current", {
      total: plainCount(total, i18n.locale),
    }),
    incomplete,
    total,
    expected,
    absent,
    lagSeconds,
  };
}

// --- anomaly partitioning --------------------------------------------------

/**
 * States that mean an incident is still live.
 *
 * Derived from the lifecycle state, never from `resolved_at IS NULL`. A NORMAL
 * row with no resolved timestamp is not an active incident, and treating the
 * absence of a timestamp as evidence of activity is the exact defect the
 * backend fixed in ec5d352.
 */
export const ACTIVE_STATES = ["CANDIDATE", "OPEN", "RECOVERING"] as const;

export function isActiveAnomaly(anomaly: AnomalySummary): boolean {
  return (ACTIVE_STATES as readonly string[]).includes(anomaly.state);
}

const ACTIVE_ORDER: Record<string, number> = {
  OPEN: 0,
  CANDIDATE: 1,
  RECOVERING: 2,
};

export function partitionAnomalies(anomalies: AnomalySummary[]): {
  active: AnomalySummary[];
  recent: AnomalySummary[];
} {
  const active = anomalies
    .filter(isActiveAnomaly)
    .sort(
      (a, b) =>
        (ACTIVE_ORDER[a.state] ?? 9) - (ACTIVE_ORDER[b.state] ?? 9) ||
        Date.parse(b.anomaly_start ?? b.detected_at) -
          Date.parse(a.anomaly_start ?? a.detected_at),
    );

  const recent = anomalies
    .filter((a) => !isActiveAnomaly(a))
    .sort(
      (a, b) =>
        Date.parse(b.resolved_at ?? b.anomaly_end ?? b.detected_at) -
        Date.parse(a.resolved_at ?? a.anomaly_end ?? a.detected_at),
    );

  return { active, recent };
}

// --- chart summary ---------------------------------------------------------

/**
 * The source timeline as prose, for readers who cannot see the chart.
 *
 * States the range covered, the current and expected values, the extremes, and
 * — critically — how much of the window was not collected, which is the fact a
 * sighted reader takes from the shaded gaps.
 */
export function summarizeSourceTimeline(
  {
    hours,
    observed,
    currentEps,
    expectedEps,
    missing,
    partial,
    anomalyCount,
    shownAnomalyCount = anomalyCount,
  }: {
    hours: number;
    observed: (number | null)[];
    currentEps: number | null;
    expectedEps: number | null;
    missing: number;
    partial: number;
    anomalyCount: number | null;
    shownAnomalyCount?: number | null;
  },
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): string {
  const t = (key: string, values?: Record<string, string | number>) =>
    i18n.t(`behavior.timeline.${key}`, values);
  const metric = (value: number) => formatMetric(value, 2, i18n.locale);
  const count = (n: number) => ({ count: n, shown: plainCount(n, i18n.locale) });
  const parts: string[] = [t("over", { range: rangeLabel(hours as RangeHours, i18n) })];

  const measured = observed.filter((v): v is number => v != null);
  if (measured.length === 0) {
    parts.push(t("noneCollected"));
  } else {
    parts.push(
      t("ranged", {
        min: metric(Math.min(...measured)),
        max: metric(Math.max(...measured)),
        ...count(measured.length),
      }),
    );
  }

  parts.push(
    currentEps != null
      ? t("current", { value: metric(currentEps) })
      : t("currentNone"),
  );

  parts.push(
    expectedEps != null
      ? t("expected", { value: metric(expectedEps) })
      : t("expectedNone"),
  );

  if (missing > 0 || partial > 0) {
    const bits: string[] = [];
    if (missing > 0) bits.push(t("missing", count(missing)));
    if (partial > 0) bits.push(t("partial", count(partial)));
    parts.push(t("gaps", { bits: bits.join(t("and")) }));
  }

  if (anomalyCount == null) {
    parts.push(t("anomaliesUnknown"));
  } else if (shownAnomalyCount != null && shownAnomalyCount < anomalyCount) {
    parts.push(
      t("anomaliesShowing", {
        shown: plainCount(shownAnomalyCount, i18n.locale),
        total: plainCount(anomalyCount, i18n.locale),
      }),
    );
  } else {
    parts.push(anomalyCount === 0 ? t("anomaliesNone") : t("anomaliesSome", count(anomalyCount)));
  }

  return parts.join(" ");
}
