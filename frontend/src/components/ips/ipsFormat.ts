// Number formatting for IPS measurements.
//
// The WAF rules apply unchanged (`waf/wafFormat.ts`): a missing value is an em
// dash and a measured zero is `0`; a lower bound is "≥ n". What IPS adds is a
// unit per detector, so a figure is never printed bare. Every function takes the
// locale (or the `BehaviorI18n` that carries it) last and optional, so a caller
// outside this area keeps today's English figures.

import { NOT_MEASURED } from "@/lib/behavior";
import { fixed, grouped } from "@/lib/figureText";
import { formatPercent, type FormatLocale } from "@/lib/format";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import type { IpsUnit } from "./ipsDetectors";
import { ENGLISH_IPS } from "./ipsI18n";

export { bucketWidth, formatPoints } from "@/components/waf/wafFormat";

/**
 * A count of events or ports: whole numbers stay whole, a fractional median keeps one decimal.
 * The English form is exactly `wafFormat.formatRequests`; Turkish groups and separates by locale.
 */
export function formatEvents(
  value: number | null | undefined,
  measurement?: string | null,
  locale: FormatLocale = "en",
): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const text = Number.isInteger(value)
    ? grouped(value, locale)
    : value.toLocaleString(locale === "tr" ? "tr-TR" : "en-US", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
  return measurement === "LOWER_BOUND" ? `≥ ${text}` : text;
}

/** The unit's words: "events / 5 min", "distinct ports", or "" for a share (the % says it). */
export function unitText(
  unit: IpsUnit | null,
  bucket: string | null,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  if (unit === "events") {
    // The English width is parsed, not matched as text: `bucketWidth` writes a
    // closed vocabulary, and the minutes/hours/seconds pick their own message.
    const m = /^(\d+(?:\.\d+)?) (h|min|s)$/.exec(bucket ?? "");
    if (!m) return i18n.t("ips.unit.perBucket");
    const n = m[1];
    if (m[2] === "min") return i18n.t("ips.unit.perMinutes", { minutes: n });
    if (m[2] === "h") return i18n.t("ips.unit.perHours", { hours: n });
    return i18n.t("ips.unit.perSeconds", { seconds: n });
  }
  if (unit === "ports") return i18n.t("ips.unit.ports");
  if (unit === "since") return i18n.t("ips.unit.since");
  return "";
}

/** One figure in its detector's unit; a missing value or an unknown unit is the dash. */
export function formatFigure(
  unit: IpsUnit | null,
  value: number | null | undefined,
  measurement?: string | null,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  if (unit == null) return NOT_MEASURED;
  return unit === "share"
    ? formatPercent(value, measurement, 1, i18n.locale)
    : formatEvents(value, measurement, i18n.locale);
}

/**
 * A change in percentage points. The API already sends points (`45.0` is 45 pp), unlike the
 * proportions, which are fractions: it is not multiplied by 100 again. Turkish writes the
 * locale's decimal mark and the unit word `puan`.
 */
export function formatPointChange(
  value: number | null | undefined,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  return `${value > 0 ? "+" : ""}${fixed(value, 1, i18n.locale)}\u00A0${i18n.t("ips.points.unit")}`;
}
