// The words of the result trend (`ResultTrend`) in the interface language.
//
// The trend sits on the search page, which is not converted yet, and in the saved-search widget on
// the Dashboard, which is. A caller that passes no `TrendI18n` gets English from this file's own
// small catalog (the `trend` and `units` namespaces only), so the search page is unchanged.

import enTrend from "../../messages/en/trend.json";
import enUnits from "../../messages/en/units.json";
import { createTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import { formatCount, type FormatLocale } from "@/lib/format";

export interface TrendI18n {
  /** A translator over the whole catalog (full keys: `trend.stale.before`). */
  t: TextTranslator;
  locale: FormatLocale;
}

export const ENGLISH_TREND: TrendI18n = {
  t: createTextTranslator("en", { trend: enTrend, units: enUnits }),
  locale: "en",
};

/**
 * A count the way the trend printed it before: plain digits in English, and the grouping of the
 * locale in Turkish.
 */
export function trendCount(value: number, locale: FormatLocale): string {
  return locale === "en" ? String(value) : formatCount(value, locale);
}
