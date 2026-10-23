// Number formatting for WAF measurements.
//
// Same rule as `lib/behavior.ts`: a missing value is an em dash and a measured
// zero is `0`. Two additions: a lower bound is "≥ n", never `n`, and a bucket
// width is spoken in minutes because every WAF figure is "per bucket".

import { NOT_MEASURED } from "@/lib/behavior";
import { formatShare } from "@/lib/format";

import { isLowerBound } from "./wafLabels";

/** A request count. Whole numbers stay whole; a fractional median keeps one decimal. */
export function formatRequests(value: number | null | undefined, measurement?: string | null) {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const text = Number.isInteger(value)
    ? value.toLocaleString("en-US")
    : value.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return isLowerBound(measurement) ? `≥ ${text}` : text;
}

/** A fraction in [0,1] as a percentage; a lower bound reads "≥ 12.5 %". */
export function formatPercent(
  value: number | null | undefined,
  measurement?: string | null,
  digits = 1,
) {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  // The shared spelling is one decimal and a space before "%". A non-default
  // precision keeps that space and does not use the tiny-share mark.
  const text =
    digits === 1 ? formatShare(value) : `${(value * 100).toFixed(digits)}\u00A0%`;
  return isLowerBound(measurement) ? `≥ ${text}` : text;
}

/**
 * A change in percentage points, signed so a drop is visibly a drop. The API already sends
 * points (`29.7` is 29.7 pp), unlike the proportions, which are fractions: no `* 100` here.
 */
export function formatPoints(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}\u00A0pp`;
}

/** "5 min" for a 300 s bucket; null when the width is not known. */
export function bucketWidth(seconds: number | null | undefined): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

/** The unit of a per-bucket request count: "requests / 5 min". */
export function requestsUnit(seconds: number | null | undefined): string {
  const width = bucketWidth(seconds);
  return width ? `requests / ${width}` : "requests / bucket";
}

/** The unit of the new-endpoint detector's cumulative count: not a per-bucket rate. */
export const SINCE_UNIT = "requests since it first appeared";
