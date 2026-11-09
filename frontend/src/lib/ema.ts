// Exponential moving averages of an observed series, for display only.
//
// The overlay is computed in the browser from the series a chart already
// drew. It feeds no detector, baseline or verdict. The input is the chart's
// observed line, where a bucket that was not fully observed is a `null`
// (`buildSeries`); the output keeps those nulls, so the average stops where the
// observation stops and never bridges, or zero-fills, an unobserved interval.

/** The periods an analyst can switch on, in buckets. */
export const EMA_PERIODS = [3, 5, 10, 20] as const;
export type EmaPeriod = (typeof EMA_PERIODS)[number];

/**
 * The EMA of `values` over `period` buckets, aligned with the input.
 *
 * `alpha = 2 / (period + 1)`. No value is produced until `period` consecutive
 * observed buckets exist; the first value is their arithmetic mean, placed at
 * the last of them, and each later one is `alpha * x + (1 - alpha) * previous`.
 * A `null` ends the line: the output is `null` there and the warm-up starts
 * again after it. A real zero is a value and is averaged as one.
 */
export function ema(values: ReadonlyArray<number | null>, period: number): Array<number | null> {
  const out: Array<number | null> = new Array(values.length).fill(null);
  if (!Number.isInteger(period) || period < 1) return out;
  const alpha = 2 / (period + 1);
  let run = 0;
  let sum = 0;
  let prev: number | null = null;
  for (let i = 0; i < values.length; i += 1) {
    const x = values[i];
    if (x == null || !Number.isFinite(x)) {
      run = 0;
      sum = 0;
      prev = null;
      continue;
    }
    if (prev == null) {
      run += 1;
      sum += x;
      if (run === period) {
        prev = sum / period;
        out[i] = prev;
      }
    } else {
      prev = alpha * x + (1 - alpha) * prev;
      out[i] = prev;
    }
  }
  return out;
}

/** Whether `ema(values, period)` draws anything at all. */
export function hasEmaRun(values: ReadonlyArray<number | null>, period: number): boolean {
  return ema(values, period).some((v) => v != null);
}

/**
 * The span of one bucket, in seconds, when the series has a single clear
 * bucket length; `null` when it cannot be told (no buckets, or buckets of
 * differing length).
 */
export function bucketSecondsOf(buckets: ReadonlyArray<{ bucket_seconds: number }>): number | null {
  if (buckets.length === 0) return null;
  const first = buckets[0].bucket_seconds;
  if (!(first > 0)) return null;
  return buckets.every((b) => b.bucket_seconds === first) ? first : null;
}

/** "100 min", "3 h", "1 d 2 h": a span of `periods` buckets of `bucketSeconds`. */
export function spanText(periods: number, bucketSeconds: number | null): string {
  if (bucketSeconds == null) return `${periods} buckets`;
  const seconds = periods * bucketSeconds;
  if (seconds < 120) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 120 || minutes % 60 !== 0) {
    if (minutes < 120) return `${minutes} min`;
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return `${h} h ${m} min`;
  }
  const hours = minutes / 60;
  if (hours >= 48 && hours % 24 === 0) return `${hours / 24} d`;
  return `${hours} h`;
}
