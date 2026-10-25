// The words of the property-watch series chart in the interface language.
//
// The chart sits on the watch pages, which are not converted yet, and in the Dashboard's
// property-watch widget, which is. A caller that passes no `PwI18n` gets English from this file's
// own small catalog (the `pwchart` namespace only), so the watch pages are unchanged.

import enPwchart from "../../../messages/en/pwchart.json";
import { createTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import { formatCount, formatPercent, type FormatLocale } from "@/lib/format";
import { NOT_MEASURED } from "@/lib/behavior";

import type { PlotKind } from "./pwSeries";

export interface PwI18n {
  /** A translator over the whole catalog (full keys: `pwchart.axis.time`). */
  t: TextTranslator;
  locale: FormatLocale;
}

export const ENGLISH_PW: PwI18n = {
  t: createTextTranslator("en", { pwchart: enPwchart }),
  locale: "en",
};

/** The unit of a bucket count, spoken from the bucket width: `events per 5 min`. */
export function pwUnit(x: PwI18n, bucketSeconds: number | null | undefined): string {
  if (!bucketSeconds || bucketSeconds <= 0) return x.t("pwchart.unit.perBucket");
  const minutes = bucketSeconds / 60;
  return Number.isInteger(minutes)
    ? x.t("pwchart.unit.perMinutes", { minutes })
    : x.t("pwchart.unit.perSeconds", { seconds: bucketSeconds });
}

/** A measurement as the words the tooltip uses. */
export function pwKind(x: PwI18n, kind: PlotKind): string {
  return x.t(`pwchart.kind.${kind}`);
}

/** `12`, or `at least 12` for a lower bound; a missing count is the dash, never `0`. */
export function pwEvents(
  x: PwI18n,
  events: number | null | undefined,
  measurement?: string | null,
): string {
  if (events == null || Number.isNaN(events)) return NOT_MEASURED;
  const text = formatCount(events, x.locale);
  return measurement === "LOWER_BOUND" ? x.t("pwchart.atLeast", { value: text }) : text;
}

/** A coverage share to whole percent. */
export function pwPercent(x: PwI18n, ratio: number | null | undefined): string {
  return formatPercent(ratio, null, 0, x.locale);
}

/** A one-decimal figure in the locale's decimal mark. */
export function pwDecimal(x: PwI18n, value: number): string {
  return value.toLocaleString(x.locale === "tr" ? "tr-TR" : "en-US", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    useGrouping: false,
  });
}
