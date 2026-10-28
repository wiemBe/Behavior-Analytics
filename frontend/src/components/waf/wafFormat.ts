// Number formatting for WAF measurements.
//
// Same rule as `lib/behavior.ts`: a missing value is an em dash and a measured
// zero is `0`. Two additions: a lower bound is "≥ n", never `n`, and a bucket
// width is spoken in minutes because every WAF figure is "per bucket".
//
// Every text-building function takes the interface language last and defaults
// to English (see `wafI18n.ts`).

import { NOT_MEASURED } from "@/lib/behavior";
import { fixed } from "@/lib/behaviorI18n";
import type { FormatLocale } from "@/lib/format";

import { ENGLISH_WAF, type BehaviorI18n } from "./wafI18n";
import { isLowerBound } from "./wafLabels";

/** A request count. Whole numbers stay whole; a fractional median keeps one decimal. */
export function formatRequests(
  value: number | null | undefined,
  measurement?: string | null,
  locale: FormatLocale = "en",
) {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const tag = locale === "tr" ? "tr-TR" : "en-US";
  const text = Number.isInteger(value)
    ? value.toLocaleString(tag)
    : value.toLocaleString(tag, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return isLowerBound(measurement) ? `≥ ${text}` : text;
}

/**
 * A change in percentage points, signed so a drop is visibly a drop. The API already sends
 * points (`29.7` is 29.7 pp), unlike the proportions, which are fractions: no `* 100` here.
 */
export function formatPoints(value: number | null | undefined, i18n: BehaviorI18n = ENGLISH_WAF) {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const sign = value > 0 ? "+" : "";
  // The unit never splits from its number in either language (English "29.7 pp" has U+00A0).
  return `${sign}${fixed(value, 1, i18n.locale)}\u00A0${i18n.t("waf.units.points")}`;
}

/** "5 min" for a 300 s bucket; null when the width is not known. */
export function bucketWidth(
  seconds: number | null | undefined,
  i18n: BehaviorI18n = ENGLISH_WAF,
): string | null {
  if (seconds == null || seconds <= 0) return null;
  // The count goes in as text: a number argument would be grouped by the locale.
  if (seconds % 3600 === 0) return i18n.t("units.hour", { count: String(seconds / 3600) });
  if (seconds % 60 === 0) return i18n.t("units.minute", { count: String(seconds / 60) });
  return i18n.t("units.second", { count: String(seconds) });
}

/** The unit of a per-bucket request count: "requests / 5 min". */
export function requestsUnit(
  seconds: number | null | undefined,
  i18n: BehaviorI18n = ENGLISH_WAF,
): string {
  const width = bucketWidth(seconds, i18n);
  return width
    ? i18n.t("waf.units.requestsPer", { width })
    : i18n.t("waf.units.requestsPerBucket");
}

/** The unit of the new-endpoint detector's cumulative count: not a per-bucket rate. */
export const SINCE_UNIT = "requests since it first appeared";

/** `SINCE_UNIT` in the interface language. */
export function sinceUnit(i18n: BehaviorI18n = ENGLISH_WAF): string {
  return i18n.t("waf.units.since");
}

/** The short unit of a WAF volume figure in the anomaly list: "req / 5 min". */
export function listUnit(i18n: BehaviorI18n = ENGLISH_WAF): string {
  return i18n.t("waf.units.listUnit");
}
