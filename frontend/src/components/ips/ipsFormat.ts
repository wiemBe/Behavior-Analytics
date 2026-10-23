// Number formatting for IPS measurements.
//
// The WAF rules apply unchanged (`waf/wafFormat.ts`): a missing value is an em
// dash and a measured zero is `0`; a lower bound is "≥ n". What IPS adds is a
// unit per detector, so a figure is never printed bare.

import { formatPercent, formatRequests } from "@/components/waf/wafFormat";
import { NOT_MEASURED } from "@/lib/behavior";

import type { IpsUnit } from "./ipsDetectors";

export { bucketWidth, formatPercent, formatPoints } from "@/components/waf/wafFormat";

/** A count of events or ports: whole numbers stay whole, a fractional median keeps one decimal. */
export const formatEvents = formatRequests;

/** The unit's words: "events / 5 min", "distinct ports", or "" for a share (the % says it). */
export function unitText(unit: IpsUnit | null, bucket: string | null): string {
  if (unit === "events") return bucket ? `events / ${bucket}` : "events / bucket";
  if (unit === "ports") return "distinct ports";
  if (unit === "since") return "events since it first appeared";
  return "";
}

/** One figure in its detector's unit; a missing value or an unknown unit is the dash. */
export function formatFigure(
  unit: IpsUnit | null,
  value: number | null | undefined,
  measurement?: string | null,
): string {
  if (unit == null) return NOT_MEASURED;
  return unit === "share" ? formatPercent(value, measurement) : formatEvents(value, measurement);
}


/**
 * A change in percentage points. The API already sends points (`45.0` is 45 pp), unlike the
 * proportions, which are fractions: it is not multiplied by 100 again.
 */
export function formatPointChange(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}\u00A0pp`;
}
